# Windows manual screenshot audit

Date: 2026-07-31

## Scope

`docs/assets/user-manual/01-setup.png` から
`16-shape-confirmation.png` までを、Windows 10 x64 の現WPF実装、
固定UI preview、画像付きマニュアル本文、履歴上のSwift実装と照合した。

Mac固有の表示をそのまま複製することは目的にせず、画面名、主操作、
戻り方、状態表示、処理契約が一致するかを確認した。存在しない機能を
buttonだけで表現すること、fake画像をprimary pathの成功として扱うこと、
public indexから追加依存を取得することは行っていない。

## Result by screenshot

| # | Reference | Result | Windows comparison and action |
| --- | --- | --- | --- |
| 01 | 初回準備 | PASS | `はじめの準備`、待機状態、データの扱い、`準備を始める`を確認。Windowsはapp-private runtimeを検証し、再解決しない。Macのupdate機能はWindows範囲外。 |
| 02 | 開始 | PASS | 2カード構成に加え、同梱済みlocal assetを開く`Sample 1の3Dプレビューを開く`を追加。作成処理は開始しない。 |
| 03 | Sample入力 | PASS | Sample 1、標準／その他モデル、strict CUDA、主buttonを確認。Windowsは保存先をapp-private `runs`へ固定し、silent fallbackを許可しない。 |
| 04 | CT未選択（旧） | UNVERIFIED | manual manifestで未使用。Windowsでは開始カードからNIfTI/DICOM選択panelへ遷移するため独立画面を持たない。 |
| 05 | CT選択済み（旧） | UNVERIFIED | manual manifestで未使用。現行03の2カード構成へ統合済み。 |
| 06 | DICOM撮影選択 | PASS | 最初の候補、撮影名、枚数、`この撮影を使う`、`閉じる`を確認。Windowsは同一画面内のfocus panelでありMacのsheetとは外観が異なる。 |
| 07 | 通常処理中 | PASS | 工程、全体の進捗範囲、未知の工程進捗、経過時間、使用機能、保存先、停止を表示。固定previewをmanualのunknown-progress状態へ修正。 |
| 08 | CT画像確認 | PASS | clean DICOMから実NIfTIの三方向PGMを生成し、path・非空file・寸法・planeを.NET側で検証してから表示する画面を追加。上／正面／横、同一folderの別撮影、別folder、表示中撮影の確定を確認。固定UI captureはfake画像を埋めず黒枠とし、primary pathだけが検証済みPGMを表示する。 |
| 09 | 成功結果 | PASS | 3D preview、結果folder、safe詳細、入力へ戻る、別CT、再実行、最初へ戻るを確認。別CTと同一入力再実行を追加。Slicer export、保存file一覧、preview再生成はUNVERIFIED。 |
| 10 | 失敗結果 | PASS | safe reason/error code、copy、safe詳細、入力へ戻る、別CT、最初へ戻るを確認。別CT操作を追加。 |
| 11 | 詳細ログ（旧） | UNVERIFIED | manual manifestで未使用。Windowsは絶対pathやraw outputを表示しないsafe protocol event summaryを採用し、外部共有向けのログmodalは実装しない。 |
| 12 | 追加モデル確認 | UNVERIFIED | Windowsは社内app-private runtime/model rootを事前配置し、UIからdownload・依存再解決しない。未準備時はtyped readiness failureで停止する。 |
| 13 | 追加モデル準備中 | UNVERIFIED | 12と同じ配布設計差。認証・配布判断なしに取得UIを追加しない。 |
| 14 | 作成方法比較 | PASS | TotalSegmentator、DentalSegmentator、個別歯、ToothSegの画像、説明、選択状態、閉じる操作を確認。 |
| 15 | ToothSeg処理中 | PASS | 工程3/5、overall 60%、40/80、工程内50%、残り目安、strict CUDA、停止を固定previewへ反映。 |
| 16 | 形状確認 | PASS | 3画像の端に6 slider、青=X、緑=Y、橙=Z、同色連動、25%〜400%の対数scale、reset、別CT、確認画像生成を確認。orientation/cropとrescueから推論開始はUNVERIFIED。 |

## Changes made during this audit

- Start画面へ、同梱済みoffline previewを直接開くsecondary actionを追加。
- 結果画面へ、`別のCTを選ぶ`と`もう一度作成`を追加。
- 通常処理の固定previewをunknown-progress表示へ修正。
- ToothSeg固定previewへknown progress、step、ETAを追加。
- DICOM rescueの6本連動slider修正を再確認。
- clean DICOM変換へ、既存NIfTI readerを再利用した三方向PGM生成を追加。
- CT画像確認画面と、確認後にだけ変換NIfTIを入力へ確定する操作を追加。
- WPF automation nameとbutton contractを34 buttonへ更新。

## Deliberately unverified

- viewer export
- DICOM rescueのorientation、rotation、slice reversal、crop
- DICOM rescue確認後のstrict CUDA segmentation接続
- 3D Slicer export、保存file一覧、preview再生成
- Windows内での追加model download／update
- installer、signing、update／rollback
- Windows 11

## Verification

- .NET Release build: PASS（0 warnings / 0 errors）
- WPF contract self-test: PASS（34 buttons）
- Windows WPF contract tests: PASS（10/10）
- Python全テスト: PASS（300、skip 3）
- MSVC Release native build: PASS
- `dicom_normalizer_synthetic` CTest: PASS（実dcm2niix、synthetic DICOM）
- `git diff --check`: PASS

Blocking FAIL: 0
