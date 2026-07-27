# ユーザーマニュアル画像

このフォルダの画像は、`docs/USER_MANUAL_JA.md`から参照するSample/DEBUG用の画面画像です。

本文と画像の正本は次の組み合わせです。

- 本文：`docs/USER_MANUAL_JA.md`
- 画面実装：`native/macos/TotalSegmentatorWrapperForMac/Views.swift`
- 画面遷移：`native/macos/TotalSegmentatorWrapperForMac/AppState.swift`
- 画像一覧：このファイル

## 更新ルール

- 実患者データや実患者DICOMから画像を作らない。
- 画面変更時は、同じファイル名の画像を現行DEBUG画面で差し替える。
- 差し替え前に、画面名・主ボタン・戻り方が本文と一致することを確認する。
- 画像を追加・差し替えた日付と、確認したアプリ版を作業記録に残す。
- 本文で使わなくなった画像は、対応表で「未使用」と明記する。

## 対応表

| ファイル | 状態・用途 | 出典・データ区分 | 本文 |
| --- | --- | --- | --- |
| `01-setup.png` | 初回準備 | 配布アプリの空状態 | 使用中 |
| `02-start.png` | Sample／手元のCTの選択 | 固定UIハーネス | 使用中 |
| `03-input-sample.png` | 現行の2カード構成によるSample入力 | `ui-preview input` | 使用中 |
| `04-input-empty.png` | 旧CT未選択画面 | 固定UIハーネス | 未使用・再撮影候補 |
| `05-input-ct-ready.png` | 旧CT選択後画面 | 固定UIハーネス | 未使用・再撮影候補 |
| `06-dicom-series.png` | 使用する撮影の変更 | 固定UIハーネスの架空候補 | 使用中 |
| `07-running.png` | 現行の進行範囲表示を含む通常処理中 | `ui-preview running-unknown` | 使用中 |
| `08-ct-preview.png` | CT画像確認 | 同梱Sample 1の固定断面 | 使用中 |
| `09-result-success.png` | 成功結果 | 固定UIハーネス | 使用中 |
| `10-result-failure.png` | 失敗結果 | 固定UIハーネス | 使用中 |
| `11-detail-log.png` | 詳細ログ | 固定UIハーネス。ローカル検証用パス表示あり | 未使用 |
| `12-dental-confirmation.png` | 追加機能の確認 | 固定UIハーネス | 使用中 |
| `13-dental-preparing.png` | 追加機能の準備中 | 固定UIハーネス | 使用中 |
| `14-creation-comparison.png` | 作成方法の比較 | Sample比較用固定画像 | 使用中 |
| `15-toothseg-running.png` | ToothSeg処理中 | `ui-preview running-known` | 使用中 |
| `16-shape-confirmation.png` | 寸法情報がないCTの形状確認 | `ui-preview dicom-rescue`と固定Sample断面 | 使用中 |

## 再撮影チェック

1. DEBUG固定状態で対象画面を開く。
2. ウィンドウサイズ、ライトモード、表示倍率を既存画像と揃える。
3. `UI PREVIEW`表示以外に、患者情報やローカルパスがないことを確認する。
4. 見出し、主ボタン、戻る操作を`Views.swift`と照合する。
5. 自動遷移を`AppState.swift`と照合する。
6. 同じファイル名で画像を差し替える。
7. `docs/USER_MANUAL_JA.md`を開き、画像と本文を続けて読む。
