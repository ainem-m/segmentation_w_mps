using Microsoft.Win32;
using System.IO;
using System.Windows;
using System.Windows.Automation;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

public partial class MainWindow
{
    private DicomIntakeSession? _dicomSession;
    private DicomAuditResult? _dicomAudit;
    private DicomCleanCandidate? _selectedDicomCandidate;
    private DicomConversionResult? _lastDicomConversion;
    private DicomUiFailure? _lastDicomFailure;
    private bool _dicomOperationActive;
    private bool _dicomStopRequested;

    private void BeginOwnDataSelection(bool clearExistingInput)
    {
        if (clearExistingInput)
        {
            _inputPath = null;
            ClearDicomSelection();
            InputDisplayName.Text = "CTデータが選択されていません";
            SetInputButtonsForNoInput();
            RunButton.IsEnabled = false;
            SampleNotice.Visibility = Visibility.Collapsed;
        }
        InputSourceChoicePanel.Visibility = Visibility.Visible;
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        SetScreen(ShellScreen.Input, "CTデータを選択してください");
        Dispatcher.BeginInvoke(ChooseNiftiButton.Focus);
    }

    private void ChooseNiftiButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        ChooseNiftiInput();
    }

    private async void ChooseDicomFolderButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        var dialog = new OpenFolderDialog
        {
            Title = "DICOM撮影フォルダを選ぶ",
            Multiselect = false,
        };
        if (dialog.ShowDialog(this) != true)
        {
            return;
        }
        await AuditAndConvertDicomAsync(dialog.FolderName);
    }

    private void ChangeDicomSeriesButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_dicomAudit is null
            || _selectedDicomCandidate is null
            || _dicomAudit.Candidates.Count <= 1)
        {
            return;
        }
        DicomSeriesListBox.ItemsSource = _dicomAudit.Candidates;
        DicomSeriesListBox.SelectedItem = _selectedDicomCandidate;
        DicomSeriesSelectionPanel.Visibility = Visibility.Visible;
        Dispatcher.BeginInvoke(DicomSeriesListBox.Focus);
    }

    private async void UseSelectedDicomSeriesButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (DicomSeriesListBox.SelectedItem
            is not DicomCleanCandidate candidate)
        {
            return;
        }
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        await ConvertDicomCandidateAsync(
            candidate,
            userSelected: true);
    }

    private void CloseDicomSeriesButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        DicomSeriesListBox.SelectedItem = _selectedDicomCandidate;
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        ChangeDicomSeriesButton.Focus();
    }

    private async Task<bool> AuditAndConvertDicomAsync(
        string dicomFolder)
    {
        var rollbackState = CaptureDicomSelection();
        _dicomStopRequested = false;
        _dicomSession = new DicomIntakeSession(_configuration);
        _dicomAudit = null;
        _selectedDicomCandidate = null;
        _lastDicomConversion = null;
        _lastDicomFailure = null;

        BeginDicomProgress(
            "CT確認中",
            "撮影データの種類を確認しています。プレビュー作成はまだ開始していません。",
            "DICOMを確認しています。");
        DicomAuditResult audit;
        try
        {
            audit = await _dicomSession.AuditAsync(dicomFolder);
        }
        finally
        {
            EndDicomProgress();
        }
        if (_dicomStopRequested)
        {
            RestoreDicomSelection(rollbackState);
            ShowDicomFailure(
                "dicom_audit_cancelled",
                "DICOMの確認を停止しました。",
                audit.OperationId,
                stage: "audit");
            return false;
        }
        if (!audit.Succeeded)
        {
            RestoreDicomSelection(rollbackState);
            ShowDicomFailure(
                audit.ErrorCode ?? "dicom_audit_failed",
                audit.SafeMessage
                    ?? "DICOMの内容を確認できませんでした。",
                audit.OperationId,
                stage: "audit");
            return false;
        }
        _dicomAudit = audit;
        _selectedDicomCandidate = audit.Candidates[0];
        AddSafeDicomEvent(
            "audit_completed",
            audit.OperationId,
            $"clean_candidate_count={audit.Candidates.Count}");
        return await ConvertDicomCandidateAsync(
            audit.Candidates[0],
            userSelected: false,
            rollbackState.InputPath,
            rollbackState);
    }

    private async Task<bool> ConvertDicomCandidateAsync(
        DicomCleanCandidate candidate,
        bool userSelected,
        string? previousInput = null,
        DicomSelectionSnapshot? rollbackState = null)
    {
        if (_dicomSession is null || _dicomAudit is null)
        {
            ShowDicomFailure(
                "dicom_audit_invalid",
                "DICOM確認結果を使用できません。",
                null,
                stage: "convert");
            return false;
        }
        _dicomStopRequested = false;
        previousInput ??= _inputPath;
        BeginDicomProgress(
            "CT取り込み中",
            "撮影データをプレビューに使える形に準備しています。プレビュー作成はまだ開始していません。",
            "準備が終わると3Dプレビューを作成できる状態になります。");
        DicomConversionResult conversion;
        try
        {
            conversion = await _dicomSession.ConvertCleanAsync(
                _dicomAudit,
                candidate,
                userSelected);
        }
        finally
        {
            EndDicomProgress();
        }
        if (_dicomStopRequested)
        {
            if (rollbackState is null)
            {
                _inputPath = previousInput;
            }
            else
            {
                RestoreDicomSelection(rollbackState);
            }
            ShowDicomFailure(
                "dicom_conversion_cancelled",
                "DICOMの変換を停止しました。",
                conversion.OperationId,
                stage: "convert");
            return false;
        }
        if (!conversion.Succeeded
            || string.IsNullOrWhiteSpace(conversion.NiftiPath))
        {
            if (rollbackState is null)
            {
                _inputPath = previousInput;
            }
            else
            {
                RestoreDicomSelection(rollbackState);
            }
            ShowDicomFailure(
                conversion.ErrorCode
                    ?? "dicom_conversion_failed",
                conversion.SafeMessage
                    ?? "DICOMを3D作成用データへ変換できませんでした。",
                conversion.OperationId,
                stage: "convert");
            return false;
        }

        rollbackState?.Session?.Dispose();
        _lastDicomFailure = null;
        _lastDicomConversion = conversion;
        _selectedDicomCandidate = candidate;
        _inputPath = conversion.NiftiPath;
        InputDisplayName.Text = "DICOM CT（取り込み済み）";
        SetInputButtonsForSample(sampleSelected: false);
        RunButton.IsEnabled = true;
        SampleNotice.Visibility = Visibility.Collapsed;
        InputSourceChoicePanel.Visibility = Visibility.Collapsed;
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        DicomSeriesListBox.ItemsSource = _dicomAudit.Candidates;
        DicomSeriesListBox.SelectedItem = candidate;
        DicomSeriesSummaryText.Text =
            $"使用する撮影: {candidate.DisplayTitle}";
        DicomSeriesSummaryPanel.Visibility =
            _dicomAudit.Candidates.Count > 1
                ? Visibility.Visible
                : Visibility.Collapsed;
        AddSafeDicomEvent(
            "conversion_completed",
            conversion.OperationId,
            $"selection_basis={conversion.SelectionBasis}");
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: false);
        SetScreen(
            ShellScreen.Input,
            "プレビュー作成準備完了");
        StatusPillText.Text = "プレビュー作成準備完了";
        return true;
    }

    private DicomSelectionSnapshot CaptureDicomSelection()
    {
        return new DicomSelectionSnapshot(
            _dicomSession,
            _dicomAudit,
            _selectedDicomCandidate,
            _lastDicomConversion,
            _inputPath);
    }

    private void RestoreDicomSelection(
        DicomSelectionSnapshot snapshot)
    {
        if (!ReferenceEquals(_dicomSession, snapshot.Session))
        {
            _dicomSession?.Dispose();
        }
        _dicomSession = snapshot.Session;
        _dicomAudit = snapshot.Audit;
        _selectedDicomCandidate = snapshot.SelectedCandidate;
        _lastDicomConversion = snapshot.Conversion;
        _inputPath = snapshot.InputPath;
    }

    private void BeginDicomProgress(
        string title,
        string detail,
        string subProgress)
    {
        _dicomOperationActive = true;
        _lastResult = null;
        StopButton.IsEnabled = true;
        SetStopButtonRequested(requested: false);
        RunProgressBar.IsIndeterminate = true;
        RunProgressBar.Value = 0;
        RunningTitle.Text = title;
        RunningDetail.Text = detail;
        OverallProgressText.Text = "全体: CTデータを準備中";
        SubProgressText.Text = subProgress;
        DeviceText.Text =
            "プレビュー作成はまだ開始していません。CPU fallbackは行いません。";
        RunningOutputText.Text = "保存先: DICOM取り込み用の一時領域";
        _runStartedUtc = DateTime.UtcNow;
        UpdateElapsed();
        _elapsedTimer.Start();
        SetScreen(ShellScreen.Running, title);
    }

    private void EndDicomProgress()
    {
        _dicomOperationActive = false;
        _elapsedTimer.Stop();
        StopButton.IsEnabled = true;
        SetStopButtonRequested(requested: false);
    }

    private bool RequestDicomStop()
    {
        if (!_dicomOperationActive || _dicomSession is null)
        {
            return false;
        }
        _dicomStopRequested = true;
        _dicomSession.RequestCancel();
        return true;
    }

    private void ShowDicomFailure(
        string errorCode,
        string safeMessage,
        string? operationId,
        string stage)
    {
        var cancelled = errorCode.EndsWith(
            "_cancelled",
            StringComparison.Ordinal);
        _lastResult = null;
        _lastDicomFailure = new DicomUiFailure(
            operationId ?? Guid.NewGuid().ToString("D"),
            errorCode,
            safeMessage,
            stage,
            cancelled);
        AddSafeDicomEvent(
            cancelled ? "intake_cancelled" : "intake_failed",
            _lastDicomFailure.OperationId,
            $"error_code={errorCode}");
        OpenPreviewButton.Visibility = Visibility.Collapsed;
        OpenOutputButton.Visibility = Visibility.Collapsed;
        CopyErrorButton.Visibility = Visibility.Visible;
        SetCopyInformationButtonForCancellation(cancelled);
        ResultErrorCode.Visibility = Visibility.Visible;
        ResultErrorCode.Text = cancelled
            ? $"reason_code={errorCode}"
            : $"error_code={errorCode}";
        ResultTitle.Text = cancelled
            ? "CT取り込みを停止しました"
            : "CTを取り込めませんでした";
        ResultReason.Text =
            $"{safeMessage} 入力は変更されていません。{DicomRecoveryMessage(errorCode)}";
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: true);
        SafeEventLogTextBox.Text = string.Join(
            Environment.NewLine,
            _safeEventLog);
        SetDetailsExpanded(expanded: false);
        SetScreen(
            ShellScreen.Result,
            cancelled
                ? "停止しました"
                : "CT取り込みを完了できませんでした");
    }

    private bool TryCopyDicomFailure()
    {
        if (_lastDicomFailure is null)
        {
            return false;
        }
        Clipboard.SetText(
            string.Join(
                Environment.NewLine,
                _lastDicomFailure.Cancelled
                    ? "status=cancelled"
                    : "status=failed",
                _lastDicomFailure.Cancelled
                    ? $"reason_code={_lastDicomFailure.ErrorCode}"
                    : $"error_code={_lastDicomFailure.ErrorCode}",
                $"operation_id={_lastDicomFailure.OperationId}",
                $"stage={_lastDicomFailure.Stage}",
                "segmentation_started=false"));
        return true;
    }

    private static string DicomRecoveryMessage(string errorCode)
    {
        return errorCode switch
        {
            "dicom_clean_series_unavailable" =>
                "DICOMファイルを直接含む最内側のフォルダ、または元の通常CTを書き出したフォルダを選んでください。",
            "dicom_input_unavailable" =>
                "ローカルに保存済みのDICOMフォルダを選び直してください。",
            "dicom_runtime_unavailable"
                or "dicom_normalizer_start_failed" =>
                    "アプリに同梱されたDICOM読み込み機能を確認してから、もう一度お試しください。",
            _ when errorCode.EndsWith(
                "_cancelled",
                StringComparison.Ordinal) =>
                    "必要なら別のCTを選ぶか、もう一度取り込みを開始してください。",
            _ =>
                "別のCTを選ぶか、同梱機能を確認してからもう一度お試しください。",
        };
    }

    private void AddSafeDicomEvent(
        string eventName,
        string? operationId,
        string detail)
    {
        _safeEventLog.Add(
            string.Join(
                " | ",
                $"event={eventName}",
                $"operation_id={operationId ?? "unknown"}",
                detail,
                "raw_output_forwarded=false",
                "segmentation_started=false"));
        SafeEventLogTextBox.Text = string.Join(
            Environment.NewLine,
            _safeEventLog);
    }

    private void ClearDicomSelection()
    {
        _dicomSession?.Dispose();
        _dicomSession = null;
        _dicomAudit = null;
        _selectedDicomCandidate = null;
        _lastDicomConversion = null;
        _lastDicomFailure = null;
        _dicomOperationActive = false;
        _dicomStopRequested = false;
        InputSourceChoicePanel.Visibility = Visibility.Collapsed;
        DicomSeriesSummaryPanel.Visibility = Visibility.Collapsed;
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        DicomSeriesListBox.ItemsSource = null;
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: false);
    }

    private void SetInputButtonsForNoInput()
    {
        const string chooseLabel = "CTデータを選ぶ";
        ChooseInputButton.Content = chooseLabel;
        AutomationProperties.SetName(
            ChooseInputButton,
            chooseLabel);
        const string runLabel =
            "このCTで3Dプレビューを作る";
        RunButton.Content = runLabel;
        AutomationProperties.SetName(RunButton, runLabel);
    }

    private void SetReturnToInputButtonForDicomFailure(
        bool dicomFailure)
    {
        var label = dicomFailure
            ? "別のCTを選ぶ"
            : "入力と作成内容へ戻る";
        ReturnToInputButton.Content = label;
        AutomationProperties.SetName(
            ReturnToInputButton,
            label);
    }

    private bool ReturnFromDicomFailure()
    {
        if (_lastDicomFailure is null)
        {
            return false;
        }
        _lastDicomFailure = null;
        SetReturnToInputButtonForDicomFailure(
            dicomFailure: false);
        BeginOwnDataSelection(clearExistingInput: false);
        return true;
    }

    private void ApplyDicomPreviewScenario(string scenario)
    {
        switch (scenario)
        {
            case "dicom-input":
                BeginOwnDataSelection(clearExistingInput: true);
                break;
            case "dicom-series":
                _inputPath = "preview-input.nii";
                InputDisplayName.Text = "DICOM CT（取り込み済み）";
                SetInputButtonsForSample(sampleSelected: false);
                RunButton.IsEnabled = true;
                SampleNotice.Visibility = Visibility.Collapsed;
                DicomSeriesSummaryText.Text =
                    "使用する撮影: 撮影 3: 通常CT";
                DicomSeriesSummaryPanel.Visibility =
                    Visibility.Visible;
                DicomSeriesListBox.ItemsSource = new[]
                {
                    new DicomCleanCandidate(
                        "preview-1",
                        "preview",
                        "preview-series-3",
                        3,
                        "通常CT",
                        161,
                        "original_ct_geometry_ok"),
                    new DicomCleanCandidate(
                        "preview-2",
                        "preview",
                        "preview-series-5",
                        5,
                        "通常CT",
                        154,
                        "original_ct_geometry_ok"),
                };
                DicomSeriesListBox.SelectedIndex = 0;
                DicomSeriesSelectionPanel.Visibility =
                    Visibility.Visible;
                SetScreen(
                    ShellScreen.Input,
                    "プレビュー作成準備完了");
                Dispatcher.BeginInvoke(
                    () =>
                    {
                        InputPanel.ScrollToTop();
                        DicomSeriesListBox.Focus();
                    },
                    System.Windows.Threading.DispatcherPriority.ContextIdle);
                break;
            default:
                throw new ArgumentException(
                    "The DICOM UI preview scenario is not supported.");
        }
    }

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
        if (converted)
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
            && coordinatorWasNotStartedDuringIntake
            && manifestExists
            && niftiExists
            && result?.TerminalEvent == "operation_completed"
            && result.SupervisorExitCode == 0
            && result.RequestedPolicy == "cuda_required"
            && result.RequestedDeviceIndex == 0
            && result.ResolvedDevice == "cuda:0"
            && result.FallbackAllowed == false
            && result.FallbackOccurred == false;
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
                coordinator_started_during_intake =
                    !coordinatorWasNotStartedDuringIntake,
                explicit_run_invoked = converted,
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
}

internal sealed record DicomUiFailure(
    string OperationId,
    string ErrorCode,
    string SafeMessage,
    string Stage,
    bool Cancelled);

internal sealed record DicomSelectionSnapshot(
    DicomIntakeSession? Session,
    DicomAuditResult? Audit,
    DicomCleanCandidate? SelectedCandidate,
    DicomConversionResult? Conversion,
    string? InputPath);
