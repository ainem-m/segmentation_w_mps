using Microsoft.Win32.SafeHandles;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace TotalSegmentatorWrapper.Windows.ProcessSupervisor;

internal sealed class JobProcess : IDisposable
{
    private readonly nint _job;
    private readonly nint _process;
    private readonly nint _thread;
    private bool _disposed;

    private JobProcess(
        nint job,
        NativeMethods.ProcessInformation processInformation,
        FileStream standardInput,
        FileStream standardOutput,
        FileStream standardError)
    {
        _job = job;
        _process = processInformation.Process;
        _thread = processInformation.Thread;
        ProcessId = checked((int)processInformation.ProcessId);
        StandardInput = new StreamWriter(standardInput, new UTF8Encoding(false))
        {
            AutoFlush = true,
        };
        StandardOutput = new StreamReader(
            standardOutput,
            new UTF8Encoding(false),
            detectEncodingFromByteOrderMarks: true);
        StandardError = new StreamReader(
            standardError,
            new UTF8Encoding(false),
            detectEncodingFromByteOrderMarks: true);
    }

    internal int ProcessId { get; }

    internal StreamWriter StandardInput { get; }

    internal StreamReader StandardOutput { get; }

    internal StreamReader StandardError { get; }

    internal static JobProcess StartSuspended(
        string executable,
        IReadOnlyList<string> arguments,
        string workingDirectory)
    {
        var securityAttributes = new NativeMethods.SecurityAttributes
        {
            Length = Marshal.SizeOf<NativeMethods.SecurityAttributes>(),
            InheritHandle = true,
        };
        CreatePipePair(
            ref securityAttributes,
            parentReads: false,
            out var childStandardInput,
            out var parentStandardInput);
        CreatePipePair(
            ref securityAttributes,
            parentReads: true,
            out var childStandardOutput,
            out var parentStandardOutput);
        CreatePipePair(
            ref securityAttributes,
            parentReads: true,
            out var childStandardError,
            out var parentStandardError);

        nint job = 0;
        NativeMethods.ProcessInformation processInformation = default;
        try
        {
            job = NativeMethods.CreateJobObjectW(0, null);
            EnsureHandle(job, "CreateJobObjectW");
            var limits = new NativeMethods.JobObjectExtendedLimitInformation
            {
                BasicLimitInformation = new NativeMethods.JobObjectBasicLimitInformation
                {
                    LimitFlags = NativeMethods.JobObjectLimitKillOnJobClose,
                },
            };
            if (!NativeMethods.SetInformationJobObject(
                    job,
                NativeMethods.JobObjectExtendedLimitInformationClass,
                    ref limits,
                    Marshal.SizeOf<NativeMethods.JobObjectExtendedLimitInformation>()))
            {
                throw LastError("SetInformationJobObject");
            }

            var startupInfo = new NativeMethods.StartupInfo
            {
                Size = Marshal.SizeOf<NativeMethods.StartupInfo>(),
                Flags = 0x00000100,
                StandardInput = childStandardInput,
                StandardOutput = childStandardOutput,
                StandardError = childStandardError,
            };
            var commandLine = new StringBuilder(BuildCommandLine(executable, arguments));
            if (!NativeMethods.CreateProcessW(
                    executable,
                    commandLine,
                    0,
                    0,
                    inheritHandles: true,
                    NativeMethods.CreateSuspended | NativeMethods.CreateNoWindow,
                    0,
                    workingDirectory,
                    ref startupInfo,
                    out processInformation))
            {
                throw LastError("CreateProcessW");
            }
            if (!NativeMethods.AssignProcessToJobObject(job, processInformation.Process))
            {
                throw LastError("AssignProcessToJobObject");
            }

            CloseIfValid(childStandardInput);
            childStandardInput = 0;
            CloseIfValid(childStandardOutput);
            childStandardOutput = 0;
            CloseIfValid(childStandardError);
            childStandardError = 0;

            return new JobProcess(
                job,
                processInformation,
                Wrap(parentStandardInput, FileAccess.Write),
                Wrap(parentStandardOutput, FileAccess.Read),
                Wrap(parentStandardError, FileAccess.Read));
        }
        catch
        {
            if (processInformation.Process != 0)
            {
                NativeMethods.TerminateProcess(processInformation.Process, 25);
                NativeMethods.WaitForSingleObject(
                    processInformation.Process,
                    5000);
            }
            CloseIfValid(processInformation.Thread);
            CloseIfValid(processInformation.Process);
            CloseIfValid(job);
            CloseIfValid(parentStandardInput);
            CloseIfValid(parentStandardOutput);
            CloseIfValid(parentStandardError);
            throw;
        }
        finally
        {
            CloseIfValid(childStandardInput);
            CloseIfValid(childStandardOutput);
            CloseIfValid(childStandardError);
        }
    }

    internal void Resume()
    {
        var previousSuspendCount = NativeMethods.ResumeThread(_thread);
        if (previousSuspendCount == uint.MaxValue)
        {
            throw LastError("ResumeThread");
        }
    }

    internal bool WaitForExit(TimeSpan timeout)
    {
        var milliseconds = timeout == Timeout.InfiniteTimeSpan
            ? uint.MaxValue
            : checked((uint)Math.Clamp(timeout.TotalMilliseconds, 0, uint.MaxValue - 1));
        var result = NativeMethods.WaitForSingleObject(_process, milliseconds);
        return result switch
        {
            NativeMethods.WaitObject0 => true,
            NativeMethods.WaitTimeout => false,
            _ => throw LastError("WaitForSingleObject"),
        };
    }

    internal uint? ExitCode
    {
        get
        {
            if (!NativeMethods.GetExitCodeProcess(_process, out var exitCode))
            {
                throw LastError("GetExitCodeProcess");
            }
            return exitCode == NativeMethods.StillActive ? null : exitCode;
        }
    }

    internal IReadOnlyList<int> ActiveProcessIds()
    {
        const int capacity = 256;
        var bytes = checked(8 + (capacity * IntPtr.Size));
        var buffer = Marshal.AllocHGlobal(bytes);
        try
        {
            if (!NativeMethods.QueryInformationJobObject(
                    _job,
                    NativeMethods.JobObjectBasicProcessIdList,
                    buffer,
                    bytes,
                    out _))
            {
                throw LastError("QueryInformationJobObject");
            }
            var assigned = Marshal.ReadInt32(buffer, 0);
            if (assigned > capacity)
            {
                throw new InvalidOperationException("The job process list exceeded its fixed capacity.");
            }
            var active = Marshal.ReadInt32(buffer, 4);
            var result = new List<int>(active);
            for (var index = 0; index < active; index++)
            {
                var offset = checked(8 + (index * IntPtr.Size));
                result.Add(checked((int)Marshal.ReadIntPtr(buffer, offset)));
            }
            return result;
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    internal void Terminate(uint exitCode)
    {
        if (!NativeMethods.TerminateJobObject(_job, exitCode))
        {
            throw LastError("TerminateJobObject");
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        try
        {
            StandardInput.Dispose();
            StandardOutput.Dispose();
            StandardError.Dispose();
        }
        finally
        {
            CloseIfValid(_thread);
            CloseIfValid(_process);
            CloseIfValid(_job);
        }
    }

    private static void CreatePipePair(
        ref NativeMethods.SecurityAttributes securityAttributes,
        bool parentReads,
        out nint childHandle,
        out nint parentHandle)
    {
        if (!NativeMethods.CreatePipe(
                out var readPipe,
                out var writePipe,
                ref securityAttributes,
                0))
        {
            throw LastError("CreatePipe");
        }
        childHandle = parentReads ? writePipe : readPipe;
        parentHandle = parentReads ? readPipe : writePipe;
        if (!NativeMethods.SetHandleInformation(
                parentHandle,
                NativeMethods.HandleFlagInherit,
                0))
        {
            CloseIfValid(readPipe);
            CloseIfValid(writePipe);
            throw LastError("SetHandleInformation");
        }
    }

    private static FileStream Wrap(nint handle, FileAccess access)
    {
        return new FileStream(
            new SafeFileHandle(handle, ownsHandle: true),
            access,
            bufferSize: 4096,
            isAsync: false);
    }

    private static string BuildCommandLine(
        string executable,
        IReadOnlyList<string> arguments)
    {
        return string.Join(
            " ",
            new[] { QuoteArgument(executable) }.Concat(arguments.Select(QuoteArgument)));
    }

    private static string QuoteArgument(string value)
    {
        if (value.Length > 0 && !value.Any(character => char.IsWhiteSpace(character) || character == '"'))
        {
            return value;
        }
        var result = new StringBuilder("\"");
        var backslashes = 0;
        foreach (var character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }
            if (character == '"')
            {
                result.Append('\\', (backslashes * 2) + 1);
                result.Append('"');
                backslashes = 0;
                continue;
            }
            result.Append('\\', backslashes);
            backslashes = 0;
            result.Append(character);
        }
        result.Append('\\', backslashes * 2);
        result.Append('"');
        return result.ToString();
    }

    private static void EnsureHandle(nint handle, string operation)
    {
        if (handle == 0 || handle == -1)
        {
            throw LastError(operation);
        }
    }

    private static Win32Exception LastError(string operation)
    {
        var error = Marshal.GetLastWin32Error();
        return new Win32Exception(error, $"{operation} failed with Win32 error {error}.");
    }

    private static void CloseIfValid(nint handle)
    {
        if (handle != 0 && handle != -1)
        {
            NativeMethods.CloseHandle(handle);
        }
    }
}
