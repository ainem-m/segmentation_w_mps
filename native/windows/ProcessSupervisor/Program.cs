using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace TotalSegmentatorWrapper.Windows.ProcessSupervisor;

internal static class Program
{
    private const string EvidenceSchema =
        "totalsegmentator_wrapper.windows_process_supervisor.v1";

    internal static async Task<int> Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        try
        {
            if (args.Length == 0)
            {
                throw new ArgumentException("A command is required.");
            }
            return args[0] switch
            {
                "self-test" => await RunSelfTestAsync(args[1..]),
                "supervise" => await RunSupervisorAsync(args[1..]),
                "synthetic-coordinator" => await RunSyntheticCoordinatorAsync(args[1..]),
                "synthetic-node" => await RunSyntheticNodeAsync(args[1..]),
                _ => throw new ArgumentException("The command is not supported."),
            };
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(
                $"process supervisor diagnostic: {exception.GetType().Name}");
            return 2;
        }
    }

    private static async Task<int> RunSelfTestAsync(string[] args)
    {
        var options = OptionSet.Parse(args, allowCommand: false);
        var evidencePath = options.RequiredPath("evidence");
        var executable = Environment.ProcessPath
            ?? throw new InvalidOperationException("The current executable path is unavailable.");
        using var process = JobProcess.StartSuspended(
            executable,
            ["synthetic-node", "--depth", "2", "--root-control"],
            Environment.CurrentDirectory);
        var stdout = DrainAsync(process.StandardOutput);
        var stderr = DrainAsync(process.StandardError);
        process.Resume();

        var membersBeforeCancel = await WaitForMembersAsync(process, minimum: 3);
        await process.StandardInput.WriteLineAsync("cancel");
        var rootExitedDuringGrace = process.WaitForExit(TimeSpan.FromSeconds(3));
        var membersAfterGrace = process.ActiveProcessIds();
        process.Terminate(23);
        var survivors = await WaitForNoMembersAsync(process);
        await Task.WhenAll(stdout, stderr);

        var passed =
            membersBeforeCancel.Count >= 3
            && rootExitedDuringGrace
            && membersAfterGrace.Count >= 2
            && survivors.Count == 0;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema = EvidenceSchema,
                mode = "synthetic_tree",
                status = passed ? "pass" : "fail",
                kill_on_job_close = true,
                created_suspended = true,
                assigned_before_resume = true,
                graceful_cancel_sent = true,
                root_exited_during_grace = rootExitedDuringGrace,
                members_before_cancel = membersBeforeCancel.Count,
                members_after_grace = membersAfterGrace.Count,
                terminate_job_called = true,
                active_processes_after = survivors.Count,
                no_survivors = survivors.Count == 0,
            });
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            status = passed ? "pass" : "fail",
            no_survivors = survivors.Count == 0,
        }));
        return passed ? 0 : 1;
    }

    private static async Task<int> RunSupervisorAsync(string[] args)
    {
        var options = OptionSet.Parse(args, allowCommand: true);
        var requestPath = options.RequiredPath("request");
        var eventsPath = options.RequiredPath("events");
        var stderrPath = options.RequiredPath("stderr");
        var evidencePath = options.RequiredPath("evidence");
        var workingDirectory = options.RequiredPath("working-directory");
        var normalCompletion = options.HasFlag("normal-completion");
        var interactiveCancel = options.HasFlag("interactive-cancel");
        var grace = TimeSpan.FromMilliseconds(options.OptionalInt("grace-ms", 3000));
        var cancelAfter = options.OptionalInt("cancel-after-ms", -1);
        var cancelOnStage = options.Optional("cancel-on-stage");
        var cancelDelay = options.OptionalInt("cancel-delay-ms", 0);
        if (normalCompletion
            && (interactiveCancel
                || options.Optional("cancel-after-ms") is not null
                || cancelOnStage is not null
                || options.Optional("cancel-delay-ms") is not null))
        {
            throw new ArgumentException(
                "--normal-completion cannot be combined with cancellation options.");
        }
        if (interactiveCancel
            && (options.Optional("cancel-after-ms") is not null
                || cancelOnStage is not null
                || options.Optional("cancel-delay-ms") is not null))
        {
            throw new ArgumentException(
                "--interactive-cancel cannot be combined with timed or stage cancellation.");
        }
        if (!normalCompletion
            && !interactiveCancel
            && cancelAfter < 0
            && cancelOnStage is null)
        {
            throw new ArgumentException(
                "Either --cancel-after-ms or --cancel-on-stage is required.");
        }
        if (options.Command.Count == 0)
        {
            throw new ArgumentException("A supervised command is required after --.");
        }

        var requestText = await File.ReadAllTextAsync(requestPath);
        using var requestDocument = JsonDocument.Parse(requestText);
        var operationId = requestDocument.RootElement
            .GetProperty("operation_id")
            .GetString()
            ?? throw new ArgumentException("The request operation_id is missing.");
        var finalOutputPath = requestDocument.RootElement
            .GetProperty("output_directory")
            .GetString()
            ?? throw new ArgumentException("The request output_directory is missing.");
        var outputParent = Path.GetDirectoryName(finalOutputPath)
            ?? throw new ArgumentException("The output parent is unavailable.");
        var stagingPath = Path.Combine(
            outputParent,
            $".tswm-{operationId}.staging");
        var requestLine = JsonSerializer.Serialize(requestDocument.RootElement);

        Directory.CreateDirectory(
            Path.GetDirectoryName(Path.GetFullPath(eventsPath))
            ?? throw new ArgumentException("The events parent is unavailable."));
        using var process = JobProcess.StartSuspended(
            options.Command[0],
            options.Command.Skip(1).ToArray(),
            workingDirectory);
        await using var events = new StreamWriter(
            eventsPath,
            append: false,
            new System.Text.UTF8Encoding(false))
        {
            AutoFlush = true,
        };
        await using var stderr = new StreamWriter(
            stderrPath,
            append: false,
            new System.Text.UTF8Encoding(false))
        {
            AutoFlush = true,
        };
        var stageReached = new TaskCompletionSource(
            TaskCreationOptions.RunContinuationsAsynchronously);
        string? terminalEvent = null;
        var terminalEventCount = 0;
        var terminalReached = new TaskCompletionSource<string>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var stdoutTask = Task.Run(async () =>
        {
            while (await process.StandardOutput.ReadLineAsync() is { } line)
            {
                await events.WriteLineAsync(line);
                try
                {
                    using var document = JsonDocument.Parse(line);
                    var root = document.RootElement;
                    if (root.TryGetProperty("event", out var eventValue))
                    {
                        var eventName = eventValue.GetString();
                        if (eventName is "operation_completed"
                            or "operation_failed"
                            or "operation_cancelled")
                        {
                            terminalEvent = eventName;
                            terminalEventCount++;
                            terminalReached.TrySetResult(eventName);
                        }
                    }
                    if (cancelOnStage is not null && MatchesStage(root, cancelOnStage))
                    {
                        stageReached.TrySetResult();
                    }
                }
                catch (JsonException)
                {
                    throw new InvalidDataException(
                        "The supervised process wrote non-JSONL stdout.");
                }
                await Console.Out.WriteLineAsync(line);
                await Console.Out.FlushAsync();
            }
        });
        var stderrTask = Task.Run(async () =>
        {
            while (await process.StandardError.ReadLineAsync() is { } line)
            {
                await stderr.WriteLineAsync(line);
            }
        });

        Task<string?>? interactiveCommandTask = null;
        if (interactiveCancel)
        {
            interactiveCommandTask = Task.Run(Console.In.ReadLine);
        }
        process.Resume();
        await process.StandardInput.WriteLineAsync(requestLine);
        var coordinatorExitedBeforeInteractiveCancel = false;
        SortedSet<int>? interactiveObservedProcessIds = null;
        IReadOnlyList<int>? interactiveMembersAtCancel = null;
        var interactiveControlSent = false;
        if (interactiveCancel)
        {
            interactiveObservedProcessIds = new(process.ActiveProcessIds());
            var coordinatorExitTask = Task.Run(
                () => process.WaitForExit(Timeout.InfiniteTimeSpan));
            while (true)
            {
                interactiveObservedProcessIds.UnionWith(
                    process.ActiveProcessIds());
                var completed = await Task.WhenAny(
                    interactiveCommandTask
                        ?? throw new InvalidOperationException(
                            "The interactive command reader was not initialized."),
                    terminalReached.Task,
                    coordinatorExitTask,
                    Task.Delay(100));
                if (completed == interactiveCommandTask)
                {
                    break;
                }
                if (completed == terminalReached.Task
                    || completed == coordinatorExitTask)
                {
                    coordinatorExitedBeforeInteractiveCancel = true;
                    break;
                }
            }
            interactiveObservedProcessIds.UnionWith(process.ActiveProcessIds());
            if (!coordinatorExitedBeforeInteractiveCancel)
            {
                var command = await interactiveCommandTask;
                if (!string.Equals(command, "cancel", StringComparison.Ordinal))
                {
                    throw new InvalidDataException(
                        "Interactive supervisor input must be exactly one cancel command.");
                }
                interactiveMembersAtCancel = process.ActiveProcessIds();
                try
                {
                    await process.StandardInput.WriteLineAsync(
                        JsonSerializer.Serialize(new
                        {
                            protocol_version = 1,
                            operation_id = operationId,
                            control = "cancel",
                        }));
                    interactiveControlSent = true;
                }
                catch (IOException) when (
                    process.WaitForExit(TimeSpan.FromSeconds(1)))
                {
                    coordinatorExitedBeforeInteractiveCancel = true;
                }
            }
        }
        if (normalCompletion || coordinatorExitedBeforeInteractiveCancel)
        {
            process.StandardInput.Close();
            var observedProcessIds =
                interactiveObservedProcessIds
                ?? new SortedSet<int>(process.ActiveProcessIds());
            var rootExitWait = Stopwatch.StartNew();
            var rootExitTimedOut = false;
            while (!process.WaitForExit(TimeSpan.FromMilliseconds(100)))
            {
                observedProcessIds.UnionWith(process.ActiveProcessIds());
                if (interactiveCancel && rootExitWait.Elapsed >= grace)
                {
                    rootExitTimedOut = true;
                    process.Terminate(24);
                    if (!process.WaitForExit(TimeSpan.FromSeconds(5)))
                    {
                        throw new InvalidOperationException(
                            "The coordinator did not exit after Job termination.");
                    }
                    break;
                }
            }
            observedProcessIds.UnionWith(process.ActiveProcessIds());
            var coordinatorExitCode = process.ExitCode;
            var membersAfterCompletion = await WaitForNoMembersAsync(process);
            var cleanupTerminateJobCalled = membersAfterCompletion.Count > 0;
            IReadOnlyList<int> membersAfterCleanup = membersAfterCompletion;
            if (cleanupTerminateJobCalled)
            {
                process.Terminate(24);
                membersAfterCleanup = await WaitForNoMembersAsync(process);
            }
            await Task.WhenAll(stdoutTask, stderrTask);

            var normalFinalOutputExists = Directory.Exists(finalOutputPath);
            var normalStagingExists = Directory.Exists(stagingPath);
            var runManifestExists = File.Exists(
                Path.Combine(finalOutputPath, "run-manifest.json"));
            var artifactManifestExists = File.Exists(
                Path.Combine(finalOutputPath, "artifact-manifest.json"));
            var offlinePreviewExists = File.Exists(
                Path.Combine(finalOutputPath, "surface_preview", "index.html"));
            var normalCompletionPassed =
                terminalEvent == "operation_completed"
                && terminalEventCount == 1
                && !rootExitTimedOut
                && coordinatorExitCode == 0
                && membersAfterCompletion.Count == 0
                && normalFinalOutputExists
                && !normalStagingExists
                && runManifestExists
                && artifactManifestExists
                && offlinePreviewExists;
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema = EvidenceSchema,
                    mode = "normal_completion",
                    operation_id = operationId,
                    status = normalCompletionPassed ? "pass" : "fail",
                    kill_on_job_close = true,
                    created_suspended = true,
                    assigned_before_resume = true,
                    graceful_cancel_sent = false,
                    coordinator_os_exit_code = coordinatorExitCode,
                    terminal_event = terminalEvent,
                    terminal_event_count = terminalEventCount,
                    observed_job_process_count = observedProcessIds.Count,
                    observed_job_process_ids = observedProcessIds,
                    root_exit_grace_ms = interactiveCancel
                        ? checked((int)grace.TotalMilliseconds)
                        : (int?)null,
                    root_exit_timed_out = rootExitTimedOut,
                    root_exit_terminate_job_called = rootExitTimedOut,
                    active_processes_after = membersAfterCompletion.Count,
                    cleanup_terminate_job_called = cleanupTerminateJobCalled,
                    active_processes_after_cleanup = membersAfterCleanup.Count,
                    no_survivors = membersAfterCleanup.Count == 0,
                    final_output_promoted = normalFinalOutputExists,
                    staging_exists = normalStagingExists,
                    run_manifest_exists = runManifestExists,
                    artifact_manifest_exists = artifactManifestExists,
                    offline_preview_exists = offlinePreviewExists,
                });
            return normalCompletionPassed ? 0 : 1;
        }
        if (!interactiveCancel && cancelOnStage is not null)
        {
            await stageReached.Task.WaitAsync(TimeSpan.FromMinutes(5));
            if (cancelDelay > 0)
            {
                await Task.Delay(cancelDelay);
            }
        }
        else if (!interactiveCancel)
        {
            await Task.Delay(cancelAfter);
        }

        var membersAtCancel =
            interactiveMembersAtCancel
            ?? process.ActiveProcessIds();
        if (!interactiveControlSent)
        {
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new
            {
                protocol_version = 1,
                operation_id = operationId,
                control = "cancel",
            }));
        }
        var rootExitedDuringGrace = process.WaitForExit(grace);
        var membersAfterGrace = process.ActiveProcessIds();
        var terminateJobCalled = membersAfterGrace.Count > 0;
        if (terminateJobCalled)
        {
            process.Terminate(24);
        }
        var survivors = await WaitForNoMembersAsync(process);
        process.StandardInput.Close();
        await Task.WhenAll(stdoutTask, stderrTask);

        var cancelCoordinatorExitCode = process.ExitCode;
        var finalOutputExists = Directory.Exists(finalOutputPath);
        var stagingExists = Directory.Exists(stagingPath);
        var passed =
            terminalEvent == "operation_cancelled"
            && terminalEventCount == 1
            && cancelCoordinatorExitCode == 3
            && survivors.Count == 0
            && !finalOutputExists;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema = EvidenceSchema,
                mode = "supervised_process",
                operation_id = operationId,
                status = passed ? "pass" : "fail",
                kill_on_job_close = true,
                created_suspended = true,
                assigned_before_resume = true,
                graceful_cancel_sent = true,
                graceful_control_acknowledged =
                    terminalEvent == "operation_cancelled",
                grace_ms = checked((int)grace.TotalMilliseconds),
                cancel_trigger = interactiveCancel
                    ? "interactive_stdin"
                    : cancelOnStage is null
                        ? $"after_ms:{cancelAfter}"
                        : $"stage:{cancelOnStage}",
                job_members_at_cancel = membersAtCancel.Count,
                job_member_pids_at_cancel = membersAtCancel,
                root_exited_during_grace = rootExitedDuringGrace,
                members_after_grace = membersAfterGrace.Count,
                terminate_job_called = terminateJobCalled,
                active_processes_after = survivors.Count,
                no_survivors = survivors.Count == 0,
                observed_processes_exited = survivors.Count == 0,
                coordinator_os_exit_code = cancelCoordinatorExitCode,
                terminal_event = terminalEvent,
                terminal_event_count = terminalEventCount,
                final_output_promoted = finalOutputExists,
                staging_exists = stagingExists,
            });
        return passed ? 0 : 1;
    }

    private static async Task<int> RunSyntheticCoordinatorAsync(string[] args)
    {
        if (args.Length > 1
            || (args.Length == 1
                && args[0] is not "complete" and not "complete-linger"))
        {
            throw new ArgumentException(
                "The synthetic coordinator mode is not supported.");
        }
        var completesNormally = args.Length == 1;
        var lingersAfterTerminal = args is ["complete-linger"];
        var requestLine = await Console.In.ReadLineAsync()
            ?? throw new InvalidDataException("A synthetic request is required.");
        using var request = JsonDocument.Parse(requestLine);
        var operationId = request.RootElement
            .GetProperty("operation_id")
            .GetString()
            ?? throw new InvalidDataException("The synthetic operation_id is missing.");
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            protocol_version = 1,
            operation_id = operationId,
            sequence = 1,
            @event = "operation_started",
        }));

        if (completesNormally)
        {
            var finalOutputPath = request.RootElement
                .GetProperty("output_directory")
                .GetString()
                ?? throw new InvalidDataException(
                    "The synthetic output directory is missing.");
            Directory.CreateDirectory(
                Path.Combine(finalOutputPath, "surface_preview"));
            await File.WriteAllTextAsync(
                Path.Combine(finalOutputPath, "run-manifest.json"),
                "{}");
            await File.WriteAllTextAsync(
                Path.Combine(finalOutputPath, "artifact-manifest.json"),
                "{}");
            await File.WriteAllTextAsync(
                Path.Combine(finalOutputPath, "surface_preview", "index.html"),
                "<!doctype html>");
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                protocol_version = 1,
                operation_id = operationId,
                sequence = 2,
                @event = "operation_completed",
            }));
            if (lingersAfterTerminal)
            {
                await Task.Delay(TimeSpan.FromSeconds(30));
            }
            return 0;
        }

        var controlLine = await Console.In.ReadLineAsync()
            ?? throw new InvalidDataException("A synthetic control message is required.");
        using var control = JsonDocument.Parse(controlLine);
        var root = control.RootElement;
        if (root.GetProperty("protocol_version").GetInt32() != 1
            || root.GetProperty("operation_id").GetString() != operationId
            || root.GetProperty("control").GetString() != "cancel")
        {
            throw new InvalidDataException("The synthetic cancel control is invalid.");
        }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            protocol_version = 1,
            operation_id = operationId,
            sequence = 2,
            @event = "operation_cancelled",
        }));
        return 3;
    }

    private static async Task<int> RunSyntheticNodeAsync(string[] args)
    {
        var options = OptionSet.Parse(args, allowCommand: false);
        var depth = options.OptionalInt("depth", 0);
        if (depth > 0)
        {
            var executable = Environment.ProcessPath
                ?? throw new InvalidOperationException(
                    "The current executable path is unavailable.");
            Process.Start(new ProcessStartInfo
            {
                FileName = executable,
                UseShellExecute = false,
                ArgumentList =
                {
                    "synthetic-node",
                    "--depth",
                    (depth - 1).ToString(System.Globalization.CultureInfo.InvariantCulture),
                },
            });
        }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            event_name = "synthetic_node_started",
            pid = Environment.ProcessId,
            depth,
        }));
        if (options.HasFlag("root-control"))
        {
            var control = await Console.In.ReadLineAsync();
            return control == "cancel" ? 0 : 3;
        }
        await Task.Delay(Timeout.InfiniteTimeSpan);
        return 0;
    }

    private static bool MatchesStage(JsonElement root, string expected)
    {
        foreach (var property in new[] { "stage", "stage_id" })
        {
            if (root.TryGetProperty(property, out var value)
                && string.Equals(
                    value.GetString(),
                    expected,
                    StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    private static async Task<IReadOnlyList<int>> WaitForMembersAsync(
        JobProcess process,
        int minimum)
    {
        var deadline = Stopwatch.GetTimestamp() + (5 * Stopwatch.Frequency);
        IReadOnlyList<int> members;
        do
        {
            members = process.ActiveProcessIds();
            if (members.Count >= minimum)
            {
                return members;
            }
            await Task.Delay(25);
        }
        while (Stopwatch.GetTimestamp() < deadline);
        return members;
    }

    private static async Task<IReadOnlyList<int>> WaitForNoMembersAsync(
        JobProcess process)
    {
        var deadline = Stopwatch.GetTimestamp() + (5 * Stopwatch.Frequency);
        IReadOnlyList<int> members;
        do
        {
            members = process.ActiveProcessIds();
            if (members.Count == 0)
            {
                return members;
            }
            await Task.Delay(25);
        }
        while (Stopwatch.GetTimestamp() < deadline);
        return members;
    }

    private static async Task DrainAsync(StreamReader reader)
    {
        while (await reader.ReadLineAsync() is not null)
        {
        }
    }

    private static async Task WriteEvidenceAsync(string path, object payload)
    {
        var parent = Path.GetDirectoryName(Path.GetFullPath(path));
        if (parent is not null)
        {
            Directory.CreateDirectory(parent);
        }
        await File.WriteAllTextAsync(
            path,
            JsonSerializer.Serialize(
                payload,
                new JsonSerializerOptions { WriteIndented = true })
            + Environment.NewLine,
            new System.Text.UTF8Encoding(false));
    }

    private sealed class OptionSet
    {
        private readonly Dictionary<string, string?> _values;

        private OptionSet(
            Dictionary<string, string?> values,
            IReadOnlyList<string> command)
        {
            _values = values;
            Command = command;
        }

        internal IReadOnlyList<string> Command { get; }

        internal static OptionSet Parse(string[] args, bool allowCommand)
        {
            var values = new Dictionary<string, string?>(StringComparer.Ordinal);
            var command = new List<string>();
            for (var index = 0; index < args.Length; index++)
            {
                var argument = args[index];
                if (allowCommand && argument == "--")
                {
                    command.AddRange(args[(index + 1)..]);
                    break;
                }
                if (!argument.StartsWith("--", StringComparison.Ordinal))
                {
                    throw new ArgumentException("An option name was expected.");
                }
                var name = argument[2..];
                if (name is "root-control"
                    or "normal-completion"
                    or "interactive-cancel")
                {
                    values[name] = null;
                    continue;
                }
                if (++index >= args.Length)
                {
                    throw new ArgumentException($"Option --{name} requires a value.");
                }
                values[name] = args[index];
            }
            return new OptionSet(values, command);
        }

        internal bool HasFlag(string name)
        {
            return _values.TryGetValue(name, out var value) && value is null;
        }

        internal string? Optional(string name)
        {
            return _values.GetValueOrDefault(name);
        }

        internal int OptionalInt(string name, int defaultValue)
        {
            var value = Optional(name);
            return value is null
                ? defaultValue
                : int.Parse(value, System.Globalization.CultureInfo.InvariantCulture);
        }

        internal string RequiredPath(string name)
        {
            return Optional(name)
                ?? throw new ArgumentException($"Option --{name} is required.");
        }
    }
}
