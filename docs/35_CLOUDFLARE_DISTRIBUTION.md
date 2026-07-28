# 35 Cloudflare Distribution

TotalSegmentator Wrapper for Mac の public alpha DMG を Cloudflare で配布し、
公開アプリの更新確認を stable channel へ向けるための準備メモ。

## 方針

Cloudflare Pages は公開ページだけに使い、DMG本体とupdate manifestは
Cloudflare R2に置く。

理由:

- `dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg` は
  `61.7 MiB` / `64673701` bytes。
- Cloudflare Pagesは単一assetの上限が `25 MiB`。CloudflareのPages limits
  docsでも、大きいファイルはR2 public bucketやcustom domainを検討するよう
  案内されている。
- app updaterは `download_url` と `release_notes_url` が manifest と同じ
  origin、またはbuild時allowlistのhostであることを要求する。余計なallowlistを
  増やさないため、update manifestもDMGと同じR2 custom domainへ置く。

参照:

- Cloudflare Pages limits: https://developers.cloudflare.com/pages/platform/limits/
- Cloudflare Pages Direct Upload: https://developers.cloudflare.com/pages/get-started/direct-upload/
- Cloudflare R2 public buckets: https://developers.cloudflare.com/r2/buckets/public-buckets/
- Cloudflare R2 get started: https://developers.cloudflare.com/r2/get-started/

## 予定するCloudflare構成

```text
Pages project:
  totalsegmentator-wrapper-mac

Pages deploy directory:
  cloudflare/pages/

Pages custom domain:
  totalsegmentator.lacramy.com

Apps hub Pages project:
  lacramy-apps

Apps hub deploy directory:
  cloudflare/app-hub/

Apps hub custom domain:
  app.lacramy.com

R2 bucket:
  lacramy-downloads

R2 custom domain:
  downloads.lacramy.com
```

`downloads.lacramy.com` はLacramy全体の共通download hostとして使う。
アプリごとの配布物はbucketを分けず、object key prefixで分ける。

```text
choioki/...
redact/...
totalsegmentator-wrapper-mac/...
```

本番配布では `r2.dev` ではなくR2 custom domainを使う。`r2.dev` は開発用途で、
rate limitや運用機能の制約がある。

## 配置するR2 object

```text
totalsegmentator-wrapper-mac/releases/0.1.2/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg
totalsegmentator-wrapper-mac/releases/0.1.2/SHA256SUMS.txt
totalsegmentator-wrapper-mac/releases/0.1.2/RELEASE_NOTES.txt
totalsegmentator-wrapper-mac/releases/0.1.2/release.json
totalsegmentator-wrapper-mac/releases/stable/update.json
totalsegmentator-wrapper-mac/releases/0.1.1/...  # rollback/history
totalsegmentator-wrapper-mac/releases/0.1.0/...  # rollback/history
totalsegmentator-wrapper-mac/releases/alpha/update.json
```

`totalsegmentator-wrapper-mac/releases/<version>/...` はversioned objectなので長期cache可。
DMGを作り直す場合は同じobject keyを上書きせず、日付やbuild id入りの一意な
filenameにする。immutable cacheが残るため、同じURLの上書きはmanifest SHAと
edge cacheの不整合を起こす。
`totalsegmentator-wrapper-mac/releases/stable/update.json` は公開アプリの更新確認入口、
`totalsegmentator-wrapper-mac/releases/alpha/update.json` は検証用入口なので、
どちらもcacheを短くするかbypassする。

## ローカル生成

release metadataを再生成する。

```bash
scripts/prepare_cloudflare_release.py \
  --version 0.1.2 \
  --channel stable \
  --minimum-supported-version 0.1.1 \
  --dmg "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg" \
  --download-origin https://downloads.lacramy.com \
  --bucket lacramy-downloads \
  --object-prefix totalsegmentator-wrapper-mac \
  --published-at 2026-07-09T03:05:39Z
```

生成物:

```text
cloudflare/r2/releases/stable/update.json
cloudflare/r2/releases/0.1.2/SHA256SUMS.txt
cloudflare/r2/releases/0.1.2/RELEASE_NOTES.txt
cloudflare/r2/releases/0.1.2/release.json
cloudflare/r2/upload-plan.json
```

公開ページは以下。`cloudflare/pages/` は TotalSegmentator Wrapper 専用ページ、
`cloudflare/app-hub/` は `app.lacramy.com` の複数アプリ入口として使う。

```text
cloudflare/pages/index.html
cloudflare/pages/_headers
cloudflare/pages/_redirects
cloudflare/pages/assets/benchmark-dentalseg.png
cloudflare/app-hub/index.html
cloudflare/app-hub/_headers
cloudflare/app-hub/_redirects
```

app page と hub の `/download` はどちらも
`downloads.lacramy.com/totalsegmentator-wrapper-mac/...` へredirectする。
`app.lacramy.com/totalsegmentator-wrapper-mac` は canonical app page の
`https://totalsegmentator.lacramy.com/` へredirectする。

## notarized DMG build時のupdate URL

R2 custom domainを確定したら、notarize前のapp buildでmanifest URLを埋める。

```bash
export TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_MANIFEST_URL=https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json

scripts/notarize_mac_dmg.sh
```

manifestとDMGを同じ `downloads.lacramy.com` に置く場合、
`TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS` は不要。

Pages domainをmanifestにしてR2 domainをDMGにする構成も可能だが、その場合は
以下のallowlistが必要になる。通常は使わない。

```bash
export TOTALSEGMENTATOR_WRAPPER_MAC_UPDATE_ALLOWED_HOSTS=downloads.lacramy.com
```

## Cloudflare初回セットアップ

```bash
npx wrangler login
npx wrangler r2 bucket create lacramy-downloads
npx wrangler pages project create totalsegmentator-wrapper-mac
npx wrangler pages project create lacramy-apps
```

Cloudflare dashboardでR2 bucketへcustom domainを接続する。

```text
R2 > bucket > Settings > Custom Domains > Add
```

Cloudflare Pagesもcustom domainを接続する。`totalsegmentator.lacramy.com` は
`totalsegmentator-wrapper-mac` projectへ、`app.lacramy.com` は `lacramy-apps`
projectへ接続する。

```text
Workers & Pages > Pages project > Custom domains
```

## 既存downloads移行

`downloads.lacramy.com` はChoioki paid beta配布で使った履歴がある。
旧bucket名は `choioki-downloads` だが、外向きURLは未固定運用なので、
共通download hostに合わせて新bucket `lacramy-downloads` へ移行する。

維持する既存prefix:

```text
choioki/beta/2026-06-06-paid-beta-2/
redact/openai-privacy-filter-q4.signed.json
```

移行方針:

```text
1. lacramy-downloads を作成
2. choioki-downloads 内の choioki/ と redact/ を lacramy-downloads へコピー
3. downloads.lacramy.com のR2 custom domainを lacramy-downloads へ接続
4. 既存Choioki URLsと新TotalSegmentator URLsをcurlで確認
5. 問題がなければ以後は lacramy-downloads だけを使う
```

bucket名は公開URLに出ない。ユーザーに見えるのは
`downloads.lacramy.com/<app-prefix>/...` だけ。

Current status on 2026-06-22:

```text
created:
  lacramy-downloads
  Pages project totalsegmentator-wrapper-mac
  Pages project lacramy-apps

uploaded to lacramy-downloads:
  redact/openai-privacy-filter-q4.signed.json
  choioki/beta/2026-06-06-paid-beta-2/*
  totalsegmentator-wrapper-mac/releases/0.1.2/*
  totalsegmentator-wrapper-mac/releases/0.1.0/*
  totalsegmentator-wrapper-mac/releases/stable/update.json
  totalsegmentator-wrapper-mac/releases/alpha/update.json

Pages:
  https://totalsegmentator-wrapper-mac.pages.dev/ -> 200
  https://totalsegmentator-wrapper-mac.pages.dev/download -> stable DMG 302
  totalsegmentator.lacramy.com is connected to the totalsegmentator-wrapper-mac Pages project:
    status: active
  https://totalsegmentator.lacramy.com/ -> 200
  https://totalsegmentator.lacramy.com/download -> stable DMG 302
  https://lacramy-apps.pages.dev/ -> 200
  app.lacramy.com is connected to the lacramy-apps Pages project:
    status: active
  https://app.lacramy.com/ -> 200 hub
  https://app.lacramy.com/download -> stable DMG 302
  https://app.lacramy.com/totalsegmentator-wrapper-mac -> canonical app page 302

custom domain:
  downloads.lacramy.com is connected to lacramy-downloads
  ownership_status: active
  ssl_status: active
  min_tls_version: 1.2

verified through Cloudflare edge:
  totalsegmentator-wrapper-mac/releases/stable/update.json -> 200, latest_version=0.1.2
  totalsegmentator-wrapper-mac/releases/alpha/update.json -> 200
  totalsegmentator-wrapper-mac/releases/0.1.2/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg -> 200
  totalsegmentator.lacramy.com/download -> 302 to the 0.1.2 DMG
  app.lacramy.com/download -> 302 to the 0.1.2 DMG
  app.lacramy.com/totalsegmentator-wrapper-mac -> 302 to https://totalsegmentator.lacramy.com/
  choioki/beta/2026-06-06-paid-beta-2/SHA256SUMS.txt -> 200

DNS note:
  1.1.1.1 resolves downloads.lacramy.com to Cloudflare edge IPs.
  1.1.1.1 resolves totalsegmentator.lacramy.com to Cloudflare edge IPs.
  1.1.1.1 resolves app.lacramy.com to Cloudflare edge IPs.
  The local default resolver may lag immediately after domain cutover.
```

既知objectのコピーは以下で行う。これは現在の公開URL
`https://downloads.lacramy.com/...` から取得し、`lacramy-downloads` へ同じkeyで
uploadする。

```bash
scripts/migrate_lacramy_downloads_existing_objects.sh
```

custom domain cutover:

```bash
npx wrangler r2 bucket domain remove choioki-downloads \
  --domain downloads.lacramy.com \
  --force

npx wrangler r2 bucket domain add lacramy-downloads \
  --domain downloads.lacramy.com \
  --zone-id 2fa1a401248fdc6004f5635a6e8f4263 \
  --min-tls 1.2 \
  --force
```

cutover後は、Choioki/Redact既存URLとTotalSegmentator Wrapper URLをすぐ確認する。

## R2 upload

`cloudflare/r2/upload-plan.json` を確認してからuploadする。

```bash
BUCKET=lacramy-downloads

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.2/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg" \
  --file "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.2/SHA256SUMS.txt" \
  --file "cloudflare/r2/releases/0.1.2/SHA256SUMS.txt" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.2/RELEASE_NOTES.txt" \
  --file "cloudflare/r2/releases/0.1.2/RELEASE_NOTES.txt" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.2/release.json" \
  --file "cloudflare/r2/releases/0.1.2/release.json" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/stable/update.json" \
  --file "cloudflare/r2/releases/stable/update.json" \
  --remote
```

必要に応じてCloudflare側でcache ruleを設定する。

```text
downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json
  bypass cache or low TTL

downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/alpha/update.json
  bypass cache or low TTL

downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.2/*
  long TTL
```

## Pages deploy

```bash
npx wrangler pages deploy cloudflare/pages --project-name totalsegmentator-wrapper-mac
npx wrangler pages deploy cloudflare/app-hub --project-name lacramy-apps
```

PagesはDMG本体を持たない。app page と hub の `/download` と `/release-notes` は
R2 custom domainへ302 redirectする。

## 公開前チェック

R2 object:

```bash
curl -fsS https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json | python3 -m json.tool
curl -fsS https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.2/SHA256SUMS.txt
```

Updater互換:

```bash
PYTHONPATH=src python -m totalsegmentator_wrapper_mac update-check \
  --manifest-url https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json \
  --current-version 0.0.0 \
  --json /tmp/totalsegmentator-wrapper-update-check.json
```

DMG checksum:

```bash
curl -L \
  -o /tmp/TotalSegmentator-Wrapper-for-Mac-0.1.2-20260708-modelsetup-arm64.dmg \
  "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.2/TotalSegmentator%20Wrapper%20for%20Mac-0.1.2-20260708-modelsetup-arm64.dmg"

shasum -a 256 /tmp/TotalSegmentator-Wrapper-for-Mac-0.1.2-20260708-modelsetup-arm64.dmg
```

期待SHA256は `cloudflare/r2/releases/0.1.2/SHA256SUMS.txt` と
`cloudflare/r2/releases/stable/update.json` の値に一致させる。

現在の0.1.2 SHA256:

```text
636d0e071dd68a60f13054165c4ef8ab7ef3f51ba535231128759810e5264a3a
```


Pages:

```bash
curl -I https://totalsegmentator-wrapper-mac.pages.dev/
curl -I https://totalsegmentator-wrapper-mac.pages.dev/download
curl -I https://totalsegmentator.lacramy.com/
curl -I https://totalsegmentator.lacramy.com/download
curl -I https://app.lacramy.com/
curl -I https://app.lacramy.com/download
curl -I https://app.lacramy.com/totalsegmentator-wrapper-mac
```

`totalsegmentator.lacramy.com` は app-specific canonical page として
`totalsegmentator-wrapper-mac` Pages projectへ接続する。`app.lacramy.com` は
`lacramy-apps` Pages projectへ接続し、複数アプリのhubとして使う。

## 運用上の注意

- `downloads.lacramy.com` は共通download hostなので、アプリごとにprefixを切る。
- `app.lacramy.com` は特定アプリ専用にしない。外部共有は各アプリのcanonical pageを使う。
- 公開appに埋め込む `update_manifest_url` は
  `totalsegmentator-wrapper-mac/releases/stable/update.json` にする。
- 検証appだけ `totalsegmentator-wrapper-mac/releases/alpha/update.json` を使う。
- `totalsegmentator-wrapper-mac/releases/alpha/update.json` を更新すると、
  既存alpha appの更新ボタンに影響する。
- versioned DMG objectは上書きしない。作り直す場合はversionかbuild idを上げる。
- DMGを差し替えた場合は、SHA256、`release.json`、`SHA256SUMS.txt`、`update.json`、
  `docs/34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md` を同じSHAへ揃える。
