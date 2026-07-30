# Completion audit

Date: 2026-07-31

| Criterion | Result | Evidence |
| --- | --- | --- |
| manual screenshot 01〜16をすべて個別に確認 | PASS | `verification-report.md`の16行 |
| 現WPFの対応画面を固定captureで確認 | PASS | `*-windows-*.png` |
| Swift/manualの主文言とbuttonを照合 | PASS | manual本文、履歴Swift、WPF contract |
| 同梱Sample previewを処理なしで開く | PASS | local `surface_preview/index.html`のみ |
| unknown／known progress表示 | PASS | 07、15のWPF capture |
| 成功／失敗から別CTへ移動 | PASS | 09、10のWPF capture |
| 成功後に同一入力を再実行 | PASS | 09のWPF capture、`RerunButton_Click` |
| clean DICOMから実MPRを生成・検証して表示 | PASS | MSVC Release build、`dicom_normalizer_synthetic` CTest、08 capture |
| fake CT／mock inferenceを成功扱いしない | PASS | 固定captureは画像を埋めず、primary pathは検証済みPGMのみを読込 |
| public indexや追加model取得を開始しない | PASS | 12、13を配布設計差として維持 |
| 34 buttonのAutomation nameとfocus | PASS | `ui-contract.json` |
| DICOM rescue同色slider連動 | PASS | `rescue_sliders_linked=true` |
| Windows 11 | UNVERIFIED | 別機検証 |
| installer／signing／update | UNVERIFIED | 範囲外 |

Blocking FAIL: 0
