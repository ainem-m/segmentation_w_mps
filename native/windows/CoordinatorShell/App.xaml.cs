using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

public partial class App : Application
{
    private async void OnStartup(object sender, StartupEventArgs e)
    {
        try
        {
            var options = LaunchOptions.Parse(e.Args);
            if (options.CapturePath is not null
                || options.EvidenceRunPath is not null
                || options.EvidenceCancelPath is not null
                || options.EvidenceDicomPath is not null
                || options.EvidenceDentalSegmentatorPath is not null)
            {
                RenderOptions.ProcessRenderMode = RenderMode.SoftwareOnly;
            }
            var configuration = ShellConfiguration.Load(
                options.EngineeringConfigurationPath);
            if (options.ContractSelfTest)
            {
                var window = new MainWindow(configuration, "start");
                var ui = window.UiContractEvidence();
                var parserPassed = CoordinatorEvent.ContractSelfTest();
                var passed = ui.Passed && parserPassed;
                var payload = new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_contract_self_test.v1",
                    status = passed ? "pass" : "fail",
                    coordinator_event_parser = parserPassed,
                    automation_names = ui.AutomationNames,
                    keyboard_focusable = ui.KeyboardFocusable,
                    dynamic_system_colors = ui.DynamicSystemColors,
                    dynamic_labels = ui.DynamicLabels,
                    button_count = ui.ButtonCount,
                    per_monitor_v2_manifest = true,
                    long_path_aware_manifest = true,
                    external_ui_automation = "unverified",
                };
                if (options.ContractEvidencePath is not null)
                {
                    await WriteJsonAsync(
                        options.ContractEvidencePath,
                        payload);
                }
                Console.WriteLine(JsonSerializer.Serialize(payload));
                Shutdown(passed ? 0 : 1);
                return;
            }

            var mainWindow = new MainWindow(
                configuration,
                options.PreviewScenario);
            if (options.CapturePath is not null
                || options.EvidenceRunPath is not null
                || options.EvidenceCancelPath is not null
                || options.EvidenceDicomPath is not null
                || options.EvidenceDentalSegmentatorPath is not null)
            {
                TextOptions.SetTextRenderingMode(
                    mainWindow,
                    TextRenderingMode.Grayscale);
            }
            MainWindow = mainWindow;
            mainWindow.Show();
            if (options.CapturePath is not null)
            {
                await CaptureAfterRenderAsync(
                    mainWindow,
                    options.CapturePath);
                mainWindow.Close();
                return;
            }
            if (options.EvidenceRunPath is not null)
            {
                var passed = await mainWindow.RunEvidenceSampleAsync(
                    options.EvidenceRunPath);
                var parent = Path.GetDirectoryName(
                    Path.GetFullPath(options.EvidenceRunPath));
                if (parent is not null)
                {
                    await CaptureAfterRenderAsync(
                        mainWindow,
                        Path.Combine(parent, "wpf-real-result.png"));
                }
                Shutdown(passed ? 0 : 1);
                return;
            }
            if (options.EvidenceCancelPath is not null)
            {
                var passed = await mainWindow.RunEvidenceCancelSampleAsync(
                    options.EvidenceCancelPath);
                var parent = Path.GetDirectoryName(
                    Path.GetFullPath(options.EvidenceCancelPath));
                if (parent is not null)
                {
                    await CaptureAfterRenderAsync(
                        mainWindow,
                        Path.Combine(parent, "wpf-real-cancel-result.png"));
                }
                Shutdown(passed ? 0 : 1);
                return;
            }
            if (options.EvidenceDentalSegmentatorPath is not null)
            {
                var passed =
                    await mainWindow
                        .RunEvidenceDentalSegmentatorAsync(
                            options.EvidenceDentalSegmentatorPath);
                var parent = Path.GetDirectoryName(
                    Path.GetFullPath(
                        options.EvidenceDentalSegmentatorPath));
                if (parent is not null)
                {
                    await CaptureAfterRenderAsync(
                        mainWindow,
                        Path.Combine(
                            parent,
                            "wpf-dentalseg-result.png"));
                }
                Shutdown(passed ? 0 : 1);
                return;
            }
            if (options.EvidenceDicomPath is not null
                && options.EvidenceDicomFolder is not null)
            {
                var passed = await mainWindow.RunEvidenceDicomAsync(
                    options.EvidenceDicomFolder,
                    options.EvidenceDicomPath);
                var parent = Path.GetDirectoryName(
                    Path.GetFullPath(options.EvidenceDicomPath));
                if (parent is not null)
                {
                    await CaptureAfterRenderAsync(
                        mainWindow,
                        Path.Combine(parent, "wpf-dicom-result.png"));
                }
                Shutdown(passed ? 0 : 1);
            }
        }
        catch (Exception exception) when (
            exception is ArgumentException
                or InvalidDataException
                or IOException
                or UnauthorizedAccessException)
        {
            Console.Error.WriteLine(
                $"windows shell diagnostic: {exception.GetType().Name}");
            Shutdown(2);
        }
    }

    private async Task CaptureAfterRenderAsync(
        MainWindow window,
        string outputPath)
    {
        await Dispatcher.InvokeAsync(
            window.UpdateLayout,
            DispatcherPriority.ContextIdle);
        await Task.Delay(TimeSpan.FromMilliseconds(150));
        await Dispatcher.InvokeAsync(
            () => window.CapturePng(outputPath),
            DispatcherPriority.ApplicationIdle);
    }

    private static async Task WriteJsonAsync(string path, object payload)
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

    private sealed record LaunchOptions(
        string? EngineeringConfigurationPath,
        string? PreviewScenario,
        string? CapturePath,
        bool ContractSelfTest,
        string? ContractEvidencePath,
        string? EvidenceRunPath,
        string? EvidenceCancelPath,
        string? EvidenceDentalSegmentatorPath,
        string? EvidenceDicomFolder,
        string? EvidenceDicomPath)
    {
        internal static LaunchOptions Parse(string[] args)
        {
            string? engineeringConfigurationPath = null;
            string? previewScenario = null;
            string? capturePath = null;
            string? contractEvidencePath = null;
            string? evidenceRunPath = null;
            string? evidenceCancelPath = null;
            string? evidenceDentalSegmentatorPath = null;
            string? evidenceDicomFolder = null;
            string? evidenceDicomPath = null;
            var contractSelfTest = false;
            for (var index = 0; index < args.Length; index++)
            {
                switch (args[index])
                {
                    case "--engineering-config":
                        engineeringConfigurationPath = RequiredValue(
                            args,
                            ref index,
                            "--engineering-config");
                        break;
                    case "--ui-preview":
                        previewScenario = RequiredValue(
                            args,
                            ref index,
                            "--ui-preview");
                        break;
                    case "--capture-ui":
                        previewScenario = RequiredValue(
                            args,
                            ref index,
                            "--capture-ui");
                        capturePath = RequiredValue(
                            args,
                            ref index,
                            "--capture-ui");
                        if (!Path.IsPathFullyQualified(capturePath))
                        {
                            throw new ArgumentException(
                                "--capture-ui requires an absolute output path.");
                        }
                        break;
                    case "--contract-self-test":
                        contractSelfTest = true;
                        if (index + 1 < args.Length
                            && !args[index + 1].StartsWith(
                                "--",
                                StringComparison.Ordinal))
                        {
                            contractEvidencePath = args[++index];
                            if (!Path.IsPathFullyQualified(contractEvidencePath))
                            {
                                throw new ArgumentException(
                                    "The contract evidence path must be absolute.");
                            }
                        }
                        break;
                    case "--evidence-run-sample":
                        evidenceRunPath = RequiredValue(
                            args,
                            ref index,
                            "--evidence-run-sample");
                        if (!Path.IsPathFullyQualified(evidenceRunPath))
                        {
                            throw new ArgumentException(
                                "The evidence path must be absolute.");
                        }
                        break;
                    case "--evidence-cancel-sample":
                        evidenceCancelPath = RequiredValue(
                            args,
                            ref index,
                            "--evidence-cancel-sample");
                        if (!Path.IsPathFullyQualified(evidenceCancelPath))
                        {
                            throw new ArgumentException(
                                "The cancellation evidence path must be absolute.");
                        }
                        break;
                    case "--evidence-run-dentalseg":
                        evidenceDentalSegmentatorPath =
                            RequiredValue(
                                args,
                                ref index,
                                "--evidence-run-dentalseg");
                        if (!Path.IsPathFullyQualified(
                                evidenceDentalSegmentatorPath))
                        {
                            throw new ArgumentException(
                                "The DentalSegmentator evidence path must be absolute.");
                        }
                        break;
                    case "--evidence-run-dicom":
                        evidenceDicomFolder = RequiredValue(
                            args,
                            ref index,
                            "--evidence-run-dicom");
                        evidenceDicomPath = RequiredValue(
                            args,
                            ref index,
                            "--evidence-run-dicom");
                        if (!Path.IsPathFullyQualified(
                                evidenceDicomFolder)
                            || !Path.IsPathFullyQualified(
                                evidenceDicomPath))
                        {
                            throw new ArgumentException(
                                "The DICOM input and evidence paths must be absolute.");
                        }
                        break;
                    default:
                        throw new ArgumentException(
                            "The Windows shell option is not supported.");
                }
            }
            if (new[]
                {
                    capturePath,
                    evidenceRunPath,
                    evidenceCancelPath,
                    evidenceDentalSegmentatorPath,
                    evidenceDicomPath,
                }.Count(value => value is not null) > 1)
            {
                throw new ArgumentException(
                    "UI capture and a real evidence run cannot be combined.");
            }
            if (contractSelfTest
                && (capturePath is not null
                    || evidenceRunPath is not null
                    || evidenceCancelPath is not null
                    || evidenceDentalSegmentatorPath is not null
                    || evidenceDicomPath is not null))
            {
                throw new ArgumentException(
                    "The contract self-test cannot be combined with another mode.");
            }
            return new LaunchOptions(
                engineeringConfigurationPath,
                previewScenario,
                capturePath,
                contractSelfTest,
                contractEvidencePath,
                evidenceRunPath,
                evidenceCancelPath,
                evidenceDentalSegmentatorPath,
                evidenceDicomFolder,
                evidenceDicomPath);
        }

        private static string RequiredValue(
            string[] args,
            ref int index,
            string option)
        {
            if (++index >= args.Length)
            {
                throw new ArgumentException(
                    $"{option} requires a value.");
            }
            return args[index];
        }
    }
}
