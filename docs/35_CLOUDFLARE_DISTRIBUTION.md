# 35 Cloudflare Distribution

TotalSegmentator Wrapper for Mac の public alpha DMG を Cloudflare で配布し、
公開アプリの更新確認を stable channel へ向けるための準備メモ。

## 方針

Cloudflare Pages は公開ページだけに使い、DMG本体とupdate manifestは
Cloudflare R2に置く。

理由:

- 現在の `dist/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg` は
  `46 MiB`。
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
totalsegmentator-wrapper-mac/releases/0.1.0/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg
totalsegmentator-wrapper-mac/releases/0.1.0/SHA256SUMS.txt
totalsegmentator-wrapper-mac/releases/0.1.0/RELEASE_NOTES.txt
totalsegmentator-wrapper-mac/releases/0.1.0/release.json
totalsegmentator-wrapper-mac/releases/stable/update.json
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
  --version 0.1.0 \
  --channel stable \
  --dmg "dist/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg" \
  --download-origin https://downloads.lacramy.com \
  --bucket lacramy-downloads \
  --object-prefix totalsegmentator-wrapper-mac \
  --published-at 2026-06-18T00:00:00Z
```

生成物:

```text
cloudflare/r2/releases/stable/update.json
cloudflare/r2/releases/0.1.0/SHA256SUMS.txt
cloudflare/r2/releases/0.1.0/RELEASE_NOTES.txt
cloudflare/r2/releases/0.1.0/release.json
cloudflare/r2/upload-plan.json
```

公開ページは以下。

```text
cloudflare/pages/index.html
cloudflare/pages/_headers
cloudflare/pages/_redirects
cloudflare/pages/assets/sample1-preview.png
```

`cloudflare/pages/_redirects` は `downloads.lacramy.com/totalsegmentator-wrapper-mac/...`
へredirectする。

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
npx wrangler pages project create
```

Cloudflare dashboardでR2 bucketへcustom domainを接続する。

```text
R2 > bucket > Settings > Custom Domains > Add
```

Cloudflare Pagesもcustom domainを接続する。

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

uploaded to lacramy-downloads:
  redact/openai-privacy-filter-q4.signed.json
  choioki/beta/2026-06-06-paid-beta-2/*
  totalsegmentator-wrapper-mac/releases/0.1.0/*
  totalsegmentator-wrapper-mac/releases/stable/update.json
  totalsegmentator-wrapper-mac/releases/alpha/update.json

Pages:
  https://totalsegmentator-wrapper-mac.pages.dev/ -> 200
  https://totalsegmentator-wrapper-mac.pages.dev/download -> stable DMG 302
  app.lacramy.com is added to the Pages project but remains pending:
    verification_error: CNAME record not set

custom domain:
  downloads.lacramy.com is connected to lacramy-downloads
  ownership_status: active
  ssl_status: active
  min_tls_version: 1.2

verified through Cloudflare edge:
  totalsegmentator-wrapper-mac/releases/stable/update.json -> 200
  totalsegmentator-wrapper-mac/releases/alpha/update.json -> 200
  totalsegmentator-wrapper-mac/releases/0.1.0/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg -> 200
  choioki/beta/2026-06-06-paid-beta-2/SHA256SUMS.txt -> 200

DNS note:
  1.1.1.1 resolves downloads.lacramy.com to Cloudflare edge IPs.
  The local default resolver may lag immediately after cutover.
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
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.0/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg" \
  --file "dist/TotalSegmentator Wrapper for Mac-0.1.0-20260622stable2-arm64.dmg" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.0/SHA256SUMS.txt" \
  --file "cloudflare/r2/releases/0.1.0/SHA256SUMS.txt" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.0/RELEASE_NOTES.txt" \
  --file "cloudflare/r2/releases/0.1.0/RELEASE_NOTES.txt" \
  --remote

npx wrangler r2 object put \
  "${BUCKET}/totalsegmentator-wrapper-mac/releases/0.1.0/release.json" \
  --file "cloudflare/r2/releases/0.1.0/release.json" \
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

downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.0/*
  long TTL
```

## Pages deploy

```bash
npx wrangler pages deploy cloudflare/pages --project-name totalsegmentator-wrapper-mac
```

PagesはDMG本体を持たない。`/download` と `/release-notes` はR2 custom domainへ
302 redirectする。

## 公開前チェック

R2 object:

```bash
curl -fsS https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json | python3 -m json.tool
curl -fsS https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.0/SHA256SUMS.txt
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
  -o /tmp/TotalSegmentator-Wrapper-for-Mac-0.1.0-20260622stable2-arm64.dmg \
  "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.0/TotalSegmentator%20Wrapper%20for%20Mac-0.1.0-20260622stable2-arm64.dmg"

shasum -a 256 /tmp/TotalSegmentator-Wrapper-for-Mac-0.1.0-20260622stable2-arm64.dmg
```

期待SHA256:

```text
6f5cf39dabd96f17035b9ffb9b3dffb23248b91d60b3c524858e4327883eada1
```

Pages:

```bash
curl -I https://totalsegmentator-wrapper-mac.pages.dev/
curl -I https://totalsegmentator-wrapper-mac.pages.dev/download
```

`app.lacramy.com` を使う場合は、Pages custom domainを追加したうえで
`app.lacramy.com` のCNAMEを `totalsegmentator-wrapper-mac.pages.dev` へ向ける。
2026-06-22時点ではPages側のdomain追加は済んでいるが、DNS API権限がなく
CNAME recordは未作成。

## 運用上の注意

- `downloads.lacramy.com` は共通download hostなので、アプリごとにprefixを切る。
- 公開appに埋め込む `update_manifest_url` は
  `totalsegmentator-wrapper-mac/releases/stable/update.json` にする。
- 検証appだけ `totalsegmentator-wrapper-mac/releases/alpha/update.json` を使う。
- `totalsegmentator-wrapper-mac/releases/alpha/update.json` を更新すると、
  既存alpha appの更新ボタンに影響する。
- versioned DMG objectは上書きしない。作り直す場合はversionかbuild idを上げる。
- DMGを差し替えた場合は、SHA256、`release.json`、`SHA256SUMS.txt`、`update.json`、
  `docs/34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md` を同じSHAへ揃える。
