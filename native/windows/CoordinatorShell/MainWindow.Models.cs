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

    private void SelectIndividualTeethButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        var readiness =
            _configuration.CheckIndividualTeethRuntime();
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
            SegmentationProfile.IndividualTeeth);
        SetModelComparisonExpanded(expanded: false);
        OtherModelsCardButton.Focus();
    }

    private void SelectToothSegButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        var readiness = _configuration.CheckToothSegRuntime();
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
            SegmentationProfile.ToothSeg);
        SetModelComparisonExpanded(expanded: false);
        OtherModelsCardButton.Focus();
    }

    private void SetSelectedSegmentationProfile(
        SegmentationProfile profile)
    {
        _selectedSegmentationProfile = profile;
        var standardSelected =
            profile == SegmentationProfile.TotalSegmentator;
        var dentalSelected =
            profile == SegmentationProfile.DentalSegmentator;
        var individualSelected =
            profile == SegmentationProfile.IndividualTeeth;
        var toothSegSelected =
            profile == SegmentationProfile.ToothSeg;
        StandardModelCardTitle.Text =
            standardSelected ? "✓  標準モデル" : "標準モデル";
        OtherModelsCardTitle.Text =
            standardSelected ? "その他のモデル" : "✓  その他のモデル";
        OtherModelsCardName.Text = standardSelected
            ? "比較して選ぶ"
            : $"選択中：{profile.DisplayName()}";
        OtherModelsCardDetail.Text = dentalSelected
            ? "現在はこの方法を使用します。クリックすると結果画像と処理内容を比較できます。"
            : individualSelected
                ? "歯を1本ずつ分けるベータ機能を使用します。クリックすると他の方法と比較できます。"
                : toothSegSelected
                    ? "FDI番号付きの高精細歯を作成します。クリックすると他の方法と比較できます。"
                : "結果画像と処理内容を見ながら、用途に合う方法を選べます。";
        StandardModelCardButton.BorderBrush = standardSelected
            ? SystemColors.HighlightBrush
            : SystemColors.ActiveBorderBrush;
        StandardModelCardButton.BorderThickness =
            standardSelected ? new Thickness(2) : new Thickness(1);
        OtherModelsCardButton.BorderBrush = standardSelected
            ? SystemColors.ActiveBorderBrush
            : SystemColors.HighlightBrush;
        OtherModelsCardButton.BorderThickness =
            standardSelected ? new Thickness(1) : new Thickness(2);
        SelectStandardModelButton.IsEnabled = !standardSelected;
        SelectStandardModelButton.Content =
            standardSelected ? "選択中" : "この方法を選ぶ";
        SelectDentalSegmentatorButton.IsEnabled = !dentalSelected;
        SelectDentalSegmentatorButton.Content =
            dentalSelected ? "選択中" : "この方法を選ぶ";
        SelectIndividualTeethButton.IsEnabled = !individualSelected;
        SelectIndividualTeethButton.Content =
            individualSelected ? "選択中" : "この方法を選ぶ";
        SelectToothSegButton.IsEnabled = !toothSegSelected;
        SelectToothSegButton.Content =
            toothSegSelected ? "選択中" : "この方法を選ぶ";
        AutomationProperties.SetName(
            SelectStandardModelButton,
            standardSelected
                ? "通常のTotalSegmentatorを選択中"
                : "通常のTotalSegmentatorを選ぶ");
        AutomationProperties.SetName(
            SelectDentalSegmentatorButton,
            dentalSelected
                ? "DentalSegmentatorを選択中"
                : "DentalSegmentatorを選ぶ");
        AutomationProperties.SetName(
            SelectIndividualTeethButton,
            individualSelected
                ? "個別歯ベータを選択中"
                : "個別歯ベータを選ぶ");
        AutomationProperties.SetName(
            SelectToothSegButton,
            toothSegSelected
                ? "高精細歯ToothSegを選択中"
                : "高精細歯ToothSegを選ぶ");
        ModelSelectionMessage.Text = dentalSelected
            ? "DentalSegmentatorは実験的な追加機能です。別モデルやCPUへ自動的に切り替えません。"
            : individualSelected
                ? "個別歯ベータは検証用のベータ機能です。通常の表示より時間がかかり、CPUへ切り替えません。"
                : toothSegSelected
                    ? "ToothSegは実験的な追加機能です。処理時間とVRAMを多く必要とし、CPUや別モデルへ切り替えません。"
                : string.Empty;
        ModelSelectionMessage.Visibility = standardSelected
            ? Visibility.Collapsed
            : Visibility.Visible;
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
        return _selectedSegmentationProfile switch
        {
            SegmentationProfile.DentalSegmentator =>
                _configuration.CheckDentalSegmentatorRuntime(),
            SegmentationProfile.IndividualTeeth =>
                _configuration.CheckIndividualTeethRuntime(),
            SegmentationProfile.ToothSeg =>
                _configuration.CheckToothSegRuntime(),
            _ => new RuntimeCheckResult(
                true,
                "TotalSegmentatorを使用します。",
                null,
                null),
        };
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
            case "input-individual-teeth":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.IndividualTeeth);
                break;
            case "running-individual-teeth":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.IndividualTeeth);
                SetScreen(
                    ShellScreen.Running,
                    "個別歯ベータで作成中");
                RunningTitle.Text =
                    "歯を1本ずつ抽出中";
                RunningDetail.Text =
                    "個別歯ベータでCTデータを処理しています。";
                OverallProgressText.Text = "全体: 工程 4 / 6";
                RunProgressBar.IsIndeterminate = true;
                SubProgressText.Text =
                    "この工程の進捗率は取得できません。処理を継続しています。";
                DeviceText.Text =
                    "使用機能: 個別歯ベータ / NVIDIA CUDA (cuda:0)";
                break;
            case "input-toothseg":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.ToothSeg);
                break;
            case "running-toothseg":
                SetSelectedSegmentationProfile(
                    SegmentationProfile.ToothSeg);
                SetScreen(
                    ShellScreen.Running,
                    "ToothSegで作成中");
                RunningTitle.Text =
                    "高精細歯を抽出中";
                RunningDetail.Text =
                    "ToothSegのsemantic/instance両branchで処理しています。";
                OverallProgressText.Text = "全体: 工程 3 / 5";
                RunProgressBar.IsIndeterminate = true;
                SubProgressText.Text =
                    "高精細推論には時間がかかります。処理を継続しています。";
                DeviceText.Text =
                    "使用機能: 高精細歯（ToothSeg） / NVIDIA CUDA (cuda:0)";
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

    internal async Task<bool> RunEvidenceIndividualTeethAsync(
        string evidencePath)
    {
        var runtime = _configuration.CheckRuntime();
        var model = _configuration.CheckIndividualTeethRuntime();
        if (!runtime.Passed || !model.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_individual_teeth_run.v1",
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
            SegmentationProfile.IndividualTeeth);
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
                        == "totalsegmentator"
                    && root.GetProperty("task").GetString()
                        == "teeth"
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
                    "totalsegmentator_wrapper.windows_wpf_individual_teeth_run.v1",
                status = passed ? "pass" : "fail",
                operation =
                    SegmentationProfile.IndividualTeeth.OperationName(),
                backend = "totalsegmentator",
                task = "teeth",
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

    internal async Task<bool> RunEvidenceToothSegAsync(
        string evidencePath)
    {
        var runtime = _configuration.CheckRuntime();
        var model = _configuration.CheckToothSegRuntime();
        if (!runtime.Passed || !model.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema =
                        "totalsegmentator_wrapper.windows_wpf_toothseg_run.v1",
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
            SegmentationProfile.ToothSeg);
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
                        == "toothseg"
                    && root.GetProperty("task").GetString()
                        == "teeth"
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
                    "totalsegmentator_wrapper.windows_wpf_toothseg_run.v1",
                status = passed ? "pass" : "fail",
                operation =
                    SegmentationProfile.ToothSeg.OperationName(),
                backend = "toothseg",
                task = "teeth",
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
