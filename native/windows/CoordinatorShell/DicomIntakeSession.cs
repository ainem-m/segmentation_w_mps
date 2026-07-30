using System.ComponentModel;
using System.IO;
using System.Text;
using System.Text.Json;
using TotalSegmentatorWrapper.Windows.ProcessSupervisor;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed class DicomIntakeSession : IDisposable
{
    private const string AuditSchema =
        "totalsegmentator_wrapper_mac.dicom_normalizer.audit.v1";
    private const string ConvertSchema =
        "totalsegmentator_wrapper_mac.dicom_normalizer.convert_clean.v1";
    private const uint CancelExitCode = 1223;
    private const uint TimeoutExitCode = 124;
    private static readonly TimeSpan AuditTimeout =
        TimeSpan.FromSeconds(120);
    private static readonly TimeSpan ConvertTimeout =
        TimeSpan.FromSeconds(900);
    private static readonly TimeSpan TerminationWait =
        TimeSpan.FromSeconds(10);

    private readonly ShellConfiguration _configuration;
    private readonly object _gate = new();
    private JobProcess? _activeProcess;
    private TerminationReason _terminationReason;
    private bool _operationActive;
    private bool _cancelRequested;
    private bool _disposed;

    internal DicomIntakeSession(ShellConfiguration configuration)
    {
        _configuration = configuration;
    }

    internal async Task<DicomAuditResult> AuditAsync(string dicomFolder)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!BeginOperation())
        {
            return DicomAuditResult.Failure(
                "dicom_intake_busy",
                "別のDICOM読み込み処理が実行中です。");
        }

        string? operationId = null;
        string? workspaceDirectory = null;
        try
        {
            var runtime = _configuration.CheckDicomRuntime();
            if (!runtime.Passed)
            {
                return DicomAuditResult.Failure(
                    runtime.ErrorCode ?? "dicom_runtime_unavailable",
                    runtime.Message);
            }

            string sourceDirectory;
            try
            {
                sourceDirectory = Path.GetFullPath(dicomFolder);
            }
            catch (Exception exception) when (
                exception is ArgumentException
                    or NotSupportedException
                    or PathTooLongException)
            {
                return DicomAuditResult.Failure(
                    "dicom_input_unavailable",
                    "選択したDICOMフォルダーを確認できません。");
            }
            if (!Directory.Exists(sourceDirectory))
            {
                return DicomAuditResult.Failure(
                    "dicom_input_unavailable",
                    "選択したDICOMフォルダーを確認できません。");
            }

            operationId = Guid.NewGuid().ToString("D");
            try
            {
                workspaceDirectory = CreateWorkspace(operationId);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or ArgumentException)
            {
                return DicomAuditResult.Failure(
                    "dicom_workspace_unavailable",
                    "DICOM読み込み用の作業領域を準備できません。",
                    operationId);
            }

            var auditPath = Path.Combine(workspaceDirectory, "audit.json");
            var execution = await RunJobAsync(
                _configuration.DicomNormalizerPath,
                new[]
                {
                    "audit",
                    "--dicom-dir",
                    sourceDirectory,
                    "--output",
                    auditPath,
                },
                AuditTimeout);
            if (!execution.Started)
            {
                return DicomAuditResult.Failure(
                    "dicom_normalizer_start_failed",
                    "DICOM読み込み機能を開始できません。",
                    operationId,
                    workspaceDirectory);
            }
            if (execution.Cancelled)
            {
                return DicomAuditResult.Failure(
                    "dicom_audit_cancelled",
                    "DICOMの確認を停止しました。",
                    operationId,
                    workspaceDirectory);
            }
            if (execution.TimedOut)
            {
                return DicomAuditResult.Failure(
                    "dicom_audit_timeout",
                    "DICOMの確認が制限時間を超えました。",
                    operationId,
                    workspaceDirectory);
            }
            if (!execution.JobBecameEmpty)
            {
                return DicomAuditResult.Failure(
                    "dicom_audit_process_tree_error",
                    "DICOMの確認処理を安全に終了できませんでした。",
                    operationId,
                    workspaceDirectory);
            }
            if (execution.ExitCode != 0)
            {
                return DicomAuditResult.Failure(
                    "dicom_audit_failed",
                    "DICOMの内容を確認できませんでした。",
                    operationId,
                    workspaceDirectory);
            }

            IReadOnlyList<DicomCleanCandidate> candidates;
            try
            {
                candidates = ParseAudit(
                    auditPath,
                    operationId);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or JsonException
                    or InvalidDataException)
            {
                return DicomAuditResult.Failure(
                    "dicom_audit_invalid",
                    "DICOM確認結果を検証できませんでした。",
                    operationId,
                    workspaceDirectory);
            }
            if (candidates.Count == 0)
            {
                return DicomAuditResult.Failure(
                    "dicom_clean_series_unavailable",
                    "3D作成に使用できるCTシリーズが見つかりませんでした。",
                    operationId,
                    workspaceDirectory);
            }

            return DicomAuditResult.Success(
                operationId,
                workspaceDirectory,
                sourceDirectory,
                auditPath,
                candidates);
        }
        finally
        {
            EndOperation();
        }
    }

    internal async Task<DicomConversionResult> ConvertCleanAsync(
        DicomAuditResult audit,
        DicomCleanCandidate candidate,
        bool userSelected)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!BeginOperation())
        {
            return DicomConversionResult.Failure(
                "dicom_intake_busy",
                "別のDICOM読み込み処理が実行中です。");
        }

        try
        {
            if (!audit.Succeeded
                || string.IsNullOrWhiteSpace(audit.OperationId)
                || string.IsNullOrWhiteSpace(audit.WorkspaceDirectory)
                || string.IsNullOrWhiteSpace(audit.SourceDirectory)
                || !Directory.Exists(audit.WorkspaceDirectory)
                || !Directory.Exists(audit.SourceDirectory))
            {
                return DicomConversionResult.Failure(
                    "dicom_audit_invalid",
                    "DICOM確認結果を使用できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            var knownCandidate = audit.Candidates.SingleOrDefault(
                item => item.CandidateId == candidate.CandidateId);
            if (knownCandidate is null
                || knownCandidate.AuditOperationId != audit.OperationId
                || knownCandidate.Classification
                    != "original_ct_geometry_ok"
                || knownCandidate.FileCount <= 0
                || (!userSelected
                    && knownCandidate.CandidateId
                        != audit.Candidates[0].CandidateId))
            {
                return DicomConversionResult.Failure(
                    "dicom_candidate_invalid",
                    "選択したCTシリーズを使用できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var runtime = _configuration.CheckDicomRuntime();
            if (!runtime.Passed)
            {
                return DicomConversionResult.Failure(
                    runtime.ErrorCode ?? "dicom_runtime_unavailable",
                    runtime.Message,
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var conversionDirectory = Path.Combine(
                audit.WorkspaceDirectory,
                $"conversion-{Guid.NewGuid():N}");
            try
            {
                Directory.CreateDirectory(conversionDirectory);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or ArgumentException)
            {
                return DicomConversionResult.Failure(
                    "dicom_workspace_unavailable",
                    "DICOM変換用の作業領域を準備できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var convertArguments = new List<string>
            {
                "convert-clean",
                "--dicom-dir",
                audit.SourceDirectory,
                "--series-key",
                knownCandidate.NativeSeriesKey,
            };
            convertArguments.Add("--output");
            convertArguments.Add(conversionDirectory);
            convertArguments.Add("--dcm2niix");
            convertArguments.Add(_configuration.Dcm2niixPath);
            convertArguments.Add("--dcm2niix-timeout-seconds");
            convertArguments.Add(
                ((int)ConvertTimeout.TotalSeconds).ToString(
                    System.Globalization.CultureInfo.InvariantCulture));
            var execution = await RunJobAsync(
                _configuration.DicomNormalizerPath,
                convertArguments,
                ConvertTimeout);
            if (!execution.Started)
            {
                return DicomConversionResult.Failure(
                    "dicom_normalizer_start_failed",
                    "DICOM変換機能を開始できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.Cancelled)
            {
                return DicomConversionResult.Failure(
                    "dicom_conversion_cancelled",
                    "DICOMの変換を停止しました。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.TimedOut)
            {
                return DicomConversionResult.Failure(
                    "dicom_conversion_timeout",
                    "DICOMの変換が制限時間を超えました。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (!execution.JobBecameEmpty)
            {
                return DicomConversionResult.Failure(
                    "dicom_conversion_process_tree_error",
                    "DICOMの変換処理を安全に終了できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.ExitCode != 0)
            {
                var metadataPath = Path.Combine(
                    conversionDirectory,
                    "convert_clean_metadata.json");
                if (Dcm2niixTimedOut(metadataPath))
                {
                    return DicomConversionResult.Failure(
                        "dicom_conversion_timeout",
                        "DICOMの変換が制限時間を超えました。",
                        audit.OperationId,
                        audit.WorkspaceDirectory);
                }
                return DicomConversionResult.Failure(
                    "dicom_conversion_failed",
                    "DICOMを3D作成用データへ変換できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            VerifiedConversion verified;
            try
            {
                verified = VerifyConversion(
                    conversionDirectory,
                    knownCandidate);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or JsonException
                    or InvalidDataException)
            {
                return DicomConversionResult.Failure(
                    "dicom_normalized_nifti_invalid",
                    "変換後の3D作成用データを検証できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var selectionBasis = userSelected
                ? "user_selected"
                : "first_geometry_ok";
            var manifestPath = Path.Combine(
                audit.WorkspaceDirectory,
                "dicom-intake-manifest.json");
            try
            {
                await WriteManifestAsync(
                    manifestPath,
                    audit.OperationId,
                    knownCandidate,
                    selectionBasis,
                    execution.ExitCode!.Value,
                    verified.Dcm2niixExitCode);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or ArgumentException)
            {
                return DicomConversionResult.Failure(
                    "dicom_intake_manifest_failed",
                    "DICOM読み込み記録を保存できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            return DicomConversionResult.Success(
                audit.OperationId,
                audit.WorkspaceDirectory,
                verified.NiftiPath,
                manifestPath,
                selectionBasis);
        }
        finally
        {
            EndOperation();
        }
    }

    internal void RequestCancel()
    {
        lock (_gate)
        {
            if (!_operationActive)
            {
                return;
            }
            _cancelRequested = true;
            if (_activeProcess is null
                || _terminationReason != TerminationReason.None
                || _activeProcess.ExitCode is not null)
            {
                return;
            }
            try
            {
                _activeProcess.Terminate(CancelExitCode);
                _terminationReason = TerminationReason.Cancelled;
            }
            catch (Win32Exception)
            {
                // The operation reports a safe process-tree failure if it
                // cannot confirm termination.
            }
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        RequestCancel();
    }

    private bool BeginOperation()
    {
        lock (_gate)
        {
            if (_operationActive)
            {
                return false;
            }
            _operationActive = true;
            _cancelRequested = false;
            _terminationReason = TerminationReason.None;
            return true;
        }
    }

    private void EndOperation()
    {
        lock (_gate)
        {
            _activeProcess = null;
            _operationActive = false;
            _cancelRequested = false;
            _terminationReason = TerminationReason.None;
        }
    }

    private string CreateWorkspace(string operationId)
    {
        var intakeRoot = Path.GetFullPath(
            Path.Combine(_configuration.OutputRoot, ".dicom-intake"));
        Directory.CreateDirectory(intakeRoot);
        var workspace = Path.GetFullPath(
            Path.Combine(intakeRoot, operationId));
        if (!IsPathWithin(workspace, intakeRoot))
        {
            throw new InvalidDataException(
                "The DICOM workspace escaped its private root.");
        }
        Directory.CreateDirectory(workspace);
        return workspace;
    }

    private async Task<JobExecutionResult> RunJobAsync(
        string executable,
        IReadOnlyList<string> arguments,
        TimeSpan timeout)
    {
        JobProcess process;
        try
        {
            process = JobProcess.StartSuspended(
                executable,
                arguments,
                Path.GetDirectoryName(executable)
                    ?? AppContext.BaseDirectory);
        }
        catch (Exception exception) when (
            exception is Win32Exception
                or IOException
                or UnauthorizedAccessException
                or ArgumentException
                or InvalidOperationException)
        {
            return JobExecutionResult.NotStarted;
        }

        using (process)
        {
            var stdoutTask = DrainAsync(process.StandardOutput);
            var stderrTask = DrainAsync(process.StandardError);
            try
            {
                lock (_gate)
                {
                    _activeProcess = process;
                    if (_cancelRequested)
                    {
                        process.Terminate(CancelExitCode);
                        _terminationReason =
                            TerminationReason.Cancelled;
                    }
                    else
                    {
                        process.Resume();
                    }
                }
            }
            catch (Exception exception) when (
                exception is Win32Exception
                    or InvalidOperationException)
            {
                TryTerminate(process, CancelExitCode);
                process.WaitForExit(TerminationWait);
                await Task.WhenAll(stdoutTask, stderrTask);
                return JobExecutionResult.NotStarted;
            }

            var exited = await Task.Run(
                () => process.WaitForExit(timeout));
            if (!exited)
            {
                lock (_gate)
                {
                    if (_terminationReason == TerminationReason.None)
                    {
                        _terminationReason =
                            TerminationReason.TimedOut;
                    }
                    TryTerminate(process, TimeoutExitCode);
                }
                process.WaitForExit(TerminationWait);
            }

            var jobBecameEmpty = await WaitForJobEmptyAsync(
                process,
                TerminationWait);
            var jobBecameEmptyWithoutForcedCleanup =
                jobBecameEmpty;
            if (!jobBecameEmpty)
            {
                lock (_gate)
                {
                    TryTerminate(process, TimeoutExitCode);
                }
                process.WaitForExit(TerminationWait);
                jobBecameEmpty = await WaitForJobEmptyAsync(
                    process,
                    TerminationWait);
            }
            await Task.WhenAll(stdoutTask, stderrTask);

            TerminationReason terminationReason;
            lock (_gate)
            {
                terminationReason = _terminationReason;
                if (ReferenceEquals(_activeProcess, process))
                {
                    _activeProcess = null;
                }
            }
            return new JobExecutionResult(
                true,
                process.ExitCode,
                terminationReason == TerminationReason.Cancelled,
                terminationReason == TerminationReason.TimedOut,
                jobBecameEmptyWithoutForcedCleanup);
        }
    }

    private static async Task DrainAsync(StreamReader reader)
    {
        var buffer = new char[4096];
        try
        {
            while (await reader.ReadAsync(buffer.AsMemory()) != 0)
            {
                // The native tool can include paths, identifiers, and
                // third-party output. Drain it to avoid deadlock, but never
                // retain or expose it.
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or ObjectDisposedException)
        {
            // Job/process state determines the typed result.
        }
    }

    private static async Task<bool> WaitForJobEmptyAsync(
        JobProcess process,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        do
        {
            try
            {
                if (process.ActiveProcessIds().Count == 0)
                {
                    return true;
                }
            }
            catch (Win32Exception)
            {
                return false;
            }
            await Task.Delay(TimeSpan.FromMilliseconds(100));
        }
        while (DateTime.UtcNow < deadline);
        return false;
    }

    private static void TryTerminate(
        JobProcess process,
        uint exitCode)
    {
        try
        {
            process.Terminate(exitCode);
        }
        catch (Win32Exception)
        {
            // The caller verifies that the root and Job are empty.
        }
    }

    private static IReadOnlyList<DicomCleanCandidate> ParseAudit(
        string auditPath,
        string operationId)
    {
        using var document = JsonDocument.Parse(
            File.ReadAllText(auditPath));
        var root = document.RootElement;
        RequireString(root, "schema", AuditSchema);
        var boundary = RequireObject(root, "product_boundary");
        RequireString(
            boundary,
            "phase",
            "gdcm_robust_intake");
        RequireFalse(boundary, "volume_written");
        RequireFalse(
            boundary,
            "secondary_capture_rescue_written");

        var series = RequireArray(root, "series");
        var result = new List<DicomCleanCandidate>();
        foreach (var item in series.EnumerateArray())
        {
            var classification = RequireObject(
                item,
                "classification");
            if (!classification.TryGetProperty(
                    "status",
                    out var status)
                || status.ValueKind != JsonValueKind.String)
            {
                throw new InvalidDataException(
                    "A DICOM classification status is invalid.");
            }
            if (!classification.TryGetProperty(
                    "next_action",
                    out var nextAction)
                || nextAction.ValueKind != JsonValueKind.String)
            {
                throw new InvalidDataException(
                    "A DICOM classification next action is invalid.");
            }
            if (!classification.TryGetProperty(
                    "requires_external_tool",
                    out var requiresExternalTool)
                || requiresExternalTool.ValueKind is not (
                    JsonValueKind.True
                    or JsonValueKind.False))
            {
                throw new InvalidDataException(
                    "A DICOM external-tool classification is invalid.");
            }
            if (status.GetString() != "original_ct_geometry_ok"
                || nextAction.GetString() != "convert_clean"
                || requiresExternalTool.GetBoolean())
            {
                continue;
            }

            var seriesKey = RequireNonEmptyString(
                item,
                "series_key");
            var seriesDescription = OptionalString(
                item,
                "series_description");
            var fileCount = RequirePositiveInt32(
                item,
                "file_count");
            int? seriesNumber = null;
            if (item.TryGetProperty(
                    "series_number",
                    out var seriesNumberElement)
                && seriesNumberElement.ValueKind
                    != JsonValueKind.Null)
            {
                if (!seriesNumberElement.TryGetInt32(
                        out var parsedSeriesNumber))
                {
                    throw new InvalidDataException(
                        "A DICOM series number is invalid.");
                }
                seriesNumber = parsedSeriesNumber;
            }

            result.Add(
                new DicomCleanCandidate(
                    $"clean-{result.Count + 1}",
                    operationId,
                    seriesKey,
                    seriesNumber,
                    seriesDescription,
                    fileCount,
                    "original_ct_geometry_ok"));
        }
        return result;
    }

    private static VerifiedConversion VerifyConversion(
        string conversionDirectory,
        DicomCleanCandidate candidate)
    {
        var metadataPath = Path.Combine(
            conversionDirectory,
            "convert_clean_metadata.json");
        using var document = JsonDocument.Parse(
            File.ReadAllText(metadataPath));
        var root = document.RootElement;
        RequireString(root, "schema", ConvertSchema);
        RequireString(root, "status", "success");

        var selected = RequireObject(root, "selected_series");
        RequireString(
            selected,
            "classification",
            "original_ct_geometry_ok");
        RequireString(
            selected,
            "series_instance_uid",
            candidate.NativeSeriesKey);
        if (RequirePositiveInt32(selected, "file_count")
            != candidate.FileCount)
        {
            throw new InvalidDataException(
                "The converted series file count changed.");
        }
        if (candidate.SeriesNumber.HasValue)
        {
            if (!selected.TryGetProperty(
                    "series_number",
                    out var selectedSeriesNumber)
                || !selectedSeriesNumber.TryGetInt32(
                    out var parsedSeriesNumber)
                || parsedSeriesNumber != candidate.SeriesNumber.Value)
            {
                throw new InvalidDataException(
                    "The converted series number changed.");
            }
        }
        else if (selected.TryGetProperty(
                     "series_number",
                     out var unexpectedSeriesNumber)
                 && unexpectedSeriesNumber.ValueKind
                     != JsonValueKind.Null)
        {
            throw new InvalidDataException(
                "The converted series number changed.");
        }

        var dcm2niix = RequireObject(root, "dcm2niix");
        var dcm2niixExitCode = RequireInt32(
            dcm2niix,
            "returncode");
        if (dcm2niixExitCode != 0)
        {
            throw new InvalidDataException(
                "dcm2niix did not succeed.");
        }
        var boundary = RequireObject(root, "product_boundary");
        RequireFalse(boundary, "segmentation_started");
        RequireFalse(boundary, "secondary_capture_rescue");

        var dcm2niixDirectory = Path.GetFullPath(
            Path.Combine(conversionDirectory, "dcm2niix"));
        if (!Directory.Exists(dcm2niixDirectory)
            || !IsPathWithin(
                dcm2niixDirectory,
                Path.GetFullPath(conversionDirectory)))
        {
            throw new InvalidDataException(
                "The dcm2niix output directory is invalid.");
        }
        var niftiFiles = Directory.EnumerateFiles(
                dcm2niixDirectory,
                "*",
                SearchOption.AllDirectories)
            .Where(IsNifti)
            .Select(Path.GetFullPath)
            .ToArray();
        if (niftiFiles.Length != 1
            || new FileInfo(niftiFiles[0]).Length <= 0
            || !IsPathWithin(niftiFiles[0], dcm2niixDirectory))
        {
            throw new InvalidDataException(
                "The normalized NIfTI output is invalid.");
        }

        var outputs = RequireObject(root, "outputs");
        var metadataDcm2niixDirectory = Path.GetFullPath(
            RequireNonEmptyString(outputs, "dcm2niix_dir"));
        var metadataNifti = Path.GetFullPath(
            RequireNonEmptyString(outputs, "nifti"));
        if (!PathEquals(
                metadataDcm2niixDirectory,
                dcm2niixDirectory)
            || !PathEquals(metadataNifti, niftiFiles[0])
            || !IsPathWithin(
                metadataNifti,
                dcm2niixDirectory))
        {
            throw new InvalidDataException(
                "The normalized NIfTI provenance is invalid.");
        }

        return new VerifiedConversion(
            niftiFiles[0],
            dcm2niixExitCode);
    }

    private static bool Dcm2niixTimedOut(string metadataPath)
    {
        try
        {
            using var document = JsonDocument.Parse(
                File.ReadAllText(metadataPath));
            var root = document.RootElement;
            RequireString(root, "schema", ConvertSchema);
            var dcm2niix = RequireObject(root, "dcm2niix");
            return RequireInt32(dcm2niix, "returncode")
                == (int)TimeoutExitCode;
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or JsonException
                or InvalidDataException)
        {
            return false;
        }
    }

    private static async Task WriteManifestAsync(
        string manifestPath,
        string operationId,
        DicomCleanCandidate candidate,
        string selectionBasis,
        uint normalizerExitCode,
        int dcm2niixExitCode)
    {
        var payload = new
        {
            schema =
                "totalsegmentator_wrapper.windows_dicom_intake.v1",
            status = "success",
            operation_id = operationId,
            source_kind = "dicom",
            selected_series = new
            {
                series_number = candidate.SeriesNumber,
                file_count = candidate.FileCount,
                classification = candidate.Classification,
            },
            selection_basis = selectionBasis,
            normalizer = new
            {
                exit_code = normalizerExitCode,
                status = "success",
            },
            dcm2niix = new
            {
                exit_code = dcm2niixExitCode,
                status = "success",
            },
            segmentation_started = false,
            raw_output_recorded = false,
        };
        await File.WriteAllTextAsync(
            manifestPath,
            JsonSerializer.Serialize(
                payload,
                new JsonSerializerOptions { WriteIndented = true })
            + Environment.NewLine,
            new UTF8Encoding(false));
    }

    private static JsonElement RequireObject(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException(
                $"The {propertyName} object is invalid.");
        }
        return value;
    }

    private static JsonElement RequireArray(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException(
                $"The {propertyName} array is invalid.");
        }
        return value;
    }

    private static string RequireNonEmptyString(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind != JsonValueKind.String
            || string.IsNullOrWhiteSpace(value.GetString()))
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return value.GetString()!;
    }

    private static void RequireString(
        JsonElement parent,
        string propertyName,
        string expected)
    {
        if (RequireNonEmptyString(parent, propertyName)
            != expected)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is unexpected.");
        }
    }

    private static int RequirePositiveInt32(
        JsonElement parent,
        string propertyName)
    {
        var value = RequireInt32(parent, propertyName);
        if (value <= 0)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return value;
    }

    private static string OptionalString(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind == JsonValueKind.Null)
        {
            return string.Empty;
        }
        if (value.ValueKind != JsonValueKind.String)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return value.GetString() ?? string.Empty;
    }

    private static int RequireInt32(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || !value.TryGetInt32(out var parsed))
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return parsed;
    }

    private static void RequireFalse(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind != JsonValueKind.False)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
    }

    private static bool IsNifti(string path)
    {
        return path.EndsWith(
                ".nii",
                StringComparison.OrdinalIgnoreCase)
            || path.EndsWith(
                ".nii.gz",
                StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsPathWithin(
        string path,
        string root)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative != ".."
            && !relative.StartsWith(
                $"..{Path.DirectorySeparatorChar}",
                StringComparison.Ordinal)
            && !Path.IsPathRooted(relative);
    }

    private static bool PathEquals(
        string left,
        string right)
    {
        return string.Equals(
            Path.GetFullPath(left).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar),
            Path.GetFullPath(right).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar),
            StringComparison.OrdinalIgnoreCase);
    }

    private enum TerminationReason
    {
        None,
        Cancelled,
        TimedOut,
    }

    private sealed record JobExecutionResult(
        bool Started,
        uint? ExitCode,
        bool Cancelled,
        bool TimedOut,
        bool JobBecameEmpty)
    {
        internal static JobExecutionResult NotStarted { get; } =
            new(false, null, false, false, true);
    }

    private sealed record VerifiedConversion(
        string NiftiPath,
        int Dcm2niixExitCode);
}

internal sealed record DicomCleanCandidate(
    string CandidateId,
    string AuditOperationId,
    string NativeSeriesKey,
    int? SeriesNumber,
    string SeriesDescription,
    int FileCount,
    string Classification)
{
    public string DisplayTitle
    {
        get
        {
            var number = SeriesNumber?.ToString(
                System.Globalization.CultureInfo.InvariantCulture)
                ?? "番号なし";
            var description = string.IsNullOrWhiteSpace(
                SeriesDescription)
                ? "名称なし"
                : SeriesDescription;
            return $"撮影 {number}: {description}";
        }
    }

    public string DisplayDetail => $"{FileCount}枚";
}

internal sealed record DicomAuditResult(
    bool Succeeded,
    string? OperationId,
    string? WorkspaceDirectory,
    string? SourceDirectory,
    string? AuditPath,
    IReadOnlyList<DicomCleanCandidate> Candidates,
    string? ErrorCode,
    string? SafeMessage)
{
    internal static DicomAuditResult Success(
        string operationId,
        string workspaceDirectory,
        string sourceDirectory,
        string auditPath,
        IReadOnlyList<DicomCleanCandidate> candidates)
    {
        return new DicomAuditResult(
            true,
            operationId,
            workspaceDirectory,
            sourceDirectory,
            auditPath,
            candidates,
            null,
            null);
    }

    internal static DicomAuditResult Failure(
        string errorCode,
        string safeMessage,
        string? operationId = null,
        string? workspaceDirectory = null)
    {
        return new DicomAuditResult(
            false,
            operationId,
            workspaceDirectory,
            null,
            null,
            Array.Empty<DicomCleanCandidate>(),
            errorCode,
            safeMessage);
    }
}

internal sealed record DicomConversionResult(
    bool Succeeded,
    string? OperationId,
    string? WorkspaceDirectory,
    string? NiftiPath,
    string? ManifestPath,
    string? SelectionBasis,
    string? ErrorCode,
    string? SafeMessage)
{
    internal static DicomConversionResult Success(
        string operationId,
        string workspaceDirectory,
        string niftiPath,
        string manifestPath,
        string selectionBasis)
    {
        return new DicomConversionResult(
            true,
            operationId,
            workspaceDirectory,
            niftiPath,
            manifestPath,
            selectionBasis,
            null,
            null);
    }

    internal static DicomConversionResult Failure(
        string errorCode,
        string safeMessage,
        string? operationId = null,
        string? workspaceDirectory = null)
    {
        return new DicomConversionResult(
            false,
            operationId,
            workspaceDirectory,
            null,
            null,
            null,
            errorCode,
            safeMessage);
    }
}
