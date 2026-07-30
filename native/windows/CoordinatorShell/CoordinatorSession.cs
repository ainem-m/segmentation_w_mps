using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed class CoordinatorSession : IDisposable
{
    private readonly ShellConfiguration _configuration;
    private readonly object _gate = new();
    private Process? _process;
    private bool _cancelSent;
    private bool _disposed;

    internal CoordinatorSession(ShellConfiguration configuration)
    {
        _configuration = configuration;
    }

    internal event EventHandler<CoordinatorEvent>? EventReceived;

    internal string? ActiveOperationId { get; private set; }

    internal async Task<CoordinatorSessionResult> RunAsync(
        string inputPath,
        SegmentationProfile profile =
            SegmentationProfile.TotalSegmentator)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        lock (_gate)
        {
            if (_process is { HasExited: false })
            {
                throw new InvalidOperationException(
                    "A coordinator operation is already running.");
            }
            _cancelSent = false;
        }

        var operationId = Guid.NewGuid().ToString("D");
        ActiveOperationId = operationId;
        var finalDirectoryName =
            $"case-{DateTime.UtcNow:yyyyMMdd-HHmmss}-{operationId[..8]}";
        var finalDirectory = Path.Combine(
            _configuration.OutputRoot,
            finalDirectoryName);
        var hostEvidenceDirectory = Path.Combine(
            _configuration.OutputRoot,
            ".host-evidence",
            operationId);
        Directory.CreateDirectory(hostEvidenceDirectory);
        var requestPath = Path.Combine(hostEvidenceDirectory, "request.json");
        var eventsPath = Path.Combine(hostEvidenceDirectory, "events.jsonl");
        var coordinatorStderrPath = Path.Combine(
            hostEvidenceDirectory,
            "coordinator-stderr.log");
        var supervisorEvidencePath = Path.Combine(
            hostEvidenceDirectory,
            "supervisor-evidence.json");
        var hostStderrPath = Path.Combine(
            hostEvidenceDirectory,
            "host-stderr.log");
        await File.WriteAllTextAsync(
            requestPath,
            JsonSerializer.Serialize(
                new
                {
                    protocol_version = 1,
                    operation_id = operationId,
                    operation = profile.OperationName(),
                    input = new
                    {
                        kind = "nifti",
                        path = Path.GetFullPath(inputPath),
                    },
                    output_directory = finalDirectory,
                    device_policy = new
                    {
                        mode = "cuda_required",
                        index = 0,
                    },
                    options = new
                    {
                        robust_crop =
                            profile
                                == SegmentationProfile.TotalSegmentator,
                        higher_order_resampling = false,
                    },
                }),
            new UTF8Encoding(false));

        Process process;
        try
        {
            process = StartSupervisor(
                requestPath,
                eventsPath,
                coordinatorStderrPath,
                supervisorEvidencePath);
        }
        catch
        {
            File.Delete(requestPath);
            throw;
        }
        using var processLifetime = process;
        lock (_gate)
        {
            _process = process;
        }

        var lastSequence = 0;
        var terminalCount = 0;
        CoordinatorEvent? terminal = null;
        CoordinatorEvent? device = null;
        Exception? protocolFailure = null;
        var stdoutTask = Task.Run(async () =>
        {
            while (await process.StandardOutput.ReadLineAsync() is { } line)
            {
                if (protocolFailure is not null)
                {
                    continue;
                }
                try
                {
                    var parsed = CoordinatorEvent.Parse(line);
                    if (parsed.ProtocolVersion != 1
                        || parsed.OperationId != operationId
                        || parsed.Sequence <= lastSequence)
                    {
                        throw new InvalidDataException(
                            "Coordinator event envelope validation failed.");
                    }
                    lastSequence = parsed.Sequence;
                    if (parsed.EventName == "device_resolved")
                    {
                        device = parsed;
                    }
                    if (parsed.IsTerminal)
                    {
                        terminal = parsed;
                        terminalCount++;
                    }
                    EventReceived?.Invoke(this, parsed);
                }
                catch (Exception exception) when (
                    exception is JsonException
                        or InvalidDataException
                        or InvalidOperationException)
                {
                    protocolFailure = exception;
                }
            }
        });
        var stderrTask = Task.Run(async () =>
        {
            await using var writer = new StreamWriter(
                hostStderrPath,
                append: false,
                new UTF8Encoding(false));
            while (await process.StandardError.ReadLineAsync() is { } line)
            {
                await writer.WriteLineAsync(line);
            }
        });

        try
        {
            await process.WaitForExitAsync();
            await Task.WhenAll(stdoutTask, stderrTask);
        }
        finally
        {
            File.Delete(requestPath);
        }
        var exitCode = process.ExitCode;
        lock (_gate)
        {
            _process = null;
        }

        if (protocolFailure is not null || terminalCount != 1 || terminal is null)
        {
            var safeReason = protocolFailure is null
                ? $"terminal_count={terminalCount};terminal_present={terminal is not null}"
                : $"protocol_exception={protocolFailure.GetType().Name}";
            return CoordinatorSessionResult.HostFailure(
                operationId,
                finalDirectory,
                hostEvidenceDirectory,
                exitCode,
                "host_protocol_error",
                safeReason);
        }

        if (terminal.EventName == "operation_completed")
        {
            var strictDevicePassed =
                device?.RequestedPolicy == "cuda_required"
                && device.RequestedDeviceIndex == 0
                && device.ResolvedDevice == "cuda:0"
                && device.FallbackAllowed == false
                && device.FallbackOccurred == false
                && terminal.RequestedPolicy == "cuda_required"
                && terminal.RequestedDeviceIndex == 0
                && terminal.ResolvedDevice == "cuda:0"
                && terminal.FallbackAllowed == false
                && terminal.FallbackOccurred == false;
            var previewExists = File.Exists(
                Path.Combine(finalDirectory, "surface_preview", "index.html"));
            var runManifestExists = File.Exists(
                Path.Combine(finalDirectory, "run-manifest.json"));
            var artifactManifestExists = File.Exists(
                Path.Combine(finalDirectory, "artifact-manifest.json"));
            if (exitCode != 0
                || !strictDevicePassed
                || !previewExists
                || !runManifestExists
                || !artifactManifestExists)
            {
                return CoordinatorSessionResult.HostFailure(
                    operationId,
                    finalDirectory,
                    hostEvidenceDirectory,
                    exitCode,
                    "host_completion_verification_failed",
                    "処理結果の検証を完了できませんでした。");
            }
        }
        else if (terminal.EventName == "operation_cancelled"
            && exitCode != 0)
        {
            return CoordinatorSessionResult.HostFailure(
                operationId,
                finalDirectory,
                hostEvidenceDirectory,
                exitCode,
                "host_cancellation_verification_failed",
                "停止結果とWindows supervisorの検証結果が一致しませんでした。");
        }

        return new CoordinatorSessionResult(
            operationId,
            terminal.EventName,
            finalDirectory,
            hostEvidenceDirectory,
            exitCode,
            terminal.ErrorCode,
            terminal.SafeReason,
            terminal.ReasonCode,
            device?.RequestedPolicy,
            device?.RequestedDeviceIndex,
            device?.ResolvedDevice,
            device?.FallbackAllowed,
            device?.FallbackOccurred);
    }

    internal async Task RequestCancelAsync()
    {
        Process? process;
        lock (_gate)
        {
            process = _process;
            if (_cancelSent || process is null || process.HasExited)
            {
                return;
            }
            _cancelSent = true;
        }
        await process.StandardInput.WriteLineAsync("cancel");
        await process.StandardInput.FlushAsync();
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        Process? process;
        lock (_gate)
        {
            process = _process;
        }
        if (process is null)
        {
            return;
        }
        try
        {
            if (!process.HasExited)
            {
                if (!_cancelSent)
                {
                    process.StandardInput.WriteLine("cancel");
                    process.StandardInput.Flush();
                    _cancelSent = true;
                }
                if (!process.WaitForExit(5000))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
            }
        }
        catch (InvalidOperationException)
        {
        }
    }

    private Process StartSupervisor(
        string requestPath,
        string eventsPath,
        string coordinatorStderrPath,
        string supervisorEvidencePath)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = _configuration.SupervisorPath,
            WorkingDirectory = _configuration.CoordinatorWorkingDirectory,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
            CreateNoWindow = true,
        };
        foreach (var argument in new[]
        {
            "supervise",
            "--request",
            requestPath,
            "--events",
            eventsPath,
            "--stderr",
            coordinatorStderrPath,
            "--evidence",
            supervisorEvidencePath,
            "--working-directory",
            _configuration.CoordinatorWorkingDirectory,
            "--interactive-cancel",
            "--grace-ms",
            "12000",
            "--",
            _configuration.CoordinatorPath,
        })
        {
            startInfo.ArgumentList.Add(argument);
        }
        var coordinatorDirectory = Path.GetDirectoryName(
            _configuration.CoordinatorPath)
            ?? throw new InvalidOperationException(
                "The coordinator directory is unavailable.");
        _ = startInfo.Environment.TryGetValue(
            "PATH",
            out var existingPath);
        existingPath ??= string.Empty;
        startInfo.Environment["PATH"] = string.Join(
            Path.PathSeparator,
            new[]
            {
                coordinatorDirectory,
                _configuration.CoordinatorWorkingDirectory,
                existingPath,
            }.Where(value => !string.IsNullOrWhiteSpace(value)));
        startInfo.Environment["TOTALSEG_HOME_DIR"] =
            _configuration.TotalSegmentatorHome;
        startInfo.Environment["TSWM_DENTALSEG_MODEL_ROOT"] =
            _configuration.DentalSegmentatorModelRoot;
        startInfo.Environment["TSWM_TOOTHSEG_MODEL_ROOT"] =
            _configuration.ToothSegModelRoot;
        startInfo.Environment["PYTHONNOUSERSITE"] = "1";
        startInfo.Environment["PYTHONUTF8"] = "1";
        return Process.Start(startInfo)
            ?? throw new InvalidOperationException(
                "The Windows process supervisor did not start.");
    }
}

internal sealed record CoordinatorSessionResult(
    string OperationId,
    string TerminalEvent,
    string FinalDirectory,
    string HostEvidenceDirectory,
    int SupervisorExitCode,
    string? ErrorCode,
    string? SafeReason,
    string? ReasonCode,
    string? RequestedPolicy,
    int? RequestedDeviceIndex,
    string? ResolvedDevice,
    bool? FallbackAllowed,
    bool? FallbackOccurred)
{
    internal static CoordinatorSessionResult HostFailure(
        string operationId,
        string finalDirectory,
        string hostEvidenceDirectory,
        int exitCode,
        string errorCode,
        string safeReason)
    {
        return new CoordinatorSessionResult(
            operationId,
            "host_failed",
            finalDirectory,
            hostEvidenceDirectory,
            exitCode,
            errorCode,
            safeReason,
            null,
            null,
            null,
            null,
            null,
            null);
    }
}
