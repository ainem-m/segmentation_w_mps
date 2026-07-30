using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

public partial class MainWindow
{
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
            [SamplePreviewButton] =
                "Sample 1の3Dプレビューを開く",
            [OwnDataChoiceButton] = "手元のCTデータを使う",
            [ChooseInputButton] = "手元のCTを選ぶ",
            [ChooseNiftiButton] = "NIfTIファイルを選ぶ",
            [ChooseDicomFolderButton] = "DICOMフォルダを選ぶ",
            [ChangeDicomSeriesButton] = "使用する撮影を変更",
            [UseSelectedDicomSeriesButton] = "この撮影を使う",
            [CloseDicomSeriesButton] = "撮影選択を閉じる",
            [ViewOtherDicomSeriesButton] =
                "同じフォルダのほかの撮影を見る",
            [UseDisplayedDicomPreviewButton] =
                "表示中の撮影で3Dプレビュー作成へ進む",
            [ChooseAnotherDicomFolderFromPreviewButton] =
                "CT画像確認から別のDICOMフォルダを選ぶ",
            [ShowRescueReasonButton] = "形状候補の理由を見る",
            [ChooseAnotherCtFromRescueButton] =
                "形状確認から別のCTを選ぶ",
            [ResetRescueSpacingButton] = "推定形状に戻す",
            [ShowRescueOrientationButton] = "画像の向きを修正",
            [RotateRescueImageButton] = "救済画像を90度回転",
            [CreateRescuePreviewButton] =
                "この形状で確認画像を作る",
            [ConfirmRescueAndRunButton] =
                "確認済みの形状で3Dプレビューを作る",
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
            [ChangeOutputRootButton] = "結果の保存先を変更",
            [StopButton] = "停止",
            [OpenPreviewButton] = "3Dプレビューを開く",
            [OpenOutputButton] = "結果フォルダを開く",
            [CopyErrorButton] = "エラー情報をコピー",
            [ShowDetailsButton] = "詳細情報を見る",
            [ExportSlicerButton] = "3D Slicer用に書き出す",
            [RebuildPreviewButton] = "3Dプレビューを再生成",
            [ReturnToInputButton] = "入力と作成内容へ戻る",
            [ChooseAnotherResultInputButton] =
                "結果画面から別のCTを選ぶ",
            [RerunButton] = "同じ入力でもう一度作成",
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
            && HasStrictCudaZeroWithoutFallback(result)
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
            && HasStrictCudaZeroWithoutFallback(result)
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

    private static bool HasStrictCudaZeroWithoutFallback(
        CoordinatorSessionResult result) =>
        result.RequestedPolicy == "cuda_required"
        && result.RequestedDeviceIndex == 0
        && result.ResolvedDevice == "cuda:0"
        && result.FallbackAllowed == false
        && result.FallbackOccurred == false;

    internal async Task<bool> RunEvidenceDentalSegmentatorAsync(
        string evidencePath)
        => await RunModelEvidenceAsync(
            evidencePath,
            new ModelEvidenceSpec(
                "totalsegmentator_wrapper.windows_wpf_dentalseg_run.v1",
                SegmentationProfile.DentalSegmentator,
                "dentalsegmentator",
                "craniofacial_structures",
                _configuration.CheckDentalSegmentatorRuntime));

    internal async Task<bool> RunEvidenceIndividualTeethAsync(
        string evidencePath)
        => await RunModelEvidenceAsync(
            evidencePath,
            new ModelEvidenceSpec(
                "totalsegmentator_wrapper.windows_wpf_individual_teeth_run.v1",
                SegmentationProfile.IndividualTeeth,
                "totalsegmentator",
                "teeth",
                _configuration.CheckIndividualTeethRuntime));

    internal async Task<bool> RunEvidenceToothSegAsync(
        string evidencePath)
        => await RunModelEvidenceAsync(
            evidencePath,
            new ModelEvidenceSpec(
                "totalsegmentator_wrapper.windows_wpf_toothseg_run.v1",
                SegmentationProfile.ToothSeg,
                "toothseg",
                "teeth",
                _configuration.CheckToothSegRuntime));

    private async Task<bool> RunModelEvidenceAsync(
        string evidencePath,
        ModelEvidenceSpec spec)
    {
        var runtime = _configuration.CheckRuntime();
        var model = spec.CheckRuntime();
        if (!runtime.Passed || !model.Passed)
        {
            await WriteEvidenceAsync(
                evidencePath,
                new
                {
                    schema = spec.Schema,
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
        SetSelectedSegmentationProfile(spec.Profile);
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
                        == spec.Backend
                    && root.GetProperty("task").GetString()
                        == spec.Task
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
            && HasStrictCudaZeroWithoutFallback(result)
            && runManifestPassed
            && artifactManifestExists;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema = spec.Schema,
                status = passed ? "pass" : "fail",
                operation = spec.Profile.OperationName(),
                backend = spec.Backend,
                task = spec.Task,
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

    private sealed record ModelEvidenceSpec(
        string Schema,
        SegmentationProfile Profile,
        string Backend,
        string Task,
        Func<RuntimeCheckResult> CheckRuntime);

    internal async Task<bool> RunEvidenceDicomAsync(
        string dicomFolder,
        string evidencePath)
    {
        _safeEventLog.Clear();
        var converted = await AuditAndConvertDicomAsync(
            dicomFolder);
        var conversion = _lastDicomConversion;
        var audit = _dicomAudit;
        var selected = _selectedDicomCandidate;
        var coordinatorWasNotStartedDuringIntake =
            _lastResult is null;
        var previewVerified =
            converted
            && conversion?.Previews.Count == 3
            && conversion.Previews.All(
                preview => File.Exists(preview.Path)
                    && new FileInfo(preview.Path).Length > 0);
        var previewConfirmed =
            previewVerified
            && CommitDicomConversion();
        if (previewConfirmed)
        {
            await StartRunAsync();
        }
        var result = _lastResult;
        var manifestExists =
            conversion?.ManifestPath is { } manifestPath
            && File.Exists(manifestPath);
        var niftiExists =
            conversion?.NiftiPath is { } niftiPath
            && File.Exists(niftiPath)
            && new FileInfo(niftiPath).Length > 0;
        var passed =
            converted
            && previewVerified
            && previewConfirmed
            && coordinatorWasNotStartedDuringIntake
            && manifestExists
            && niftiExists
            && result?.TerminalEvent == "operation_completed"
            && result.SupervisorExitCode == 0
            && HasStrictCudaZeroWithoutFallback(result);
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_dicom_run.v1",
                status = passed ? "pass" : "fail",
                source_kind = "dicom",
                dicom_operation_id =
                    audit?.OperationId
                    ?? _lastDicomFailure?.OperationId,
                clean_candidate_count =
                    audit?.Candidates.Count
                    ?? (_lastDicomFailure?.ErrorCode
                            == "dicom_clean_series_unavailable"
                        ? 0
                        : null),
                selected_series_number =
                    selected?.SeriesNumber,
                selected_series_file_count =
                    selected?.FileCount,
                selection_basis =
                    conversion?.SelectionBasis,
                intake_manifest_exists = manifestExists,
                normalized_nifti_nonempty = niftiExists,
                mpr_preview_verified = previewVerified,
                mpr_preview_count =
                    conversion?.Previews.Count,
                preview_confirmed_before_run =
                    previewConfirmed,
                coordinator_started_during_intake =
                    !coordinatorWasNotStartedDuringIntake,
                explicit_run_invoked = previewConfirmed,
                coordinator_operation_id =
                    result?.OperationId,
                terminal_event = result?.TerminalEvent,
                supervisor_exit_code =
                    result?.SupervisorExitCode,
                requested_policy =
                    result?.RequestedPolicy,
                requested_device_index =
                    result?.RequestedDeviceIndex,
                resolved_device = result?.ResolvedDevice,
                fallback_allowed =
                    result?.FallbackAllowed,
                fallback_occurred =
                    result?.FallbackOccurred,
                intake_error_code =
                    _lastDicomFailure?.ErrorCode,
                intake_failure_stage =
                    _lastDicomFailure?.Stage,
                segmentation_started =
                    result is not null,
                raw_output_forwarded = false,
            });
        return passed;
    }

    internal async Task<bool> RunEvidenceDicomRescueAsync(
        string dicomFolder,
        string evidencePath)
    {
        _safeEventLog.Clear();
        var audited = await AuditAndConvertDicomAsync(
            dicomFolder);
        var candidate = _selectedDicomRescueCandidate;
        var audit = _dicomAudit;
        DicomSpacing? spacing = null;
        if (candidate is not null
            && TryReadRescueSpacing(out var sliderSpacing))
        {
            spacing = sliderSpacing;
        }
        var coordinatorWasNotStarted =
            _lastResult is null;
        DicomRescueResult? rescue = null;
        if (audited
            && audit is not null
            && candidate is not null
            && spacing is not null
            && _dicomSession is not null
            && audit.Candidates.Count == 0)
        {
            rescue = await _dicomSession.PrepareRescueAsync(
                audit,
                candidate,
                spacing,
                _rescueTransform);
            if (rescue.Succeeded)
            {
                ShowRescuePreviews(rescue.Previews);
                _lastDicomRescue = rescue;
                RescuePreviewStatusText.Text =
                    "三方向の確認画像を作成しました。AI推論は開始していません。";
                SetScreen(
                    ShellScreen.DicomRescue,
                    "確認画像を作成しました");
            }
        }
        var manifestExists =
            rescue?.ManifestPath is { } manifestPath
            && File.Exists(manifestPath);
        var decodedVolumeExists =
            rescue?.DecodedVolumePath is { } decodedVolumePath
            && File.Exists(decodedVolumePath)
            && new FileInfo(decodedVolumePath).Length > 0;
        var confirmationBound =
            rescue?.ConfirmationToken is { Length: 64 }
            && rescue.ConfirmedSpacing == spacing
            && rescue.Transform == _rescueTransform;
        var previewPlanes = rescue?.Previews
            .Select(preview => preview.Plane)
            .Order(StringComparer.Ordinal)
            .ToArray()
            ?? Array.Empty<string>();
        var passed =
            audited
            && coordinatorWasNotStarted
            && rescue?.Succeeded == true
            && manifestExists
            && decodedVolumeExists
            && confirmationBound
            && previewPlanes.SequenceEqual(
                new[] { "axial", "coronal", "sagittal" })
            && _lastResult is null
            && _inputPath is null;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_dicom_rescue_preview.v2",
                status = passed ? "pass" : "fail",
                source_kind =
                    "dicom_secondary_capture",
                dicom_operation_id =
                    audit?.OperationId
                    ?? _lastDicomFailure?.OperationId,
                clean_candidate_count =
                    audit?.Candidates.Count,
                rescue_candidate_count =
                    audit?.RescueCandidates.Count,
                selected_series_number =
                    candidate?.SeriesNumber,
                selected_series_file_count =
                    candidate?.FileCount,
                requested_spacing_xyz =
                    spacing?.Values,
                normalizer_exit_code =
                    rescue?.Succeeded == true ? (int?)0 : null,
                job_became_empty =
                    rescue?.Succeeded == true,
                rescue_manifest_exists =
                    manifestExists,
                decoded_volume_nonempty =
                    decodedVolumeExists,
                confirmation_token_bound =
                    confirmationBound,
                preview_count =
                    rescue?.Previews.Count,
                preview_planes = previewPlanes,
                preview_uniform_or_empty =
                    rescue?.Previews.ToDictionary(
                        preview => preview.Plane,
                        preview => preview.UniformOrEmpty),
                coordinator_started =
                    !coordinatorWasNotStarted
                    || _lastResult is not null,
                segmentation_started = false,
                rescue_output_promoted_as_clean_ct =
                    _inputPath is not null,
                raw_output_forwarded = false,
                error_code =
                    rescue?.ErrorCode
                    ?? _lastDicomFailure?.ErrorCode,
            });
        return passed;
    }

}
