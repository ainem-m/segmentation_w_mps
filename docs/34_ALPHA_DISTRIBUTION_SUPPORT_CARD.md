# 34 Alpha Distribution Support Card

> **Historical document:** this card records the 0.1.2 distribution only. It
> must not be used for the current 0.4.1 release. Current builds require an
> Apple Silicon Mac running macOS 14 or later; the macOS 13 value below is
> retained solely as historical evidence.

TotalSegmentator Wrapper for Mac `0.1.2` の外部紹介・返事待ち中に使う配布カードと
問い合わせ対応表。新機能追加ではなく、配布体験と失敗時対応を安定させるための
運用メモ。

## 配布カード

| Item | Value |
| --- | --- |
| 配布物 | `dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg` |
| SHA256 | `636d0e071dd68a60f13054165c4ef8ab7ef3f51ba535231128759810e5264a3a` |
| Size | `61.7 MiB` / `64673701` bytes |
| Version | `0.1.2` |
| Bundle ID | `jp.chino.totalsegmentator.wrapper.mac` |
| Signing | Developer ID / hardened runtime |
| Notarization | Apple notarized / stapled DMG |
| Public app page | `https://totalsegmentator.lacramy.com/` |
| Lacramy apps hub | `https://app.lacramy.com/` |
| Cloudflare Pages | `cloudflare/pages/` for app page, `cloudflare/app-hub/` for hub |
| R2 update manifest | `https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json` |
| 対応環境（0.1.2当時） | Apple Silicon Mac, macOS 13以降を想定。現行0.4.1は macOS 14以降。 |
| 同梱Sample | Sample 1 CT input, precomputed 3D HTML preview |
| 初回Setup | App Support配下に専用runtimeを作成 |
| 初回Setup所要時間 | Python依存は同梱wheelから導入。モデル取得時間は環境・通信状況により数分以上 |
| 通信 | Python依存はアプリに同梱。セットアップ中にネットワークを使用するのはモデルweightの取得だけ |
| 送信しないもの | DICOM, CT, 処理結果, ローカルpath, ログ, ユーザー識別子 |
| Sample 1処理時間 | model取得済み/MPS利用時も入力の大きさやMacの状態により数分以上かかる場合あり |
| 注意 | 研究・教育目的の非臨床プレビューです。医療機器ではなく、診断、治療方針の決定、治療計画、またはその他の医療上の判断には使用できません。 |

共有時の短い説明:

```text
TotalSegmentator Wrapper for Macは、Apple Silicon Mac上でCBCT/CTの非臨床3D previewを作るalpha版です。
DMGはDeveloper ID署名・notarized済みです。DICOM/CT/処理結果は送信しません。
初回SetupではPython依存を同梱wheelからオフライン導入し、初回実行に必要なmodel weightだけを取得します。
まずは同梱Sample 1の3D previewと3Dプレビュー作成を試してください。
```

## フィードバック質問

返答依頼は3問中心にする。回答負担を増やさないため、自由記述は任意にする。

1. 起動できましたか？
2. Sample 1の3D previewを開けましたか？
3. 自分のCTで、`CT選択 → 3Dプレビュー作成 → 3D確認` のどこまで進みましたか？

任意:

```text
怖かった点、迷った点、表示文言で分かりにくかった点があれば教えてください。
```

## 失敗時の案内

まず試す順番:

1. アプリを終了して再起動する。
2. Setup画面が出る場合は、もう一度Setupを実行する。
3. DMGから `TotalSegmentator Wrapper for Mac.app` をもう一度コピーする。
4. DMG内の `Collect TotalSegmentator Wrapper Logs.command` を実行してログを回収する。

自分のCTが読めない場合に確認するもの:

```text
1. アプリ画面のスクリーンショット
2. 詳細ログ、またはログ回収ファイル
3. そのCTを書き出したソフト名
```

ログ共有時の注意:

```text
ログにはローカルpathや実行状況が含まれる場合があります。共有前に内容を確認してください。
```

## 問い合わせ分類表

| 問い合わせ | まず見るもの | 想定原因 | 次アクション |
| --- | --- | --- | --- |
| 起動できない | macOS警告表示、`Collect TotalSegmentator Wrapper Logs.command` の出力 | アプリのコピー不完全、古いGatekeeper cache、壊れたDMG展開 | DMGから再コピー。改善しなければログ回収 |
| Setupが進まない | Setup画面のreason、`launcher.log`, `setup_state.json` | ネットワーク、MPS不可、runtime install失敗、App Support権限 | ネットワーク確認、再Setup、必要ならDMG再コピー |
| Sampleは動くが自分のCTが読めない | スクリーンショット、詳細ログ、CTを書き出したソフト名 | CTを見るソフトから「表示用の断面画像」として書き出されたデータ、複数series、geometry不足、compressed | CT画像そのものが壊れているとは限らない。対応できる場合があるため、ログ内容を確認してから連絡してもらう |
| プレビュー作成が失敗する | case outputの `logs/run.log`, `logs/benchmark.json` | model取得失敗、MPS不可、TotalSegmentator実行失敗、入力volume不適合 | 再実行前にログ確認。Sample 1でも失敗するかで環境問題と入力問題を切り分け |
| 3D previewが開かない | `surface_preview/index.html`, `logs/run.log` | surface-preview未生成、ブラウザ起動失敗、CT解析は成功したが3D作成失敗 | 結果画面の `3Dプレビューを再生成` を使う。だめなら結果フォルダ内を確認 |

## 最終配布前チェック

配布前に1回だけ確認する。

```bash
xcrun stapler validate "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg"
spctl --assess --type open --context context:primary-signature --verbose=4 \
  "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg"
```

DMGをmountしてappも確認する。

```bash
MOUNT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/totalsegmentator-wrapper-mac-release-check.XXXXXX")"
hdiutil attach "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg" -nobrowse -readonly -mountpoint "$MOUNT_ROOT"
spctl --assess --type execute --verbose=4 "$MOUNT_ROOT/TotalSegmentator Wrapper for Mac.app"
hdiutil detach "$MOUNT_ROOT"
```

手動確認:

- Sample 1の3D previewを開ける。
- Sample 1で3D preview作成を開始できる。
- DICOMフォルダ選択時にCT intakeが先に走り、直接プレビュー作成へ進まない。
- 結果画面から3D preview再生成・詳細ログ・結果フォルダを開ける。

## release evidence

- `notarytool submit`: `Accepted`, submission `a325757c-234e-4324-a542-6f9450469b83`
- `stapler validate`: 成功
- `spctl` DMG: `accepted`, `source=Notarized Developer ID`
- `spctl` mounted app: `accepted`, `source=Notarized Developer ID`
- `shasum -a 256`: `636d0e071dd68a60f13054165c4ef8ab7ef3f51ba535231128759810e5264a3a`
- R2 metadata: `cloudflare/r2/releases/0.1.2/` と `cloudflare/r2/releases/stable/update.json`
- `scripts/verify_zero_env_mac_dmg.sh`: pass
- zero-env setup: `status=success`, `actual_device=mps`, DICOM normalizer `app_bundle`
- zero-env dependency install: `111.0` 秒
- zero-env model weight準備: `130.9` 秒
- `unittest` with distribution dependency env: `130 tests OK`
