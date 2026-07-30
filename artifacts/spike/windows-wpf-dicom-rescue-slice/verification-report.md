# Windows WPF DICOM rescue slice verification

Date: 2026-07-31

## Scope

Windows 10 x64 の WPF で、`secondary_capture_rescue_candidate` を
clean CT と分離したまま、三方向画像の端に置いた連動スライダー確認から既存
`prepare-rescue` を実行し、pseudo-NIfTI と三方向 PGM を表示する
manual-only slice を検証した。

このsliceは TotalSegmentator を開始せず、rescue出力を通常CTへ昇格しない。

## Result

```text
status: PASS
source: synthetic non-patient Secondary Capture
series files: 32
selected spacing: 1.0 x 1.0 x 1.0 mm
normalizer exit code: 0
Job became empty: true
patched NIfTI non-empty: true
preview planes: axial, coronal, sagittal
coordinator started: false
segmentation started: false
promoted as clean CT: false
raw native output forwarded: false
```

Synthetic fixtureの画素は一様なため、3枚とも
`uniform_or_empty=true`である。pipelineとPGM表示の検証には使用したが、
実データの形状目視品質をPASS扱いにはしていない。

## Checks

- `dotnet build ... -c Release --no-restore`: PASS、警告0、エラー0
- WPF contract self-test: PASS、28 buttons、同色slider連動PASS
- `tests.test_windows_wpf_contract`: 10 tests PASS
- Python全テスト: 300 tests PASS、3 skipped
- `git diff --check`: PASS
- 実WPF -> Job Object -> native normalizer -> 実dcm2niix: PASS
- warning flags、spacing readback、非空NIfTI、3 PGM、safe manifest: PASS
- coordinator未起動、AI推論未開始、clean CT非昇格: PASS

Native synthetic suiteを既存の前工程binaryへ向けた補助実行では、
13 case通過後、Python形式fake dcm2niixの直接起動がWindows error 193で停止した。
今回変更していないC++の失敗ではなく、test sourceと既存binaryの世代差である。
今回のproduction-like経路は実`dcm2niix.exe`で別途PASSしている。

## UI wording and button comparison

| Swift/manual reference | Windows slice | Status |
| --- | --- | --- |
| `形状を確認` | `形状を確認` | PASS |
| `理由を見る` / `理由を閉じる` | 同じ表示とAutomation名 | PASS |
| 画像端のhandle | 三方向画像の横・縦に6本 | PASS |
| 同色handleの連動 | 青=X、緑=Y、橙=Z | PASS（contract self-test） |
| 推定値基準の対数scale | 25%〜400% | PASS |
| `推定形状に戻す` | `推定形状に戻す` | PASS |
| `別のCTを選ぶ` | `別のCTを選ぶ` | PASS |
| `この形状で作成` | `この形状で確認画像を作る` | intentional difference。推論を開始しないsliceのため |
| `画像の向きを修正` | なし | UNVERIFIED（次のtransform工程） |
| crop、rotation | なし | UNVERIFIED |

## Unverified

- 実Secondary Captureの三方向目視品質
- 画像向き、axis permutation、rotation、slice reversal、crop
- rescue出力からのTotalSegmentator接続
- Windows 11
- WPF以外のinstaller、signing、update/rollback
- DICOM diagnostic accuracy、clinical use
