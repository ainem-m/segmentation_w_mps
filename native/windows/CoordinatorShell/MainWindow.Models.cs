using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

public partial class MainWindow
{
    private SegmentationProfile _selectedSegmentationProfile =
        SegmentationProfile.TotalSegmentator;

    private string SelectedModelDisplayName =>
        _selectedSegmentationProfile.DisplayName();

    private void InitializeModelSelection()
    {
        SetSelectedSegmentationProfile(
            SegmentationProfile.TotalSegmentator);
    }

    private void StandardModelCardButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        SetSelectedSegmentationProfile(
            SegmentationProfile.TotalSegmentator);
    }

    private void OtherModelsCardButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        SetModelComparisonExpanded(expanded: true);
    }

    private void CloseModelComparisonButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        SetModelComparisonExpanded(expanded: false);
        OtherModelsCardButton.Focus();
    }

    private void SelectStandardModelButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        SetSelectedSegmentationProfile(
            SegmentationProfile.TotalSegmentator);
        SetModelComparisonExpanded(expanded: false);
        StandardModelCardButton.Focus();
    }

    private void SelectDentalSegmentatorButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        var readiness =
            _configuration.CheckDentalSegmentatorRuntime();
        if (!readiness.Passed)
        {
            ModelSelectionMessage.Text = string.Join(
                " ",
                readiness.Message,
                readiness.RecoveryMessage);
            ModelSelectionMessage.Visibility =
                Visibility.Visible;
            StatusPillText.Text = "追加モデル未準備";
            return;
        }
        SetSelectedSegmentationProfile(
            SegmentationProfile.DentalSegmentator);
        SetModelComparisonExpanded(expanded: false);
        OtherModelsCardButton.Focus();
    }

    private void IndividualTeethStatusButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        ModelSelectionMessage.Text =
            "個別歯ベータは次のWindows工程で検証します。この実行では選択していません。";
        ModelSelectionMessage.Visibility = Visibility.Visible;
    }

    private void SetSelectedSegmentationProfile(
        SegmentationProfile profile)
    {
        _selectedSegmentationProfile = profile;
        var dentalSelected =
            profile == SegmentationProfile.DentalSegmentator;
        StandardModelCardTitle.Text =
            dentalSelected ? "標準モデル" : "✓  標準モデル";
        OtherModelsCardTitle.Text =
            dentalSelected ? "✓  その他のモデル" : "その他のモデル";
        OtherModelsCardName.Text = dentalSelected
            ? "選択中：DentalSegmentator（実験的）"
            : "比較して選ぶ";
        OtherModelsCardDetail.Text = dentalSelected
            ? "現在はこの方法を使用します。クリックすると結果画像と処理内容を比較できます。"
            : "結果画像と処理内容を見ながら、用途に合う方法を選べます。";
        StandardModelCardButton.BorderBrush = dentalSelected
            ? SystemColors.ActiveBorderBrush
            : SystemColors.HighlightBrush;
        StandardModelCardButton.BorderThickness =
            dentalSelected ? new Thickness(1) : new Thickness(2);
        OtherModelsCardButton.BorderBrush = dentalSelected
            ? SystemColors.HighlightBrush
            : SystemColors.ActiveBorderBrush;
        OtherModelsCardButton.BorderThickness =
            dentalSelected ? new Thickness(2) : new Thickness(1);
        SelectStandardModelButton.IsEnabled = dentalSelected;
        SelectStandardModelButton.Content =
            dentalSelected ? "この方法を選ぶ" : "選択中";
        SelectDentalSegmentatorButton.IsEnabled = !dentalSelected;
        SelectDentalSegmentatorButton.Content =
            dentalSelected ? "選択中" : "この方法を選ぶ";
        AutomationProperties.SetName(
            SelectStandardModelButton,
            dentalSelected
                ? "通常のTotalSegmentatorを選ぶ"
                : "通常のTotalSegmentatorを選択中");
        AutomationProperties.SetName(
            SelectDentalSegmentatorButton,
            dentalSelected
                ? "DentalSegmentatorを選択中"
                : "DentalSegmentatorを選ぶ");
        ModelSelectionMessage.Text = dentalSelected
            ? "DentalSegmentatorは実験的な追加機能です。別モデルやCPUへ自動的に切り替えません。"
            : string.Empty;
        ModelSelectionMessage.Visibility = dentalSelected
            ? Visibility.Visible
            : Visibility.Collapsed;
        AutomationProperties.SetHelpText(
            RunButton,
            $"strict CUDAで{profile.DisplayName()}処理を開始します。");
    }

    private void SetModelComparisonExpanded(bool expanded)
    {
        ModelComparisonPanel.Visibility =
            expanded ? Visibility.Visible : Visibility.Collapsed;
        if (expanded)
        {
            CloseModelComparisonButton.Focus();
            Dispatcher.BeginInvoke(
                () => ModelComparisonPanel.BringIntoView(
                    new Rect(0, 0, 1, 1)),
                System.Windows.Threading.DispatcherPriority.ContextIdle);
        }
    }

    private RuntimeCheckResult CheckSelectedModelRuntime()
    {
        return _selectedSegmentationProfile
            == SegmentationProfile.DentalSegmentator
            ? _configuration.CheckDentalSegmentatorRuntime()
            : new RuntimeCheckResult(
                true,
                "TotalSegmentatorを使用します。",
                null,
                null);
    }

    private void ResetModelSelection()
    {
        SetModelComparisonExpanded(expanded: false);
        SetSelectedSegmentationProfile(
            SegmentationProfile.TotalSegmentator);
    }

    private void ApplyModelPreviewScenario(string scenario)
    {
        SelectBundledSample();
        switch (scenario)
        {
            case "model-comparison":
                SetModelComparisonExpanded(expanded: true);
                break;
            case "input-dentalseg":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.DentalSegmentator);
                break;
            case "running-dentalseg":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.DentalSegmentator);
                SetScreen(
                    ShellScreen.Running,
                    "DentalSegmentatorで作成中");
                RunningTitle.Text =
                    "DentalSegmentatorで推論中";
                RunningDetail.Text =
                    "DentalSegmentatorでCTデータを処理しています。";
                OverallProgressText.Text = "全体: 工程 2 / 4";
                RunProgressBar.IsIndeterminate = true;
                SubProgressText.Text =
                    "この工程の進捗率は取得できません。処理を継続しています。";
                DeviceText.Text =
                    "使用機能: DentalSegmentator（実験的） / NVIDIA CUDA (cuda:0)";
                break;
            default:
                throw new ArgumentException(
                    "The model UI preview scenario is not supported.");
        }
    }

    internal async Task<bool> RunEvidenceDentalSegmentatorAsync(
        string evidencePath)
    {
        var runtime = _configuration.CheckRuntime();
        var model = _configuration.CheckDentalSegmentatorRuntime();
        if (!runtime.Passed || !model.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_dentalseg_run.v1",
                    status = "fail",
                    error_code =
                        model.ErrorCode
                        ?? runtime.ErrorCode
                        ?? "runtime_unavailable",
                    segmentation_started = false,
                });
            return false;
        }

        SelectBundledSample();
        SetSelectedSegmentationProfile(
            SegmentationProfile.DentalSegmentator);
        await StartRunAsync();
        var result = _lastResult;
        var runManifestPassed = false;
        var artifactManifestExists = false;
        if (result?.FinalDirectory is { Length: > 0 } finalDirectory)
        {
            var runManifestPath = Path.Combine(
                finalDirectory,
                "run-manifest.json");
            var artifactManifestPath = Path.Combine(
                finalDirectory,
                "artifact-manifest.json");
            artifactManifestExists =
                File.Exists(artifactManifestPath)
                && new FileInfo(artifactManifestPath).Length > 0;
            if (File.Exists(runManifestPath))
            {
                using var manifest = JsonDocument.Parse(
                    File.ReadAllText(runManifestPath));
                var root = manifest.RootElement;
                runManifestPassed =
                    root.GetProperty("backend").GetString()
                        == "dentalsegmentator"
                    && root.GetProperty("task").GetString()
                        == "craniofacial_structures"
                    && root.GetProperty(
                            "requested_policy")
                        .GetString() == "cuda_required"
                    && root.GetProperty(
                            "requested_device_index")
                        .GetInt32() == 0
                    && root.GetProperty("resolved_device")
                        .GetString() == "cuda:0"
                    && !root.GetProperty(
                            "fallback_allowed")
                        .GetBoolean()
                    && !root.GetProperty(
                            "fallback_occurred")
                        .GetBoolean();
            }
        }
        var passed =
            result?.TerminalEvent == "operation_completed"
            && result.SupervisorExitCode == 0
            && result.RequestedPolicy == "cuda_required"
            && result.RequestedDeviceIndex == 0
            && result.ResolvedDevice == "cuda:0"
            && result.FallbackAllowed == false
            && result.FallbackOccurred == false
            && runManifestPassed
            && artifactManifestExists;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_dentalseg_run.v1",
                status = passed ? "pass" : "fail",
                operation =
                    SegmentationProfile.DentalSegmentator.OperationName(),
                backend = "dentalsegmentator",
                task = "craniofacial_structures",
                operation_id = result?.OperationId,
                terminal_event = result?.TerminalEvent,
                supervisor_exit_code = result?.SupervisorExitCode,
                requested_policy = result?.RequestedPolicy,
                requested_device_index =
                    result?.RequestedDeviceIndex,
                resolved_device = result?.ResolvedDevice,
                fallback_allowed = result?.FallbackAllowed,
                fallback_occurred = result?.FallbackOccurred,
                run_manifest_verified = runManifestPassed,
                artifact_manifest_exists =
                    artifactManifestExists,
                segmentation_started = result is not null,
            });
        return passed;
    }
}
