using Microsoft.Win32;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal enum ShellScreen
{
    Setup,
    Start,
    Input,
    DicomRescue,
    Running,
    Result,
}

public partial class MainWindow : Window
{
    private readonly ShellConfiguration _configuration;
    private readonly DispatcherTimer _elapsedTimer;
    private readonly List<string> _safeEventLog = [];
    private CoordinatorSession? _session;
    private CoordinatorSessionResult? _lastResult;
    private string? _inputPath;
    private DateTime _runStartedUtc;
    private ShellScreen _screen;

    internal MainWindow(
        ShellConfiguration configuration,
        string? previewScenario = null)
    {
        _configuration = configuration;
        InitializeComponent();
        _elapsedTimer = new DispatcherTimer(
            TimeSpan.FromSeconds(1),
            DispatcherPriority.Background,
            (_, _) => UpdateElapsed(),
            Dispatcher);
        Closed += (_, _) =>
        {
            _dicomSession?.Dispose();
            _session?.Dispose();
        };
        OutputDisplayName.Text =
            $"保存先: {Path.GetFileName(_configuration.OutputRoot)}";
        InitializeModelSelection();
        SetScreen(ShellScreen.Setup, "待機中");
        if (previewScenario is not null)
        {
            ApplyPreviewScenario(previewScenario);
        }
    }

    internal UiContractResult UiContractEvidence()
    {
        SetInputButtonsForSample(sampleSelected: true);
        SetStopButtonRequested(requested: false);
        SetDetailsExpanded(expanded: false);
        SetCopyInformationButtonForCancellation(cancelled: false);
        var expectedNames = new Dictionary<Button, string>
        {
            [PrepareButton] = "準備を始める",
            [SampleChoiceButton] = "Sampleから始める",
            [OwnDataChoiceButton] = "手元のCTデータを使う",
            [ChooseInputButton] = "手元のCTを選ぶ",
            [ChooseNiftiButton] = "NIfTIファイルを選ぶ",
            [ChooseDicomFolderButton] = "DICOMフォルダを選ぶ",
            [ChangeDicomSeriesButton] = "使用する撮影を変更",
            [UseSelectedDicomSeriesButton] = "この撮影を使う",
            [CloseDicomSeriesButton] = "撮影選択を閉じる",
            [ShowRescueReasonButton] = "形状候補の理由を見る",
            [ChooseAnotherCtFromRescueButton] =
                "形状確認から別のCTを選ぶ",
            [ResetRescueSpacingButton] = "推定形状に戻す",
            [CreateRescuePreviewButton] =
                "この形状で確認画像を作る",
            [StandardModelCardButton] = "標準モデルを選ぶ",
            [OtherModelsCardButton] = "その他のモデルを比較",
            [CloseModelComparisonButton] =
                "作成方法の比較を閉じる",
            [SelectStandardModelButton] =
                "通常のTotalSegmentatorを選択中",
            [SelectDentalSegmentatorButton] =
                "DentalSegmentatorを選ぶ",
            [SelectIndividualTeethButton] =
                "個別歯ベータを選ぶ",
            [SelectToothSegButton] =
                "高精細歯ToothSegを選ぶ",
            [RunButton] = "Sampleで3Dプレビューを作る",
            [StopButton] = "停止",
            [OpenPreviewButton] = "3Dプレビューを開く",
            [OpenOutputButton] = "結果フォルダを開く",
            [CopyErrorButton] = "エラー情報をコピー",
            [ShowDetailsButton] = "詳細情報を見る",
            [ReturnToInputButton] = "入力と作成内容へ戻る",
            [ReturnToStartButton] = "最初に戻る",
        };
        var namesPassed = expectedNames.All(
            pair => AutomationProperties.GetName(pair.Key) == pair.Value);
        var keyboardPassed = expectedNames.Keys.All(
            button => button.Focusable && KeyboardNavigation.GetIsTabStop(button));
        SetInputButtonsForSample(sampleSelected: false);
        var ownInputLabelPassed =
            Equals(ChooseInputButton.Content, "別のCTを選ぶ")
            && AutomationProperties.GetName(ChooseInputButton)
                == "別のCTを選ぶ"
            && Equals(RunButton.Content, "このCTで3Dプレビューを作る")
            && AutomationProperties.GetName(RunButton)
                == "このCTで3Dプレビューを作る";
        SetInputButtonsForSample(sampleSelected: true);
        SetDetailsExpanded(expanded: true);
        var detailsExpandedLabelPassed =
            Equals(ShowDetailsButton.Content, "詳細情報を閉じる")
            && AutomationProperties.GetName(ShowDetailsButton)
                == "詳細情報を閉じる";
        SetDetailsExpanded(expanded: false);
        SetStopButtonRequested(requested: true);
        var stopRequestedLabelPassed =
            Equals(
                StopButton.Content,
                "停止要求済み。終了処理中です。")
            && AutomationProperties.GetName(StopButton)
                == "停止要求済み。終了処理中です。";
        SetStopButtonRequested(requested: false);
        SetCopyInformationButtonForCancellation(cancelled: true);
        var cancellationCopyLabelPassed =
            Equals(CopyErrorButton.Content, "停止情報をコピー")
            && AutomationProperties.GetName(CopyErrorButton)
                == "停止情報をコピー";
        SetCopyInformationButtonForCancellation(cancelled: false);
        SetInputButtonsForNoInput();
        var noInputLabelPassed =
            Equals(ChooseInputButton.Content, "CTデータを選ぶ")
            && AutomationProperties.GetName(ChooseInputButton)
                == "CTデータを選ぶ";
        SetInputButtonsForSample(sampleSelected: true);
        SetReturnToInputButtonForDicomFailure(dicomFailure: true);
        var dicomRecoveryLabelPassed =
            Equals(ReturnToInputButton.Content, "別のCTを選ぶ")
            && AutomationProperties.GetName(ReturnToInputButton)
                == "別のCTを選ぶ";
        SetReturnToInputButtonForDicomFailure(dicomFailure: false);
        ShowRescueReasonButton_Click(
            ShowRescueReasonButton,
            new RoutedEventArgs());
        var rescueReasonExpandedLabelPassed =
            Equals(ShowRescueReasonButton.Content, "理由を閉じる")
            && AutomationProperties.GetName(
                    ShowRescueReasonButton)
                == "形状候補の理由を閉じる";
        ShowRescueReasonButton_Click(
            ShowRescueReasonButton,
            new RoutedEventArgs());
        SetSelectedSegmentationProfile(
            SegmentationProfile.DentalSegmentator);
        var dentalModelLabelPassed =
            Equals(
                OtherModelsCardName.Text,
                "選択中：DentalSegmentator（実験的）")
            && Equals(
                SelectDentalSegmentatorButton.Content,
                "選択中")
            && AutomationProperties.GetName(
                    SelectDentalSegmentatorButton)
                == "DentalSegmentatorを選択中"
            && AutomationProperties.GetHelpText(RunButton)
                .Contains(
                    "DentalSegmentator",
                    StringComparison.Ordinal);
        SetSelectedSegmentationProfile(
            SegmentationProfile.IndividualTeeth);
        var individualTeethLabelPassed =
            Equals(
                OtherModelsCardName.Text,
                "選択中：個別歯ベータ")
            && Equals(
                SelectIndividualTeethButton.Content,
                "選択中")
            && AutomationProperties.GetName(
                    SelectIndividualTeethButton)
                == "個別歯ベータを選択中"
            && AutomationProperties.GetHelpText(RunButton)
                .Contains(
                    "個別歯ベータ",
                    StringComparison.Ordinal);
        SetSelectedSegmentationProfile(
            SegmentationProfile.ToothSeg);
        var toothSegLabelPassed =
            Equals(
                OtherModelsCardName.Text,
                "選択中：高精細歯（ToothSeg）")
            && Equals(
                SelectToothSegButton.Content,
                "選択中")
            && AutomationProperties.GetName(
                    SelectToothSegButton)
                == "高精細歯ToothSegを選択中"
            && AutomationProperties.GetHelpText(RunButton)
                .Contains(
                    "ToothSeg",
                    StringComparison.Ordinal);
        SetSelectedSegmentationProfile(
            SegmentationProfile.TotalSegmentator);
        var rescueSlidersLinked =
            RescueSliderContractSelfTest();
        var dynamicLabelsPassed =
            ownInputLabelPassed
            && detailsExpandedLabelPassed
            && stopRequestedLabelPassed
            && cancellationCopyLabelPassed
            && noInputLabelPassed
            && dicomRecoveryLabelPassed
            && rescueReasonExpandedLabelPassed
            && dentalModelLabelPassed
            && individualTeethLabelPassed
            && toothSegLabelPassed;
        var systemColorsPassed =
            Equals(PrepareButton.Background, SystemColors.HighlightBrush)
            && Equals(Background, SystemColors.WindowBrush);
        return new UiContractResult(
            namesPassed
                && keyboardPassed
                && dynamicLabelsPassed
                && rescueSlidersLinked
                && systemColorsPassed,
            namesPassed,
            keyboardPassed,
            systemColorsPassed,
            dynamicLabelsPassed,
            rescueSlidersLinked,
            expectedNames.Count);
    }

    internal void CapturePng(string outputPath)
    {
        UpdateLayout();
        var dpi = VisualTreeHelper.GetDpi(this);
        var width = Math.Max(1, (int)Math.Ceiling(ActualWidth * dpi.DpiScaleX));
        var height = Math.Max(1, (int)Math.Ceiling(ActualHeight * dpi.DpiScaleY));
        var bitmap = new RenderTargetBitmap(
            width,
            height,
            dpi.PixelsPerInchX,
            dpi.PixelsPerInchY,
            PixelFormats.Pbgra32);
        bitmap.Render(this);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        var parent = Path.GetDirectoryName(Path.GetFullPath(outputPath));
        if (parent is not null)
        {
            Directory.CreateDirectory(parent);
        }
        using var stream = File.Create(outputPath);
        encoder.Save(stream);
    }

    internal async Task<bool> RunEvidenceSampleAsync(string evidencePath)
    {
        var runtime = _configuration.CheckRuntime();
        if (!runtime.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_evidence_run.v1",
                    status = "fail",
                    error_code = runtime.ErrorCode,
                    terminal_event = (string?)null,
                });
            return false;
        }
        SelectBundledSample();
        await StartRunAsync();
        var result = _lastResult;
        var dpi = VisualTreeHelper.GetDpi(this);
        var outputExists =
            result is not null && Directory.Exists(result.FinalDirectory);
        var previewExists =
            result is not null
            && File.Exists(
                Path.Combine(
                    result.FinalDirectory,
                    "surface_preview",
                    "index.html"));
        var manifestExists =
            result is not null
            && File.Exists(
                Path.Combine(result.FinalDirectory, "artifact-manifest.json"));
        var passed =
            result?.TerminalEvent == "operation_completed"
            && result.SupervisorExitCode == 0
            && result.RequestedPolicy == "cuda_required"
            && result.RequestedDeviceIndex == 0
            && result.ResolvedDevice == "cuda:0"
            && result.FallbackAllowed == false
            && result.FallbackOccurred == false
            && outputExists
            && previewExists
            && manifestExists;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_evidence_run.v1",
                status = passed ? "pass" : "fail",
                operation_id = result?.OperationId,
                terminal_event = result?.TerminalEvent,
                supervisor_exit_code = result?.SupervisorExitCode,
                requested_policy = result?.RequestedPolicy,
                requested_device_index = result?.RequestedDeviceIndex,
                resolved_device = result?.ResolvedDevice,
                fallback_allowed = result?.FallbackAllowed,
                fallback_occurred = result?.FallbackOccurred,
                output_promoted = outputExists,
                offline_preview_exists = previewExists,
                artifact_manifest_exists = manifestExists,
                output_directory_name =
                    result is null ? null : Path.GetFileName(result.FinalDirectory),
                ui_screen = _screen.ToString().ToLowerInvariant(),
                ui_status = StatusPillText.Text,
                ui_dpi_x = dpi.PixelsPerInchX,
                ui_dpi_y = dpi.PixelsPerInchY,
                system_high_contrast = SystemParameters.HighContrast,
                error_code = result?.ErrorCode,
                safe_reason = result?.SafeReason,
            });
        return passed;
    }

    internal async Task<bool> RunEvidenceCancelSampleAsync(string evidencePath)
    {
        var runtime = _configuration.CheckRuntime();
        if (!runtime.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_cancel_evidence.v1",
                    status = "fail",
                    error_code = runtime.ErrorCode,
                    terminal_event = (string?)null,
                });
            return false;
        }

        SelectBundledSample();
        var runTask = StartRunAsync();
        var cancelTriggerReached = false;
        for (var attempt = 0; attempt < 1200; attempt++)
        {
            if (_safeEventLog.Any(
                    line => line.Contains(
                        "progress segment",
                        StringComparison.Ordinal)))
            {
                cancelTriggerReached = true;
                break;
            }
            if (runTask.IsCompleted)
            {
                break;
            }
            await Task.Delay(100);
        }
        if (!runTask.IsCompleted)
        {
            await RequestStopAsync();
        }
        await runTask;

        var result = _lastResult;
        var finalExists =
            result is not null && Directory.Exists(result.FinalDirectory);
        var stagingExists =
            result is not null
            && Directory.Exists(
                Path.Combine(
                    _configuration.OutputRoot,
                    $".tswm-{result.OperationId}.staging"));
        var supervisorEvidenceExists =
            result is not null
            && File.Exists(
                Path.Combine(
                    result.HostEvidenceDirectory,
                    "supervisor-evidence.json"));
        var requestRemains =
            result is not null
            && File.Exists(
                Path.Combine(
                    result.HostEvidenceDirectory,
                    "request.json"));
        var passed =
            cancelTriggerReached
            && result?.TerminalEvent == "operation_cancelled"
            && result.SupervisorExitCode == 0
            && result.RequestedPolicy == "cuda_required"
            && result.RequestedDeviceIndex == 0
            && result.ResolvedDevice == "cuda:0"
            && result.FallbackAllowed == false
            && result.FallbackOccurred == false
            && !finalExists
            && supervisorEvidenceExists
            && !requestRemains;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_cancel_evidence.v1",
                status = passed ? "pass" : "fail",
                operation_id = result?.OperationId,
                cancel_trigger_reached = cancelTriggerReached,
                terminal_event = result?.TerminalEvent,
                supervisor_exit_code = result?.SupervisorExitCode,
                requested_policy = result?.RequestedPolicy,
                requested_device_index = result?.RequestedDeviceIndex,
                resolved_device = result?.ResolvedDevice,
                fallback_allowed = result?.FallbackAllowed,
                fallback_occurred = result?.FallbackOccurred,
                final_output_promoted = finalExists,
                staging_exists = stagingExists,
                supervisor_evidence_exists = supervisorEvidenceExists,
                transient_request_deleted = !requestRemains,
                ui_screen = _screen.ToString().ToLowerInvariant(),
                ui_status = StatusPillText.Text,
                error_code = result?.ErrorCode,
                reason_code = result?.ReasonCode,
            });
        return passed;
    }

    private void PrepareButton_Click(object sender, RoutedEventArgs e)
    {
        var result = _configuration.CheckRuntime();
        RuntimeMessage.Text = result.Message;
        if (!result.Passed)
        {
            RuntimeErrorCode.Text = $"error_code={result.ErrorCode}";
            RuntimeErrorCode.Visibility = Visibility.Visible;
            RuntimeRecoveryMessage.Text =
                result.RecoveryMessage ?? string.Empty;
            RuntimeRecoveryMessage.Visibility =
                string.IsNullOrWhiteSpace(result.RecoveryMessage)
                    ? Visibility.Collapsed
                    : Visibility.Visible;
            StatusPillText.Text = "準備できません";
            return;
        }
        RuntimeErrorCode.Visibility = Visibility.Collapsed;
        RuntimeRecoveryMessage.Visibility = Visibility.Collapsed;
        SetScreen(ShellScreen.Start, "準備済み");
    }

    private void SampleChoiceButton_Click(object sender, RoutedEventArgs e)
    {
        SelectBundledSample();
    }

    private void OwnDataChoiceButton_Click(object sender, RoutedEventArgs e)
    {
        BeginOwnDataSelection(clearExistingInput: true);
    }

    private void ChooseInputButton_Click(object sender, RoutedEventArgs e)
    {
        BeginOwnDataSelection(clearExistingInput: false);
    }

    private async void RunButton_Click(object sender, RoutedEventArgs e)
    {
        await StartRunAsync();
    }

    private async void StopButton_Click(object sender, RoutedEventArgs e)
    {
        await RequestStopAsync();
    }

    private async Task RequestStopAsync()
    {
        StopButton.IsEnabled = false;
        SetStopButtonRequested(requested: true);
        StatusPillText.Text = "停止要求済み";
        SubProgressText.Text = "終了処理中です。画面が切り替わるまで待ってください。";
        if (RequestDicomStop())
        {
            return;
        }
        if (_session is not null)
        {
            try
            {
                await _session.RequestCancelAsync();
            }
            catch (Exception exception) when (
                exception is IOException
                    or InvalidOperationException)
            {
                StatusPillText.Text = "終了状態を確認中";
            }
        }
    }

    private void OpenPreviewButton_Click(object sender, RoutedEventArgs e)
    {
        if (_lastResult is null)
        {
            return;
        }
        OpenLocalPath(
            Path.Combine(
                _lastResult.FinalDirectory,
                "surface_preview",
                "index.html"));
    }

    private void OpenOutputButton_Click(object sender, RoutedEventArgs e)
    {
        if (_lastResult is not null)
        {
            OpenLocalPath(_lastResult.FinalDirectory);
        }
    }

    private void CopyErrorButton_Click(object sender, RoutedEventArgs e)
    {
        if (TryCopyDicomFailure())
        {
            return;
        }
        if (_lastResult is null)
        {
            return;
        }
        var fallbackAllowed = _lastResult.FallbackAllowed is { } allowed
            ? allowed.ToString().ToLowerInvariant()
            : "unverified";
        var fallbackOccurred = _lastResult.FallbackOccurred is { } occurred
            ? occurred.ToString().ToLowerInvariant()
            : "unverified";
        if (_lastResult.TerminalEvent == "operation_cancelled")
        {
            Clipboard.SetText(
                string.Join(
                    Environment.NewLine,
                    "status=cancelled",
                    $"reason_code={_lastResult.ReasonCode ?? "cancel_requested"}",
                    $"operation_id={_lastResult.OperationId}",
                    $"fallback_allowed={fallbackAllowed}",
                    $"fallback_occurred={fallbackOccurred}"));
            return;
        }
        Clipboard.SetText(
            string.Join(
                Environment.NewLine,
                $"error_code={_lastResult.ErrorCode ?? "unknown"}",
                $"operation_id={_lastResult.OperationId}",
                $"fallback_allowed={fallbackAllowed}",
                $"fallback_occurred={fallbackOccurred}"));
    }

    private void ShowDetailsButton_Click(object sender, RoutedEventArgs e)
    {
        SetDetailsExpanded(
            SafeDetailsPanel.Visibility != Visibility.Visible);
    }

    private void SetInputButtonsForSample(bool sampleSelected)
    {
        var chooseLabel = sampleSelected
            ? "手元のCTを選ぶ"
            : "別のCTを選ぶ";
        ChooseInputButton.Content = chooseLabel;
        AutomationProperties.SetName(ChooseInputButton, chooseLabel);
        var runLabel = sampleSelected
            ? "Sampleで3Dプレビューを作る"
            : "このCTで3Dプレビューを作る";
        RunButton.Content = runLabel;
        AutomationProperties.SetName(RunButton, runLabel);
    }

    private void SetStopButtonRequested(bool requested)
    {
        var label = requested
            ? "停止要求済み。終了処理中です。"
            : "停止";
        StopButton.Content = label;
        AutomationProperties.SetName(StopButton, label);
    }

    private void SetDetailsExpanded(bool expanded)
    {
        SafeDetailsPanel.Visibility =
            expanded ? Visibility.Visible : Visibility.Collapsed;
        var label = expanded
            ? "詳細情報を閉じる"
            : "詳細情報を見る";
        ShowDetailsButton.Content = label;
        AutomationProperties.SetName(ShowDetailsButton, label);
    }

    private void SetCopyInformationButtonForCancellation(bool cancelled)
    {
        var label = cancelled
            ? "停止情報をコピー"
            : "エラー情報をコピー";
        CopyErrorButton.Content = label;
        AutomationProperties.SetName(CopyErrorButton, label);
    }

    private void ReturnToInputButton_Click(object sender, RoutedEventArgs e)
    {
        if (ReturnFromDicomFailure())
        {
            return;
        }
        SetScreen(ShellScreen.Input, "プレビュー作成準備完了");
    }

    private void ReturnToStartButton_Click(object sender, RoutedEventArgs e)
    {
        _inputPath = null;
        ClearDicomSelection();
        ResetModelSelection();
        SetScreen(ShellScreen.Start, "準備済み");
    }

    private void SelectBundledSample()
    {
        ClearDicomSelection();
        _inputPath = _configuration.BundledSamplePath;
        InputDisplayName.Text = "Sample 1";
        SetInputButtonsForSample(sampleSelected: true);
        RunButton.IsEnabled = true;
        SampleNotice.Visibility = Visibility.Visible;
        SetScreen(ShellScreen.Input, "プレビュー作成準備完了");
    }

    private void ChooseNiftiInput()
    {
        var dialog = new OpenFileDialog
        {
            Title = "NIfTI形式のCTデータを選ぶ",
            Filter =
                "NIfTI CT (*.nii;*.nii.gz)|*.nii;*.nii.gz|すべてのファイル (*.*)|*.*",
            Multiselect = false,
            CheckFileExists = true,
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }
        var lower = dialog.FileName.ToLowerInvariant();
        if (!lower.EndsWith(".nii", StringComparison.Ordinal)
            && !lower.EndsWith(".nii.gz", StringComparison.Ordinal))
        {
            MessageBox.Show(
                this,
                "NIfTI形式（.nii または .nii.gz）のCTを選んでください。",
                "入力を確認してください",
                MessageBoxButton.OK,
                MessageBoxImage.Information);
            return;
        }
        ClearDicomSelection();
        _inputPath = dialog.FileName;
        InputDisplayName.Text = Path.GetFileName(dialog.FileName);
        SetInputButtonsForSample(sampleSelected: false);
        RunButton.IsEnabled = true;
        SampleNotice.Visibility = Visibility.Collapsed;
        SetScreen(ShellScreen.Input, "プレビュー作成準備完了");
    }

    private async Task StartRunAsync()
    {
        if (_inputPath is null)
        {
            SetHostFailure(
                "input_not_selected",
                "NIfTI形式のCT入力を選んでください。");
            return;
        }
        var runtime = _configuration.CheckRuntime();
        if (!runtime.Passed)
        {
            SetHostFailure(
                runtime.ErrorCode ?? "runtime_unavailable",
                runtime.Message);
            return;
        }
        var modelRuntime = CheckSelectedModelRuntime();
        if (!modelRuntime.Passed)
        {
            SetHostFailure(
                modelRuntime.ErrorCode
                    ?? "dentalseg_prepare_required",
                modelRuntime.Message);
            return;
        }

        _session?.Dispose();
        _session = new CoordinatorSession(_configuration);
        _session.EventReceived += OnCoordinatorEvent;
        _lastResult = null;
        _lastDicomFailure = null;
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: false);
        _safeEventLog.Clear();
        SafeEventLogTextBox.Clear();
        SetDetailsExpanded(expanded: false);
        StopButton.IsEnabled = true;
        SetStopButtonRequested(requested: false);
        RunProgressBar.IsIndeterminate = true;
        RunProgressBar.Value = 0;
        RunningTitle.Text = "実行準備";
        RunningDetail.Text = "production coordinatorを開始しています。";
        OverallProgressText.Text = "全体: 工程を確認中";
        SubProgressText.Text = "処理を継続しています。";
        DeviceText.Text =
            $"使用機能: {SelectedModelDisplayName} / strict CUDA確認中";
        RunningOutputText.Text = "保存先: 新しいcase";
        _runStartedUtc = DateTime.UtcNow;
        UpdateElapsed();
        _elapsedTimer.Start();
        SetScreen(ShellScreen.Running, "処理中");

        try
        {
            _lastResult = await _session.RunAsync(
                _inputPath,
                _selectedSegmentationProfile);
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or InvalidOperationException
                or Win32Exception
                or JsonException)
        {
            _lastResult = CoordinatorSessionResult.HostFailure(
                _session.ActiveOperationId ?? Guid.NewGuid().ToString("D"),
                string.Empty,
                string.Empty,
                2,
                "host_operation_failed",
                "Windows hostで処理を開始できませんでした。");
        }
        finally
        {
            _elapsedTimer.Stop();
        }
        ShowResult(_lastResult);
    }

    private void OnCoordinatorEvent(
        object? sender,
        CoordinatorEvent coordinatorEvent)
    {
        Dispatcher.BeginInvoke(
            () => ApplyCoordinatorEvent(coordinatorEvent),
            DispatcherPriority.Background);
    }

    private void ApplyCoordinatorEvent(CoordinatorEvent coordinatorEvent)
    {
        var summary = coordinatorEvent.EventName switch
        {
            "operation_started" => "operation_started",
            "device_resolved" =>
                $"device_resolved {coordinatorEvent.ResolvedDevice}",
            "phase_started" =>
                $"phase_started {coordinatorEvent.StageId}",
            "progress" =>
                coordinatorEvent.Percent is { } percent
                    ? $"progress {coordinatorEvent.StageId} {percent:0}%"
                    : $"progress {coordinatorEvent.StageId}",
            "artifact_created" => "artifact_created",
            "operation_completed" => "operation_completed",
            "operation_failed" =>
                $"operation_failed {coordinatorEvent.ErrorCode}",
            "operation_cancelled" => "operation_cancelled",
            _ => coordinatorEvent.EventName,
        };
        _safeEventLog.Add(
            $"{coordinatorEvent.Sequence:D3} {summary}");
        SafeEventLogTextBox.Text = string.Join(
            Environment.NewLine,
            _safeEventLog);

        if (coordinatorEvent.EventName == "device_resolved")
        {
            DeviceText.Text =
                coordinatorEvent.RequestedPolicy == "cuda_required"
                && coordinatorEvent.RequestedDeviceIndex == 0
                && coordinatorEvent.ResolvedDevice == "cuda:0"
                && coordinatorEvent.FallbackAllowed == false
                && coordinatorEvent.FallbackOccurred == false
                    ? $"使用機能: {SelectedModelDisplayName} / NVIDIA CUDA (cuda:0)"
                    : "strict CUDAの確認に失敗しました。CPUには切り替えません。";
        }
        else if (coordinatorEvent.EventName == "phase_started")
        {
            var label = coordinatorEvent.Label
                ?? StageLabel(coordinatorEvent.StageId);
            RunningTitle.Text = label;
            RunningDetail.Text =
                $"{SelectedModelDisplayName}でCTデータを処理しています。";
            if (coordinatorEvent.StageIndex is { } index
                && coordinatorEvent.StageTotal is { } total)
            {
                OverallProgressText.Text =
                    $"全体: 工程 {index} / {total}";
            }
            RunProgressBar.IsIndeterminate = true;
            SubProgressText.Text = "処理を継続しています。";
        }
        else if (coordinatorEvent.EventName == "progress")
        {
            if (!string.IsNullOrWhiteSpace(coordinatorEvent.Stage))
            {
                RunningTitle.Text = StageLabel(coordinatorEvent.Stage);
            }
            if (coordinatorEvent.Percent is { } percent
                && percent is >= 0 and < 100)
            {
                RunProgressBar.IsIndeterminate = false;
                RunProgressBar.Value = percent;
                SubProgressText.Text =
                    $"この工程の進み具合: {percent:0}%";
            }
            else if (coordinatorEvent.Percent is >= 100)
            {
                RunProgressBar.IsIndeterminate = true;
                SubProgressText.Text =
                    "この工程は完了しました。次の処理を準備しています。";
            }
            else
            {
                RunProgressBar.IsIndeterminate = true;
                SubProgressText.Text =
                    "この工程の進捗率は取得できません。処理を継続しています。";
            }
        }
    }

    private void ShowResult(CoordinatorSessionResult result)
    {
        _lastDicomFailure = null;
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: false);
        if (result.TerminalEvent == "operation_completed")
        {
            ResultTitle.Text = "3Dプレビューを作成しました";
            ResultReason.Text =
                _selectedDicomCandidate is null
                    ? $"{SelectedModelDisplayName}をstrict CUDAで処理し、保存結果とoffline previewを検証しました。"
                    : $"使用した撮影: {_selectedDicomCandidate.DisplayTitle}。{SelectedModelDisplayName}をstrict CUDAで処理し、保存結果とoffline previewを検証しました。";
            ResultErrorCode.Visibility = Visibility.Collapsed;
            OpenPreviewButton.Visibility = Visibility.Visible;
            OpenOutputButton.Visibility = Visibility.Visible;
            CopyErrorButton.Visibility = Visibility.Collapsed;
            SetCopyInformationButtonForCancellation(cancelled: false);
            SetScreen(ShellScreen.Result, "完了");
            return;
        }

        OpenPreviewButton.Visibility = Visibility.Collapsed;
        OpenOutputButton.Visibility = Visibility.Collapsed;
        CopyErrorButton.Visibility = Visibility.Visible;
        ResultErrorCode.Visibility = Visibility.Visible;
        if (result.TerminalEvent == "operation_cancelled")
        {
            SetCopyInformationButtonForCancellation(cancelled: true);
            ResultTitle.Text = "処理を停止しました";
            ResultReason.Text =
                "入力データは変更されていません。結果はfinal directoryへ保存していません。";
            ResultErrorCode.Text =
                $"reason_code={result.ReasonCode ?? "cancel_requested"}";
            SetScreen(ShellScreen.Result, "停止しました");
        }
        else
        {
            SetCopyInformationButtonForCancellation(cancelled: false);
            ResultTitle.Text = "3Dプレビューを作成できませんでした";
            ResultReason.Text = FailureMessage(result.ErrorCode);
            ResultErrorCode.Text =
                $"error_code={result.ErrorCode ?? "unknown"}";
            SetScreen(ShellScreen.Result, "処理を完了できませんでした");
        }
    }

    private void SetHostFailure(string errorCode, string reason)
    {
        _lastResult = CoordinatorSessionResult.HostFailure(
            Guid.NewGuid().ToString("D"),
            string.Empty,
            string.Empty,
            2,
            errorCode,
            reason);
        ShowResult(_lastResult);
    }

    private void SetScreen(ShellScreen screen, string status)
    {
        _screen = screen;
        SetupPanel.Visibility =
            screen == ShellScreen.Setup
                ? Visibility.Visible
                : Visibility.Collapsed;
        StartPanel.Visibility =
            screen == ShellScreen.Start
                ? Visibility.Visible
                : Visibility.Collapsed;
        InputPanel.Visibility =
            screen == ShellScreen.Input
                ? Visibility.Visible
                : Visibility.Collapsed;
        DicomRescuePanel.Visibility =
            screen == ShellScreen.DicomRescue
                ? Visibility.Visible
                : Visibility.Collapsed;
        RunningPanel.Visibility =
            screen == ShellScreen.Running
                ? Visibility.Visible
                : Visibility.Collapsed;
        ResultPanel.Visibility =
            screen == ShellScreen.Result
                ? Visibility.Visible
                : Visibility.Collapsed;
        (HeaderTitle.Text, HeaderSubtitle.Text) = screen switch
        {
            ShellScreen.Setup => (
                "はじめの準備",
                "同梱済みのWindows実行環境を確認します。"),
            ShellScreen.Start => (
                "最初はSampleで流れを確認",
                "入力から結果確認までを先に試せます。"),
            ShellScreen.Input => (
                "入力と作成内容",
                "入力を確認し、作成する3Dプレビューを確認します。"),
            ShellScreen.DicomRescue => (
                "形状を確認",
                "三方向の断面を見ながら、形が自然に見える寸法を確認します。"),
            ShellScreen.Running => (
                "処理中",
                "現在の処理と経過時間を表示します。"),
            ShellScreen.Result => (
                "結果",
                "作成したファイルと次の操作を確認できます。"),
            _ => throw new ArgumentOutOfRangeException(nameof(screen)),
        };
        StatusPillText.Text = status;
        UpdateStepHighlights(screen);
        Dispatcher.BeginInvoke(
            () => FocusPrimaryControl(screen),
            DispatcherPriority.ContextIdle);
    }

    private void UpdateStepHighlights(ShellScreen screen)
    {
        var activeIndex = screen switch
        {
            ShellScreen.Start => 1,
            ShellScreen.Input => 2,
            ShellScreen.DicomRescue => 2,
            ShellScreen.Running => 3,
            ShellScreen.Result => 4,
            _ => 0,
        };
        var circles = new[]
        {
            Step1Circle,
            Step2Circle,
            Step3Circle,
            Step4Circle,
        };
        for (var index = 0; index < circles.Length; index++)
        {
            var active = index + 1 == activeIndex;
            circles[index].Background = active
                ? SystemColors.HighlightBrush
                : SystemColors.ControlBrush;
            if (circles[index].Child is TextBlock text)
            {
                text.Foreground = active
                    ? SystemColors.HighlightTextBrush
                    : SystemColors.ControlTextBrush;
            }
        }
    }

    private void FocusPrimaryControl(ShellScreen screen)
    {
        _ = screen switch
        {
            ShellScreen.Setup => PrepareButton.Focus(),
            ShellScreen.Start => SampleChoiceButton.Focus(),
            ShellScreen.Input when
                InputSourceChoicePanel.Visibility == Visibility.Visible =>
                    ChooseNiftiButton.Focus(),
            ShellScreen.Input => RunButton.Focus(),
            ShellScreen.DicomRescue =>
                ShowRescueReasonButton.Focus(),
            ShellScreen.Running => StopButton.Focus(),
            ShellScreen.Result when
                OpenPreviewButton.Visibility == Visibility.Visible =>
                    OpenPreviewButton.Focus(),
            ShellScreen.Result => CopyErrorButton.Focus(),
            _ => false,
        };
    }

    private void ApplyPreviewScenario(string scenario)
    {
        switch (scenario)
        {
            case "setup":
                SetScreen(ShellScreen.Setup, "待機中");
                break;
            case "start":
                SetScreen(ShellScreen.Start, "準備済み");
                break;
            case "input":
                SelectBundledSample();
                break;
            case "running":
                _inputPath = _configuration.BundledSamplePath;
                SetScreen(ShellScreen.Running, "歯列・顎骨を作成中");
                RunningTitle.Text = "顎顔面を抽出中";
                RunningDetail.Text =
                    "TotalSegmentatorでCTデータを処理しています。";
                OverallProgressText.Text = "全体: 工程 2 / 4";
                RunProgressBar.IsIndeterminate = false;
                RunProgressBar.Value = 42;
                SubProgressText.Text = "この工程の進み具合: 42%";
                DeviceText.Text =
                    "使用機能: TotalSegmentator / NVIDIA CUDA (cuda:0)";
                ElapsedText.Text = "経過時間: 1分42秒";
                break;
            case "success":
                _lastResult = new CoordinatorSessionResult(
                    "00000000-0000-0000-0000-000000000001",
                    "operation_completed",
                    Path.Combine(_configuration.OutputRoot, "sample-case"),
                    string.Empty,
                    0,
                    null,
                    null,
                    null,
                    "cuda_required",
                    0,
                    "cuda:0",
                    false,
                    false);
                ShowResult(_lastResult);
                break;
            case "failure":
                _lastResult = CoordinatorSessionResult.HostFailure(
                    "00000000-0000-0000-0000-000000000001",
                    string.Empty,
                    string.Empty,
                    1,
                    "cuda_unavailable",
                    "The required CUDA device did not pass strict validation.");
                ShowResult(_lastResult);
                break;
            case "dicom-input":
            case "dicom-series":
            case "dicom-rescue":
                ApplyDicomPreviewScenario(scenario);
                break;
            case "model-comparison":
            case "input-dentalseg":
            case "running-dentalseg":
            case "input-individual-teeth":
            case "running-individual-teeth":
            case "input-toothseg":
            case "running-toothseg":
                ApplyModelPreviewScenario(scenario);
                break;
            default:
                throw new ArgumentException(
                    "The UI preview scenario is not supported.");
        }
    }

    private void UpdateElapsed()
    {
        var elapsed = DateTime.UtcNow - _runStartedUtc;
        ElapsedText.Text = elapsed.TotalMinutes >= 1
            ? $"経過時間: {(int)elapsed.TotalMinutes}分{elapsed.Seconds}秒"
            : $"経過時間: {Math.Max(0, elapsed.Seconds)}秒";
    }

    private static string StageLabel(string? value)
    {
        return value switch
        {
            "prepare" => "実行準備",
            "segment" => "顎顔面を抽出中",
            "predict" => "DentalSegmentatorで推論中",
            "roi" => "歯列ROI・入力を準備中",
            "semantic" => "ToothSeg semantic枝",
            "instance" => "ToothSeg instance枝",
            "restore" => "FDI番号付与・元画像へ復元中",
            "finalize" => "結果を整理中",
            "preview" => "3D表示・結果情報を作成中",
            "Resampling" => "入力を調整中",
            "Predicting" => "顎顔面を抽出中",
            "Saving segmentations" => "結果を保存中",
            _ => "処理を継続中",
        };
    }

    private static string FailureMessage(string? errorCode)
    {
        return errorCode switch
        {
            "cuda_unavailable"
                or "cuda_device_index_invalid"
                or "cuda_runtime_unavailable"
                or "unexpected_device_fallback" =>
                    "必要なNVIDIA CUDAデバイスを確認できませんでした。CPUには切り替えていません。",
            "input_not_found" or "input_path_invalid" =>
                "選択したNIfTI形式のCTを開けませんでした。",
            "artifact_verification_failed"
                or "host_completion_verification_failed" =>
                    "保存結果の検証を完了できませんでした。final directoryへ確定していません。",
            "dentalseg_prepare_required" =>
                    "検証済みのapp-private DentalSegmentatorモデルを確認できませんでした。",
            "dentalseg_failed" =>
                    "DentalSegmentator処理を完了できませんでした。別モデルやCPUには切り替えていません。",
            "toothseg_prepare_required" =>
                    "検証済みのapp-private ToothSegモデルを確認できませんでした。",
            "toothseg_cuda_oom" =>
                    "ToothSegに必要なCUDAメモリが不足しました。解像度やモデルは変更していません。",
            "toothseg_failed" =>
                    "ToothSeg処理を完了できませんでした。別モデルやCPUには切り替えていません。",
            _ =>
                "Windows hostで処理状態を確認できませんでした。詳細情報を確認してください。",
        };
    }

    private static void OpenLocalPath(string path)
    {
        var absolutePath = Path.GetFullPath(path);
        if (!File.Exists(absolutePath) && !Directory.Exists(absolutePath))
        {
            return;
        }
        Process.Start(
            new ProcessStartInfo
            {
                FileName = absolutePath,
                UseShellExecute = true,
            });
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
}

internal sealed record UiContractResult(
    bool Passed,
    bool AutomationNames,
    bool KeyboardFocusable,
    bool DynamicSystemColors,
    bool DynamicLabels,
    bool RescueSlidersLinked,
    int ButtonCount);
