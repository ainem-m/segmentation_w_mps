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
    private const string RescueSchema =
        "totalsegmentator_wrapper_mac.dicom_normalizer.rescue.v1";
    private const string RescueValidationSchema =
        "totalsegmentator_wrapper_mac.dicom_normalizer.rescue_validation.v1";
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

            ParsedDicomAudit parsed;
            try
            {
                parsed = ParseAudit(
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
            if (parsed.CleanCandidates.Count == 0
                && parsed.RescueCandidates.Count == 0)
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
                parsed.CleanCandidates,
                parsed.RescueCandidates);
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

    internal async Task<DicomRescueResult> PrepareRescueAsync(
        DicomAuditResult audit,
        DicomRescueCandidate candidate,
        DicomSpacing spacing)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!BeginOperation())
        {
            return DicomRescueResult.Failure(
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
                return DicomRescueResult.Failure(
                    "dicom_audit_invalid",
                    "DICOM確認結果を使用できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            var knownCandidate = audit.RescueCandidates.SingleOrDefault(
                item => item.CandidateId == candidate.CandidateId);
            if (knownCandidate is null
                || knownCandidate.AuditOperationId != audit.OperationId
                || knownCandidate.Classification
                    != "secondary_capture_rescue_candidate")
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_candidate_invalid",
                    "選択した救済候補を使用できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (!spacing.IsValid)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_spacing_invalid",
                    "X、Y、Zの寸法は0より大きく20以下の数値で入力してください。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var runtime = _configuration.CheckDicomRuntime();
            if (!runtime.Passed)
            {
                return DicomRescueResult.Failure(
                    runtime.ErrorCode ?? "dicom_runtime_unavailable",
                    runtime.Message,
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            var rescueDirectory = Path.Combine(
                audit.WorkspaceDirectory,
                $"rescue-{Guid.NewGuid():N}");
            Directory.CreateDirectory(rescueDirectory);
            var arguments = new List<string>
            {
                "prepare-rescue",
                "--dicom-dir",
                audit.SourceDirectory,
                "--series-key",
                knownCandidate.NativeSeriesKey,
                "--patched-spacing",
                spacing.CommandValue,
                "--output",
                rescueDirectory,
                "--dcm2niix",
                _configuration.Dcm2niixPath,
                "--dcm2niix-timeout-seconds",
                ((int)ConvertTimeout.TotalSeconds).ToString(
                    System.Globalization.CultureInfo.InvariantCulture),
            };
            var execution = await RunJobAsync(
                _configuration.DicomNormalizerPath,
                arguments,
                ConvertTimeout);
            if (!execution.Started)
            {
                return DicomRescueResult.Failure(
                    "dicom_normalizer_start_failed",
                    "DICOM救済機能を開始できません。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.Cancelled)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_cancelled",
                    "確認画像の作成を停止しました。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.TimedOut)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_timeout",
                    "確認画像の作成が制限時間を超えました。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (!execution.JobBecameEmpty)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_process_tree_error",
                    "確認画像の作成処理を安全に終了できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
            if (execution.ExitCode != 0)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_failed",
                    "確認画像を作成できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }

            try
            {
                var verified = VerifyRescue(
                    rescueDirectory,
                    knownCandidate,
                    spacing);
                var manifestPath = Path.Combine(
                    rescueDirectory,
                    "windows-rescue-manifest.json");
                await WriteRescueManifestAsync(
                    manifestPath,
                    audit.OperationId,
                    knownCandidate,
                    spacing,
                    execution.ExitCode!.Value,
                    verified);
                return DicomRescueResult.Success(
                    audit.OperationId,
                    audit.WorkspaceDirectory,
                    verified.PatchedNiftiPath,
                    manifestPath,
                    verified.Previews);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or JsonException
                    or InvalidDataException
                    or ArgumentException)
            {
                return DicomRescueResult.Failure(
                    "dicom_rescue_output_invalid",
                    "作成した確認画像と寸法情報を検証できませんでした。",
                    audit.OperationId,
                    audit.WorkspaceDirectory);
            }
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

    private static ParsedDicomAudit ParseAudit(
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
        var cleanCandidates = new List<DicomCleanCandidate>();
        var rescueCandidates = new List<DicomRescueCandidate>();
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
            if (requiresExternalTool.GetBoolean())
            {
                continue;
            }

            var classificationStatus = status.GetString();
            var action = nextAction.GetString();
            var clean =
                classificationStatus == "original_ct_geometry_ok"
                && action == "convert_clean";
            var rescue =
                classificationStatus
                    == "secondary_capture_rescue_candidate"
                && action
                    == "prepare_rescue_with_explicit_spacing";
            if (!clean && !rescue)
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

            if (clean)
            {
                cleanCandidates.Add(
                    new DicomCleanCandidate(
                        $"clean-{cleanCandidates.Count + 1}",
                        operationId,
                        seriesKey,
                        seriesNumber,
                        seriesDescription,
                        fileCount,
                        classificationStatus!));
                continue;
            }

            var sliceThickness = OptionalConsistentSpacing(
                item,
                "slice_thickness");
            rescueCandidates.Add(
                new DicomRescueCandidate(
                    $"rescue-{rescueCandidates.Count + 1}",
                    operationId,
                    seriesKey,
                    seriesNumber,
                    seriesDescription,
                    fileCount,
                    classificationStatus!,
                    sliceThickness));
        }
        return new ParsedDicomAudit(
            cleanCandidates,
            rescueCandidates);
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

    private static VerifiedRescue VerifyRescue(
        string rescueDirectory,
        DicomRescueCandidate candidate,
        DicomSpacing requestedSpacing)
    {
        var metadataPath = Path.Combine(
            rescueDirectory,
            "rescue_metadata.json");
        using (var document = JsonDocument.Parse(
                   File.ReadAllText(metadataPath)))
        {
            var root = document.RootElement;
            RequireString(root, "schema", RescueSchema);
            RequireString(root, "status", "success");
            var selected = RequireObject(root, "selected_series");
            RequireString(
                selected,
                "classification",
                "secondary_capture_rescue_candidate");
            RequireString(
                selected,
                "series_instance_uid",
                candidate.NativeSeriesKey);
            if (RequirePositiveInt32(selected, "file_count")
                != candidate.FileCount)
            {
                throw new InvalidDataException(
                    "The rescue series file count changed.");
            }
            var warnings = RequireObject(root, "warnings");
            foreach (var warning in new[]
                     {
                         "secondary_capture",
                         "geometry_inferred",
                         "burned_in_annotation",
                         "not_segmentation_grade_original_ct",
                         "manual_spacing_required",
                     })
            {
                RequireTrue(warnings, warning);
            }
            RequireSpacing(
                root,
                "patched_spacing",
                requestedSpacing);
            var dcm2niix = RequireObject(root, "dcm2niix");
            if (RequireInt32(dcm2niix, "returncode") != 0)
            {
                throw new InvalidDataException(
                    "dcm2niix did not succeed.");
            }
        }

        var validationPath = Path.Combine(
            rescueDirectory,
            "rescue_validation.json");
        using var validationDocument = JsonDocument.Parse(
            File.ReadAllText(validationPath));
        var validation = validationDocument.RootElement;
        RequireString(
            validation,
            "schema",
            RescueValidationSchema);
        RequireString(validation, "status", "success");
        RequireTrue(
            validation,
            "patched_spacing_matches_requested");
        RequireSpacing(
            validation,
            "requested_spacing",
            requestedSpacing);
        var patched = RequireObject(validation, "patched_nifti");
        RequireTrue(patched, "ok");
        RequireSpacing(patched, "spacing", requestedSpacing);
        var patchedNifti = Path.GetFullPath(
            RequireNonEmptyString(patched, "path"));
        var rescueRoot = Path.GetFullPath(rescueDirectory);
        if (!IsPathWithin(patchedNifti, rescueRoot)
            || !File.Exists(patchedNifti)
            || new FileInfo(patchedNifti).Length <= 0
            || !IsNifti(patchedNifti))
        {
            throw new InvalidDataException(
                "The rescue NIfTI output is invalid.");
        }

        var mpr = RequireObject(validation, "mpr_preview");
        RequireTrue(mpr, "written");
        var previewRoot = Path.GetFullPath(
            Path.Combine(rescueDirectory, "mpr_preview"));
        var previews = new List<DicomRescuePreview>();
        foreach (var item in RequireArray(
                     mpr,
                     "previews").EnumerateArray())
        {
            var plane = RequireNonEmptyString(item, "plane");
            if (plane is not ("axial" or "coronal" or "sagittal")
                || previews.Any(existing => existing.Plane == plane))
            {
                throw new InvalidDataException(
                    "A rescue preview plane is invalid.");
            }
            var path = Path.GetFullPath(
                RequireNonEmptyString(item, "path"));
            var width = RequirePositiveInt32(item, "width");
            var height = RequirePositiveInt32(item, "height");
            if (!IsPathWithin(path, previewRoot)
                || !File.Exists(path)
                || new FileInfo(path).Length <= 0)
            {
                throw new InvalidDataException(
                    "A rescue preview image is invalid.");
            }
            previews.Add(
                new DicomRescuePreview(
                    plane,
                    path,
                    width,
                    height,
                    RequireBoolean(item, "uniform_or_empty")));
        }
        if (previews.Count != 3)
        {
            throw new InvalidDataException(
                "Three rescue preview images are required.");
        }
        return new VerifiedRescue(
            patchedNifti,
            previews);
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

    private static async Task WriteRescueManifestAsync(
        string manifestPath,
        string operationId,
        DicomRescueCandidate candidate,
        DicomSpacing spacing,
        uint normalizerExitCode,
        VerifiedRescue verified)
    {
        var payload = new
        {
            schema =
                "totalsegmentator_wrapper.windows_dicom_rescue_preview.v1",
            status = "success",
            operation_id = operationId,
            source_kind = "dicom_secondary_capture",
            selected_series = new
            {
                series_number = candidate.SeriesNumber,
                file_count = candidate.FileCount,
                classification = candidate.Classification,
            },
            confirmed_spacing_xyz = spacing.Values,
            warning_flags = new
            {
                secondary_capture = true,
                geometry_inferred = true,
                burned_in_annotation = true,
                not_segmentation_grade_original_ct = true,
                manual_spacing_required = true,
            },
            normalizer = new
            {
                exit_code = normalizerExitCode,
                status = "success",
            },
            output_validation = new
            {
                patched_nifti_nonempty = true,
                preview_count = verified.Previews.Count,
                preview_planes = verified.Previews
                    .Select(preview => preview.Plane)
                    .Order(StringComparer.Ordinal)
                    .ToArray(),
            },
            segmentation_started = false,
            rescue_output_promoted_as_clean_ct = false,
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

    private static double? OptionalConsistentSpacing(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind == JsonValueKind.Null)
        {
            return null;
        }
        if (value.ValueKind == JsonValueKind.Number)
        {
            if (!value.TryGetDouble(out var parsed)
                || !double.IsFinite(parsed)
                || parsed <= 0)
            {
                throw new InvalidDataException(
                    $"The {propertyName} value is invalid.");
            }
            return parsed;
        }
        if (value.ValueKind != JsonValueKind.Object
            || !value.TryGetProperty(
                "consistent",
                out var consistent)
            || consistent.ValueKind is not (
                JsonValueKind.True or JsonValueKind.False)
            || !value.TryGetProperty(
                "values_mm",
                out var values)
            || values.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        if (!consistent.GetBoolean())
        {
            return null;
        }
        var parsedValues = values.EnumerateArray().ToArray();
        if (parsedValues.Length != 1
            || !parsedValues[0].TryGetDouble(out var spacing)
            || !double.IsFinite(spacing)
            || spacing <= 0)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return spacing;
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

    private static void RequireTrue(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind != JsonValueKind.True)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
    }

    private static bool RequireBoolean(
        JsonElement parent,
        string propertyName)
    {
        if (!parent.TryGetProperty(
                propertyName,
                out var value)
            || value.ValueKind is not (
                JsonValueKind.True
                    or JsonValueKind.False))
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        return value.GetBoolean();
    }

    private static void RequireSpacing(
        JsonElement parent,
        string propertyName,
        DicomSpacing expected)
    {
        var value = RequireArray(parent, propertyName);
        var values = value.EnumerateArray().ToArray();
        if (values.Length != 3)
        {
            throw new InvalidDataException(
                $"The {propertyName} value is invalid.");
        }
        var expectedValues = expected.Values;
        for (var index = 0; index < values.Length; index++)
        {
            if (!values[index].TryGetDouble(out var parsed)
                || !double.IsFinite(parsed)
                || Math.Abs(parsed - expectedValues[index]) > 1e-4)
            {
                throw new InvalidDataException(
                    $"The {propertyName} value is invalid.");
            }
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

    private sealed record VerifiedRescue(
        string PatchedNiftiPath,
        IReadOnlyList<DicomRescuePreview> Previews);

    private sealed record ParsedDicomAudit(
        IReadOnlyList<DicomCleanCandidate> CleanCandidates,
        IReadOnlyList<DicomRescueCandidate> RescueCandidates);
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

internal sealed record DicomRescueCandidate(
    string CandidateId,
    string AuditOperationId,
    string NativeSeriesKey,
    int? SeriesNumber,
    string SeriesDescription,
    int FileCount,
    string Classification,
    double? SliceThickness)
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

    public string DisplayDetail =>
        SliceThickness.HasValue
            ? $"{FileCount}枚 / スライス厚 {SliceThickness.Value:0.####} mm"
            : $"{FileCount}枚 / 寸法情報なし";

    public DicomSpacing InitialSpacing =>
        new(1.0, 1.0, SliceThickness ?? 1.0);
}

internal sealed record DicomSpacing(
    double X,
    double Y,
    double Z)
{
    public bool IsValid =>
        new[] { X, Y, Z }.All(
            value => double.IsFinite(value)
                && value > 0
                && value <= 20);

    public double[] Values => [X, Y, Z];

    public string CommandValue => string.Join(
        ",",
        Values.Select(
            value => value.ToString(
                "0.######",
                System.Globalization.CultureInfo.InvariantCulture)));
}

internal sealed record DicomRescuePreview(
    string Plane,
    string Path,
    int Width,
    int Height,
    bool UniformOrEmpty);

internal sealed record DicomAuditResult(
    bool Succeeded,
    string? OperationId,
    string? WorkspaceDirectory,
    string? SourceDirectory,
    string? AuditPath,
    IReadOnlyList<DicomCleanCandidate> Candidates,
    IReadOnlyList<DicomRescueCandidate> RescueCandidates,
    string? ErrorCode,
    string? SafeMessage)
{
    internal static DicomAuditResult Success(
        string operationId,
        string workspaceDirectory,
        string sourceDirectory,
        string auditPath,
        IReadOnlyList<DicomCleanCandidate> candidates,
        IReadOnlyList<DicomRescueCandidate> rescueCandidates)
    {
        return new DicomAuditResult(
            true,
            operationId,
            workspaceDirectory,
            sourceDirectory,
            auditPath,
            candidates,
            rescueCandidates,
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
            Array.Empty<DicomRescueCandidate>(),
            errorCode,
            safeMessage);
    }
}

internal sealed record DicomRescueResult(
    bool Succeeded,
    string? OperationId,
    string? WorkspaceDirectory,
    string? NiftiPath,
    string? ManifestPath,
    IReadOnlyList<DicomRescuePreview> Previews,
    string? ErrorCode,
    string? SafeMessage)
{
    internal static DicomRescueResult Success(
        string operationId,
        string workspaceDirectory,
        string niftiPath,
        string manifestPath,
        IReadOnlyList<DicomRescuePreview> previews)
    {
        return new DicomRescueResult(
            true,
            operationId,
            workspaceDirectory,
            niftiPath,
            manifestPath,
            previews,
            null,
            null);
    }

    internal static DicomRescueResult Failure(
        string errorCode,
        string safeMessage,
        string? operationId = null,
        string? workspaceDirectory = null)
    {
        return new DicomRescueResult(
            false,
            operationId,
            workspaceDirectory,
            null,
            null,
            Array.Empty<DicomRescuePreview>(),
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
