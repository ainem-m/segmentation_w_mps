using System.ComponentModel;
using System.Text.RegularExpressions;
using TotalSegmentatorWrapper.Windows.ProcessSupervisor;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed class ResultToolsSession : IDisposable
{
    private const uint CancelExitCode = 1223;
    private const uint TimeoutExitCode = 124;
    private static readonly TimeSpan SlicerExportTimeout =
        TimeSpan.FromMinutes(15);
    private static readonly TimeSpan PreviewRebuildTimeout =
        TimeSpan.FromMinutes(30);
    private static readonly TimeSpan TerminationWait =
        TimeSpan.FromSeconds(10);
    private static readonly Regex ProtocolRelativeResource =
        new(
            """(?is)\b(?:src|href|poster|action)\s*=\s*["']\s*//|url\(\s*["']?\s*//""",
            RegexOptions.CultureInvariant);

    private readonly ShellConfiguration _configuration;
    private readonly object _gate = new();
    private JobProcess? _activeProcess;
    private TerminationReason _terminationReason;
    private bool _operationActive;
    private bool _cancelRequested;
    private bool _disposed;

    internal ResultToolsSession(ShellConfiguration configuration)
    {
        _configuration = configuration;
    }

    internal async Task<ResultToolResult> ExportSlicerAsync(
        string finalDirectory)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!BeginOperation())
        {
            return ResultToolResult.Failure(
                "result_tool_busy");
        }

        try
        {
            if (!TryResolveCaseDirectory(
                    finalDirectory,
                    out var caseDirectory,
                    out var pythonPath))
            {
                return ResultToolResult.Failure(
                    "result_case_unavailable");
            }

            var outputDirectory = Path.Combine(
                caseDirectory,
                "slicer_export");
            var execution = await RunJobAsync(
                pythonPath,
                new[]
                {
                    "-m",
                    "totalsegmentator_wrapper_mac",
                    "slicer-export",
                    "--case",
                    caseDirectory,
                    "--output",
                    outputDirectory,
                },
                SlicerExportTimeout);
            return MapExecutionResult(
                outputDirectory,
                execution);
        }
        finally
        {
            EndOperation();
        }
    }

    internal async Task<ResultToolResult> RebuildPreviewAsync(
        string finalDirectory)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!BeginOperation())
        {
            return ResultToolResult.Failure(
                "result_tool_busy");
        }

        string? stagingDirectory = null;
        try
        {
            if (!TryResolveCaseDirectory(
                    finalDirectory,
                    out var caseDirectory,
                    out var pythonPath))
            {
                return ResultToolResult.Failure(
                    "result_case_unavailable");
            }

            var stagingRoot = Path.Combine(
                caseDirectory,
                ".postprocess-staging");
            stagingDirectory = Path.Combine(
                stagingRoot,
                Guid.NewGuid().ToString("D"));
            try
            {
                Directory.CreateDirectory(stagingDirectory);
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or ArgumentException)
            {
                return ResultToolResult.Failure(
                    "preview_staging_unavailable");
            }

            var execution = await RunJobAsync(
                pythonPath,
                new[]
                {
                    "-m",
                    "totalsegmentator_wrapper_mac",
                    "surface-preview",
                    "--case",
                    caseDirectory,
                    "--output",
                    stagingDirectory,
                },
                PreviewRebuildTimeout);
            var executionResult = MapExecutionResult(
                stagingDirectory,
                execution);
            if (!executionResult.Succeeded)
            {
                return executionResult;
            }

            if (!IsValidOfflinePreview(stagingDirectory))
            {
                return ResultToolResult.Failure(
                    "preview_verification_failed");
            }

            string promotedDirectory;
            try
            {
                promotedDirectory = NextPreviewDestination(caseDirectory);
                Directory.Move(stagingDirectory, promotedDirectory);
                stagingDirectory = null;
            }
            catch (Exception exception) when (
                exception is IOException
                    or UnauthorizedAccessException
                    or ArgumentException)
            {
                return ResultToolResult.Failure(
                    "preview_promotion_failed");
            }

            return ResultToolResult.Success(
                promotedDirectory);
        }
        finally
        {
            if (stagingDirectory is not null)
            {
                TryDeleteStagingDirectory(stagingDirectory);
            }
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
                // The operation returns a process-tree failure unless it can
                // confirm that the assigned Job became empty.
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

    private bool TryResolveCaseDirectory(
        string finalDirectory,
        out string caseDirectory,
        out string pythonPath)
    {
        caseDirectory = string.Empty;
        pythonPath = Path.Combine(
            _configuration.CoordinatorWorkingDirectory,
            "python.exe");
        try
        {
            caseDirectory = Path.GetFullPath(finalDirectory);
            return Directory.Exists(caseDirectory)
                && File.Exists(
                    Path.Combine(caseDirectory, "run-manifest.json"))
                && File.Exists(
                    Path.Combine(caseDirectory, "artifact-manifest.json"))
                && File.Exists(pythonPath);
        }
        catch (Exception exception) when (
            exception is ArgumentException
                or NotSupportedException
                or PathTooLongException)
        {
            return false;
        }
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
                _configuration.CoordinatorWorkingDirectory);
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
                _ = await WaitForJobEmptyAsync(
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

    private static ResultToolResult MapExecutionResult(
        string outputDirectory,
        JobExecutionResult execution)
    {
        if (!execution.Started)
        {
            return ResultToolResult.Failure(
                "result_tool_start_failed");
        }
        if (execution.Cancelled)
        {
            return ResultToolResult.Failure(
                "result_tool_cancelled");
        }
        if (execution.TimedOut)
        {
            return ResultToolResult.Failure(
                "result_tool_timeout");
        }
        if (!execution.JobBecameEmpty)
        {
            return ResultToolResult.Failure(
                "result_tool_process_tree_error");
        }
        if (execution.ExitCode != 0)
        {
            return ResultToolResult.Failure(
                "result_tool_failed");
        }
        return ResultToolResult.Success(
            outputDirectory);
    }

    private static bool IsValidOfflinePreview(string outputDirectory)
    {
        try
        {
            var indexPath = Path.Combine(outputDirectory, "index.html");
            if (!File.Exists(indexPath)
                || new FileInfo(indexPath).Length == 0)
            {
                return false;
            }
            var html = File.ReadAllText(indexPath);
            return !html.Contains(
                    "http://",
                    StringComparison.OrdinalIgnoreCase)
                && !html.Contains(
                    "https://",
                    StringComparison.OrdinalIgnoreCase)
                && !ProtocolRelativeResource.IsMatch(html);
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or ArgumentException)
        {
            return false;
        }
    }

    private static string NextPreviewDestination(
        string caseDirectory)
    {
        var preferred = Path.Combine(
            caseDirectory,
            "surface_preview_rebuilt");
        if (!Directory.Exists(preferred)
            && !File.Exists(preferred))
        {
            return preferred;
        }

        while (true)
        {
            var suffix =
                $"{DateTime.UtcNow:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}";
            var candidate = Path.Combine(
                caseDirectory,
                $"surface_preview_rebuilt-{suffix}");
            if (!Directory.Exists(candidate)
                && !File.Exists(candidate))
            {
                return candidate;
            }
        }
    }

    private static async Task DrainAsync(StreamReader reader)
    {
        var buffer = new char[4096];
        try
        {
            while (await reader.ReadAsync(buffer.AsMemory()) != 0)
            {
                // Output can contain paths and third-party diagnostics.
                // Drain it to avoid deadlock, but never retain or expose it.
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
            // The caller requires the Job to become empty.
        }
    }

    private static void TryDeleteStagingDirectory(
        string stagingDirectory)
    {
        try
        {
            var parent = Directory.GetParent(stagingDirectory);
            if (parent?.Name == ".postprocess-staging"
                && IsPathWithin(stagingDirectory, parent.FullName)
                && Directory.Exists(stagingDirectory))
            {
                Directory.Delete(stagingDirectory, recursive: true);
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or ArgumentException)
        {
            // A typed operation result has already been selected. A later
            // run can safely use a different UUID staging directory.
        }
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
}

internal sealed record ResultToolResult(
    bool Succeeded,
    string? OutputDirectory,
    string? ErrorCode)
{
    internal static ResultToolResult Success(
        string outputDirectory)
    {
        return new ResultToolResult(
            true,
            outputDirectory,
            null);
    }

    internal static ResultToolResult Failure(
        string errorCode)
    {
        return new ResultToolResult(
            false,
            null,
            errorCode);
    }
}
