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
    private DicomRescueCandidate? _selectedDicomRescueCandidate;
    private DicomConversionResult? _lastDicomConversion;
    private DicomRescueResult? _lastDicomRescue;
    private DicomSpacing? _initialDicomRescueSpacing;
    private DicomUiFailure? _lastDicomFailure;
    private bool _dicomOperationActive;
    private bool _dicomStopRequested;
    private bool _synchronizingRescueSpacingControls;
    private bool _returnToDicomPreviewOnSeriesClose;

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
        _returnToDicomPreviewOnSeriesClose = false;
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
        if (_returnToDicomPreviewOnSeriesClose
            && _lastDicomConversion is not null
            && _selectedDicomCandidate is not null)
        {
            _returnToDicomPreviewOnSeriesClose = false;
            ShowDicomPreview(
                _lastDicomConversion,
                _selectedDicomCandidate);
            return;
        }
        ChangeDicomSeriesButton.Focus();
    }

    private void ViewOtherDicomSeriesButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_dicomAudit is null
            || _selectedDicomCandidate is null
            || _dicomAudit.Candidates.Count <= 1)
        {
            return;
        }
        _returnToDicomPreviewOnSeriesClose = true;
        SetScreen(
            ShellScreen.Input,
            "使用する撮影を確認");
        DicomSeriesListBox.ItemsSource = _dicomAudit.Candidates;
        DicomSeriesListBox.SelectedItem = _selectedDicomCandidate;
        DicomSeriesSelectionPanel.Visibility = Visibility.Visible;
        Dispatcher.BeginInvoke(DicomSeriesListBox.Focus);
    }

    private void UseDisplayedDicomPreviewButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        CommitDicomConversion();
    }

    private void ChooseAnotherDicomFolderFromPreviewButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        _returnToDicomPreviewOnSeriesClose = false;
        ChooseDicomFolderButton_Click(sender, e);
    }

    private void ShowRescueReasonButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        var expanded =
            RescueReasonPanel.Visibility != Visibility.Visible;
        RescueReasonPanel.Visibility = expanded
            ? Visibility.Visible
            : Visibility.Collapsed;
        ShowRescueReasonButton.Content = expanded
            ? "理由を閉じる"
            : "理由を見る";
        AutomationProperties.SetName(
            ShowRescueReasonButton,
            expanded
                ? "形状候補の理由を閉じる"
                : "形状候補の理由を見る");
    }

    private void ChooseAnotherCtFromRescueButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        BeginOwnDataSelection(clearExistingInput: true);
    }

    private void ResetRescueSpacingButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_initialDicomRescueSpacing is null)
        {
            return;
        }
        SetRescueSpacingControls(_initialDicomRescueSpacing);
        RescueValidationText.Text =
            "候補値へ戻しました。確認後に確認画像を作ってください。";
    }

    private void RescueCandidateComboBox_SelectionChanged(
        object sender,
        System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (RescueCandidateComboBox.SelectedItem
            is not DicomRescueCandidate candidate)
        {
            return;
        }
        _selectedDicomRescueCandidate = candidate;
        _initialDicomRescueSpacing = candidate.InitialSpacing;
        SetRescueSpacingControls(candidate.InitialSpacing);
        RescueReasonText.Text = candidate.SliceThickness.HasValue
            ? "X/Yは編集用の仮初期値です。ZはDICOMに残っているスライス厚を候補に使います。推定の確かさ: 低。"
            : "X/Y/Zはすべて編集用の仮初期値です。寸法は確認できていません。推定の確かさ: 不明。";
        ClearRescuePreview();
    }

    private async void CreateRescuePreviewButton_Click(
        object sender,
        RoutedEventArgs e)
    {
        if (_dicomSession is null
            || _dicomAudit is null
            || _selectedDicomRescueCandidate is null)
        {
            ShowDicomFailure(
                "dicom_rescue_candidate_invalid",
                "形状を確認する撮影を選択してください。",
                _dicomAudit?.OperationId,
                stage: "rescue");
            return;
        }
        if (!TryReadRescueSpacing(out var spacing))
        {
            RescueValidationText.Text =
                "X、Y、Zのスライダー位置を確認してください。";
            RescueSpacingXSlider.Focus();
            return;
        }

        _dicomStopRequested = false;
        BeginDicomProgress(
            "確認画像を作成中",
            "確定した寸法でpseudo-volumeと三方向画像を作成しています。AI推論は開始しません。",
            "形状と寸法のreadbackを確認しています。");
        DicomRescueResult result;
        try
        {
            result = await _dicomSession.PrepareRescueAsync(
                _dicomAudit,
                _selectedDicomRescueCandidate,
                spacing);
        }
        finally
        {
            EndDicomProgress();
        }
        if (_dicomStopRequested)
        {
            ShowDicomFailure(
                "dicom_rescue_cancelled",
                "確認画像の作成を停止しました。",
                result.OperationId,
                stage: "rescue");
            return;
        }
        if (!result.Succeeded)
        {
            ShowDicomFailure(
                result.ErrorCode ?? "dicom_rescue_failed",
                result.SafeMessage
                    ?? "確認画像を作成できませんでした。",
                result.OperationId,
                stage: "rescue");
            return;
        }
        try
        {
            ShowRescuePreviews(result.Previews);
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or InvalidDataException
                or ArgumentException
                or OverflowException)
        {
            ShowDicomFailure(
                "dicom_rescue_preview_invalid",
                "作成した三方向画像を表示できませんでした。",
                result.OperationId,
                stage: "rescue");
            return;
        }
        _lastDicomRescue = result;
        AddSafeDicomEvent(
            "rescue_preview_completed",
            result.OperationId,
            "preview_count=3");
        RescueValidationText.Text =
            $"確認済み寸法: X {spacing.X:0.####} / Y {spacing.Y:0.####} / Z {spacing.Z:0.####} mm";
        RescuePreviewStatusText.Text =
            "三方向の確認画像を作成しました。Secondary Captureの救済用pseudo-volumeであり、通常CTとして扱いません。AI推論は開始していません。";
        SetScreen(
            ShellScreen.DicomRescue,
            "確認画像を作成しました");
    }

    private void ShowDicomRescue(
        DicomRescueCandidate candidate)
    {
        _inputPath = null;
        RunButton.IsEnabled = false;
        RescueCandidateComboBox.ItemsSource =
            _dicomAudit?.RescueCandidates;
        RescueCandidateComboBox.SelectedItem = candidate;
        _selectedDicomRescueCandidate = candidate;
        _initialDicomRescueSpacing = candidate.InitialSpacing;
        SetRescueSpacingControls(candidate.InitialSpacing);
        ClearRescuePreview();
        RescueReasonPanel.Visibility = Visibility.Collapsed;
        ShowRescueReasonButton.Content = "理由を見る";
        AutomationProperties.SetName(
            ShowRescueReasonButton,
            "形状候補の理由を見る");
        SetScreen(
            ShellScreen.DicomRescue,
            "推定の確かさ: 低");
    }

    private void SetRescueSpacingControls(DicomSpacing spacing)
    {
        var estimate = _initialDicomRescueSpacing ?? spacing;
        _synchronizingRescueSpacingControls = true;
        try
        {
            SetRescueAxisSliderValues(
                RescueSpacingXSlider,
                RescueSpacingXCoronalSlider,
                ToRescueSliderPosition(spacing.X, estimate.X));
            SetRescueAxisSliderValues(
                RescueSpacingYSlider,
                RescueSpacingYSagittalSlider,
                ToRescueSliderPosition(spacing.Y, estimate.Y));
            SetRescueAxisSliderValues(
                RescueSpacingZSlider,
                RescueSpacingZSagittalSlider,
                ToRescueSliderPosition(spacing.Z, estimate.Z));
        }
        finally
        {
            _synchronizingRescueSpacingControls = false;
        }
        UpdateRescueSpacingAccessibility();
    }

    private bool TryReadRescueSpacing(out DicomSpacing spacing)
    {
        var estimate = _initialDicomRescueSpacing;
        if (estimate is null)
        {
            spacing = new DicomSpacing(0, 0, 0);
            return false;
        }
        spacing = new DicomSpacing(
            RescueSpacingFromSlider(
                RescueSpacingXSlider.Value,
                estimate.X),
            RescueSpacingFromSlider(
                RescueSpacingYSlider.Value,
                estimate.Y),
            RescueSpacingFromSlider(
                RescueSpacingZSlider.Value,
                estimate.Z));
        return spacing.IsValid;
    }

    private void RescueSpacingSlider_ValueChanged(
        object sender,
        RoutedPropertyChangedEventArgs<double> e)
    {
        if (_synchronizingRescueSpacingControls
            || RescueSpacingXSlider is null
            || RescueSpacingXCoronalSlider is null
            || RescueSpacingYSlider is null
            || RescueSpacingYSagittalSlider is null
            || RescueSpacingZSlider is null
            || RescueSpacingZSagittalSlider is null)
        {
            return;
        }
        _synchronizingRescueSpacingControls = true;
        try
        {
            if (ReferenceEquals(sender, RescueSpacingXSlider))
            {
                RescueSpacingXCoronalSlider.Value = e.NewValue;
            }
            else if (ReferenceEquals(
                sender,
                RescueSpacingXCoronalSlider))
            {
                RescueSpacingXSlider.Value = e.NewValue;
            }
            else if (ReferenceEquals(sender, RescueSpacingYSlider))
            {
                RescueSpacingYSagittalSlider.Value = e.NewValue;
            }
            else if (ReferenceEquals(
                sender,
                RescueSpacingYSagittalSlider))
            {
                RescueSpacingYSlider.Value = e.NewValue;
            }
            else if (ReferenceEquals(sender, RescueSpacingZSlider))
            {
                RescueSpacingZSagittalSlider.Value = e.NewValue;
            }
            else if (ReferenceEquals(
                sender,
                RescueSpacingZSagittalSlider))
            {
                RescueSpacingZSlider.Value = e.NewValue;
            }
        }
        finally
        {
            _synchronizingRescueSpacingControls = false;
        }
        UpdateRescueSpacingAccessibility();
        if (_lastDicomRescue is not null)
        {
            ClearRescuePreview();
            RescueValidationText.Text =
                "形状を変更しました。もう一度、確認画像を作ってください。";
        }
    }

    private static void SetRescueAxisSliderValues(
        System.Windows.Controls.Slider first,
        System.Windows.Controls.Slider second,
        double value)
    {
        first.Value = value;
        second.Value = value;
    }

    private static double ToRescueSliderPosition(
        double spacing,
        double estimate)
    {
        return Math.Clamp(
            Math.Log2(
                Math.Max(spacing, 0.01)
                / Math.Max(estimate, 0.01)),
            -2,
            2);
    }

    private static double RescueSpacingFromSlider(
        double position,
        double estimate)
    {
        return Math.Clamp(
            Math.Max(estimate, 0.01) * Math.Pow(2, position),
            0.01,
            20);
    }

    private void UpdateRescueSpacingAccessibility()
    {
        SetRescueSliderAccessibility(
            RescueSpacingXSlider,
            RescueSpacingXCoronalSlider,
            RescueSpacingXSlider.Value);
        SetRescueSliderAccessibility(
            RescueSpacingYSlider,
            RescueSpacingYSagittalSlider,
            RescueSpacingYSlider.Value);
        SetRescueSliderAccessibility(
            RescueSpacingZSlider,
            RescueSpacingZSagittalSlider,
            RescueSpacingZSlider.Value);
    }

    private static void SetRescueSliderAccessibility(
        System.Windows.Controls.Slider first,
        System.Windows.Controls.Slider second,
        double position)
    {
        var percentage =
            $"{Math.Round(Math.Pow(2, position) * 100):0}パーセント";
        AutomationProperties.SetItemStatus(first, percentage);
        AutomationProperties.SetItemStatus(second, percentage);
    }

    internal bool RescueSliderContractSelfTest()
    {
        _initialDicomRescueSpacing = new DicomSpacing(1, 2, 4);
        SetRescueSpacingControls(_initialDicomRescueSpacing);

        RescueSpacingXSlider.Value = 0.5;
        RescueSpacingYSagittalSlider.Value = -0.5;
        RescueSpacingZSlider.Value = 1;

        return RescueSpacingXCoronalSlider.Value == 0.5
            && RescueSpacingYSlider.Value == -0.5
            && RescueSpacingZSagittalSlider.Value == 1
            && TryReadRescueSpacing(out var spacing)
            && Math.Abs(spacing.X - Math.Sqrt(2)) < 0.000_001
            && Math.Abs(spacing.Y - Math.Sqrt(2)) < 0.000_001
            && Math.Abs(spacing.Z - 8) < 0.000_001;
    }

    private void ShowRescuePreviews(
        IReadOnlyList<DicomMprPreview> previews)
    {
        RescueAxialImage.Source = LoadVerifiedPreview(
            previews.Single(item => item.Plane == "axial"));
        RescueCoronalImage.Source = LoadVerifiedPreview(
            previews.Single(item => item.Plane == "coronal"));
        RescueSagittalImage.Source = LoadVerifiedPreview(
            previews.Single(item => item.Plane == "sagittal"));
    }

    private static System.Windows.Media.Imaging.BitmapSource
        LoadVerifiedPreview(DicomMprPreview preview)
    {
        var image = PgmBitmapLoader.Load(preview.Path);
        if (image.PixelWidth != preview.Width
            || image.PixelHeight != preview.Height)
        {
            throw new InvalidDataException(
                "The PGM dimensions do not match the rescue manifest.");
        }
        return image;
    }

    private void ClearRescuePreview()
    {
        _lastDicomRescue = null;
        RescueAxialImage.Source = null;
        RescueCoronalImage.Source = null;
        RescueSagittalImage.Source = null;
        RescueValidationText.Text =
            "3枚を見比べながら形を確認してください。候補値は正確な寸法が確認できたことを意味しません。";
        RescuePreviewStatusText.Text =
            "確認画像はまだ作成されていません。AI推論は開始しません。";
    }

    private async Task<bool> AuditAndConvertDicomAsync(
        string dicomFolder)
    {
        var rollbackState = CaptureDicomSelection();
        _dicomStopRequested = false;
        _dicomSession = new DicomIntakeSession(_configuration);
        _dicomAudit = null;
        _selectedDicomCandidate = null;
        _selectedDicomRescueCandidate = null;
        _lastDicomConversion = null;
        _lastDicomRescue = null;
        _initialDicomRescueSpacing = null;
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
        if (audit.Candidates.Count == 0)
        {
            _selectedDicomRescueCandidate =
                audit.RescueCandidates[0];
            AddSafeDicomEvent(
                "audit_completed",
                audit.OperationId,
                $"rescue_candidate_count={audit.RescueCandidates.Count}");
            ShowDicomRescue(
                _selectedDicomRescueCandidate);
            return true;
        }

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
        AddSafeDicomEvent(
            "conversion_completed",
            conversion.OperationId,
            $"selection_basis={conversion.SelectionBasis}");
        return ShowDicomPreview(conversion, candidate);
    }

    private bool ShowDicomPreview(
        DicomConversionResult conversion,
        DicomCleanCandidate candidate)
    {
        if (conversion.Previews.Count != 3)
        {
            ShowDicomFailure(
                "dicom_mpr_preview_invalid",
                "CTの三方向画像を確認できませんでした。",
                conversion.OperationId,
                stage: "preview");
            return false;
        }
        try
        {
            DicomPreviewAxialImage.Source = LoadVerifiedPreview(
                conversion.Previews.Single(
                    item => item.Plane == "axial"));
            DicomPreviewCoronalImage.Source = LoadVerifiedPreview(
                conversion.Previews.Single(
                    item => item.Plane == "coronal"));
            DicomPreviewSagittalImage.Source = LoadVerifiedPreview(
                conversion.Previews.Single(
                    item => item.Plane == "sagittal"));
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or InvalidDataException
                or ArgumentException
                or OverflowException)
        {
            ShowDicomFailure(
                "dicom_mpr_preview_invalid",
                "CTの三方向画像を表示できませんでした。",
                conversion.OperationId,
                stage: "preview");
            return false;
        }
        DicomPreviewSeriesText.Text =
            _dicomAudit?.Candidates.Count > 1
                ? $"複数の撮影データがあります。表示中: {candidate.DisplayTitle}"
                : $"表示中: {candidate.DisplayTitle}";
        ViewOtherDicomSeriesButton.Visibility =
            _dicomAudit?.Candidates.Count > 1
                ? Visibility.Visible
                : Visibility.Collapsed;
        AddSafeDicomEvent(
            "dicom_preview_presented",
            conversion.OperationId,
            "preview_count=3");
        SetScreen(
            ShellScreen.DicomPreview,
            "CT画像を確認");
        return true;
    }

    private bool CommitDicomConversion()
    {
        var conversion = _lastDicomConversion;
        var candidate = _selectedDicomCandidate;
        if (_dicomAudit is null
            || conversion is null
            || candidate is null
            || string.IsNullOrWhiteSpace(conversion.NiftiPath))
        {
            return false;
        }
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
            "dicom_preview_confirmed",
            conversion.OperationId,
            "preview_count=3");
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
            _selectedDicomRescueCandidate,
            _lastDicomConversion,
            _lastDicomRescue,
            _initialDicomRescueSpacing,
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
        _selectedDicomRescueCandidate =
            snapshot.SelectedRescueCandidate;
        _lastDicomConversion = snapshot.Conversion;
        _lastDicomRescue = snapshot.Rescue;
        _initialDicomRescueSpacing =
            snapshot.InitialRescueSpacing;
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
        _selectedDicomRescueCandidate = null;
        _lastDicomConversion = null;
        _lastDicomRescue = null;
        _initialDicomRescueSpacing = null;
        _lastDicomFailure = null;
        _dicomOperationActive = false;
        _dicomStopRequested = false;
        _returnToDicomPreviewOnSeriesClose = false;
        InputSourceChoicePanel.Visibility = Visibility.Collapsed;
        DicomSeriesSummaryPanel.Visibility = Visibility.Collapsed;
        DicomSeriesSelectionPanel.Visibility = Visibility.Collapsed;
        DicomSeriesListBox.ItemsSource = null;
        RescueCandidateComboBox.ItemsSource = null;
        DicomPreviewAxialImage.Source = null;
        DicomPreviewCoronalImage.Source = null;
        DicomPreviewSagittalImage.Source = null;
        ClearRescuePreview();
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
            case "dicom-preview":
                var previewCandidate = new DicomCleanCandidate(
                    "preview-1",
                    "preview",
                    "preview-series-7",
                    7,
                    "Reconstructed volume",
                    512,
                    "original_ct_geometry_ok");
                _dicomAudit = DicomAuditResult.Success(
                    "preview",
                    "preview-workspace",
                    "preview-source",
                    "preview-audit.json",
                    new[] { previewCandidate },
                    Array.Empty<DicomRescueCandidate>());
                _selectedDicomCandidate = previewCandidate;
                DicomPreviewAxialImage.Source = null;
                DicomPreviewCoronalImage.Source = null;
                DicomPreviewSagittalImage.Source = null;
                DicomPreviewSeriesText.Text =
                    "表示中: 撮影 7: Reconstructed volume";
                ViewOtherDicomSeriesButton.Visibility =
                    Visibility.Collapsed;
                SetScreen(
                    ShellScreen.DicomPreview,
                    "UI PREVIEW・CT画像を確認");
                break;
            case "dicom-rescue":
                var rescueCandidate = new DicomRescueCandidate(
                    "preview-rescue-1",
                    "preview",
                    "preview-rescue-series",
                    200,
                    "AXIAL BO",
                    138,
                    "secondary_capture_rescue_candidate",
                    0.9375);
                _dicomAudit = DicomAuditResult.Success(
                    "preview",
                    "preview-workspace",
                    "preview-source",
                    "preview-audit.json",
                    Array.Empty<DicomCleanCandidate>(),
                    new[] { rescueCandidate });
                _selectedDicomRescueCandidate = rescueCandidate;
                ShowDicomRescue(rescueCandidate);
                RescuePreviewStatusText.Text =
                    "UI PREVIEW: 三方向画像の生成前です。AI推論は開始しません。";
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
                spacing);
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
        var niftiExists =
            rescue?.NiftiPath is { } niftiPath
            && File.Exists(niftiPath)
            && new FileInfo(niftiPath).Length > 0;
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
            && niftiExists
            && previewPlanes.SequenceEqual(
                new[] { "axial", "coronal", "sagittal" })
            && _lastResult is null
            && _inputPath is null;
        await WriteEvidenceAsync(
            evidencePath,
            new
            {
                schema =
                    "totalsegmentator_wrapper.windows_wpf_dicom_rescue_preview.v1",
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
                patched_nifti_nonempty =
                    niftiExists,
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
    DicomRescueCandidate? SelectedRescueCandidate,
    DicomConversionResult? Conversion,
    DicomRescueResult? Rescue,
    DicomSpacing? InitialRescueSpacing,
    string? InputPath);
