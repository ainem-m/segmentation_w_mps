# 35 Cloudflare Distribution

TotalSegmentator Wrapper for Mac のnotarized DMGをCloudflareで配布し、
将来の0.4.1以降の公開アプリの更新確認を`stable-v2` channelへ向けるための手順。

> 現在の公開境界: `releases/stable/update.json` はlive payload
> `latest_version=0.3.0` のまま、永久にread-onlyである。0.4.0はwithdrawnであり、
> localのrelease recordを再upload・download redirect・update targetに使わない。
> 0.4.1は検証済みDMGを手動で導入するまでの候補で、`stable-v2/update.json` は公開前のため
> 現在HTTP 404である。stable-v2をuploadするのは0.4.1+の全release gate通過後だけにする。

## 方針

Cloudflare Pages は公開ページだけに使い、DMG本体とupdate manifestは
Cloudflare R2に置く。

理由:

- notarized DMGはCloudflare Pagesの単一asset上限を超える可能性がある。
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
totalsegmentator-wrapper-mac/releases/<version>/TotalSegmentator Wrapper for Mac-<version>-<release-tag>-arm64.dmg
totalsegmentator-wrapper-mac/releases/<version>/SHA256SUMS.txt
totalsegmentator-wrapper-mac/releases/<version>/RELEASE_NOTES.txt
totalsegmentator-wrapper-mac/releases/<version>/release.json
totalsegmentator-wrapper-mac/releases/stable-v2/update.json  # 0.4.1+公開時にのみ作成
totalsegmentator-wrapper-mac/releases/stable/update.json  # live 0.3.0、永久read-only
totalsegmentator-wrapper-mac/releases/0.4.0/release.json  # withdrawn record、再公開しない
totalsegmentator-wrapper-mac/releases/0.1.1/...  # rollback/history
totalsegmentator-wrapper-mac/releases/0.1.0/...  # rollback/history
totalsegmentator-wrapper-mac/releases/alpha/update.json
```

`totalsegmentator-wrapper-mac/releases/<version>/...` はversioned objectなので長期cache可。
DMGを作り直す場合は同じobject keyを上書きせず、日付やbuild id入りの一意な
filenameにする。immutable cacheが残るため、同じURLの上書きはmanifest SHAと
edge cacheの不整合を起こす。
`totalsegmentator-wrapper-mac/releases/stable-v2/update.json` は0.4.1以降の公開後にだけ使う更新確認入口で、
公開前はHTTP 404のままにする。
`totalsegmentator-wrapper-mac/releases/alpha/update.json` は検証用入口なので、
どちらもcacheを短くするかbypassする。

## ローカル生成

release metadataはcleanなsource checkoutと、そのHEADから作成したnotarized
DMGに対してだけ生成する。versionは`pyproject.toml`から読み、異なる
`--version`、DMGファイル名、app内部version、wheel version、normalizer
version、`source_commit`は拒否される。
stable-v2の`release.json`と`update.json`は、DMG内manifestと検証済みの
`source_commit`を同じ値で記録し、`source_tree_dirty=false`を必須とする。
過去のimmutable release metadataにこのfieldを後付けしない。
0.4.1以降のDMGはApple Silicon / macOS 14以降を対象とし、`Info.plist`と
`setup_manifest.json`の双方に`14.0`を記録する。app/DMG/notarized releaseの
検証は、この二つの値が一致しないartifactを拒否する。
stable-v2ではdownload origin、bucket、object prefixもcanonical値に固定される。
`stable`（legacy・凍結） / `stable-v2` / `candidate` / `alpha`以外のchannelは拒否し、
非production検証ではstable-v2のimmutable version pathを占有しないよう、versionと異なる明示的な
`--release-id`が必須である。`--published-at`を指定する場合はUTC RFC 3339
（例: `2026-08-01T00:00:00Z`）だけを受け付ける。

### 口腔内スキャン由来WebPの公開境界

`/assets/totalsegmentator-ios-tooth-segmentation.webp`は、
[`docs/43_OPEN_SOURCE_PUBLICATION_DECISIONS.md`](43_OPEN_SOURCE_PUBLICATION_DECISIONS.md)
の非秘密decision ID
`owner-explicit-public-display-consent-2026-08-03`により、**その名前・SHA-256の
派生WebPだけ**がpublic display用に承認されている。stable-v2のlocal gateは、
`approved`状態、decision ID、UTC記録時刻、本人自己スキャンのattestation、decision record
参照、ledgerと実ファイルの固定SHA-256をすべて照合する。

この承認は`ios_upper.ply`を含むraw PLY、またはTGNetのcheckpoint/weightの配布を許可しない。
これらがPagesまたはapp-hubのdeployable treeにあれば、stable-v2のmetadata生成と
public Pages stage生成を停止する。承認済みWebPでも、TGNet checkpointの利用条件や
重みの再配布許可を意味しない。

```bash
APP_VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
DMG_PATH="dist/TotalSegmentator Wrapper for Mac-${APP_VERSION}-release-arm64.dmg"
SOURCE_COMMIT="$(git rev-parse HEAD)"
PROMOTED_PAGES_ROOT="/tmp/totalsegmentator-wrapper-mac-${APP_VERSION}-${SOURCE_COMMIT}-pages"

scripts/prepare_cloudflare_release.py \
  --channel stable-v2 \
  --minimum-supported-version 0.4.1 \
  --dmg "${DMG_PATH}" \
  --download-origin https://downloads.lacramy.com \
  --bucket lacramy-downloads \
  --object-prefix totalsegmentator-wrapper-mac \
  --promoted-pages-output "${PROMOTED_PAGES_ROOT}" \
  --notarized
```

`PROMOTED_PAGES_ROOT`はrepo外の、まだ存在しない一意なpathにする。生成器は
`release.json`、`update.json`、DMGのfilename・size・SHA-256・source commit・
notarization状態・canonical URLが一致しなければ公開用stageを作らない。
追跡中の`cloudflare/pages/`と`cloudflare/app-hub/`はlive 0.3.0を表す
pre-release templateとして変更しない。
`PROMOTION_RECEIPT.json`の検証範囲はlocalのrelease/update/DMG identityだけで、
upload後のlive R2 objectの検証を代替しない。Cloudflare projectのdeployment topologyも
代替しない。

## Cloudflare deployment-topology gate (external)

`prepare_cloudflare_release.py`、`PROMOTION_RECEIPT.json`、およびpublic asset
provenance gateが示せるのは **LOCAL PASS**（checkout内のDMG、metadata、生成stageの
整合）だけである。これらはCloudflare account内のGit integration、production branch、
auto-deploy、またはdashboard上のsource deployment設定を **does not prove**。

このrepoから確認できるCloudflare project topologyの現在値は
**EXTERNAL-STATE UNVERIFIED** である。stable-v2 promotionの前に、release operatorは
Cloudflare dashboardまたは権限を持つAPIの確認結果をrelease evidenceへ記録する。確認が
できない場合はintegrationをpauseし、そのpause状態を記録する。local testのPASSや
`PROMOTION_RECEIPT.json`だけでこの状態をPASSへ変更してはならない。

recordにはrelease version/release ID、UTC確認時刻、確認者、secretを含まない設定画面または
API応答の保存先を含め、以下を **両方** のPages projectについて明記する。

```text
totalsegmentator-wrapper-mac
lacramy-apps

Git auto-deploy: none/disabled または integration-paused
direct source deployment: none/disabled または integration-paused
authorized manual deploy source: ${PROMOTED_PAGES_ROOT}/pages または ${PROMOTED_PAGES_ROOT}/app-hub
```

ここでいう`direct source deployment`は、追跡中の`cloudflare/pages/`または
`cloudflare/app-hub/`をCloudflare側のsource integrationから直接productionへ送る設定を指す。
生成済みstageを明示的に送る`wrangler pages deploy`は、下記のmanual promotionだけに限る。
Git integrationが残る場合は、両projectのGit auto-deploy/direct source deploymentを
`integration-paused`にしてから記録する。

**Fail closed:** このexternal attestationがrelease evidenceにない、projectのどちらかが
`none/disabled`でも`integration-paused`でもない、または確認内容が不明な場合は
**Do not upload the immutable R2 objects, stable-v2/update.json, or Pages stage**。
これはstable-v2 promotionを停止する条件であり、local asset provenanceがPASSでも例外にしない。

生成物:

```text
cloudflare/r2/releases/stable-v2/update.json
cloudflare/r2/releases/<version>/SHA256SUMS.txt
cloudflare/r2/releases/<version>/RELEASE_NOTES.txt
cloudflare/r2/releases/<version>/release.json
cloudflare/r2/upload-plan.json
${PROMOTED_PAGES_ROOT}/pages/
${PROMOTED_PAGES_ROOT}/app-hub/
${PROMOTED_PAGES_ROOT}/PROMOTION_RECEIPT.json
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

notarize scriptはcanonical stable-v2 URLとhost allowlistを自動設定する。
bundle identifierは`jp.chino.totalsegmentator.wrapper.mac`に固定される。
Developer ID Team IDは明示する。

```bash
export TOTALSEGMENTATOR_WRAPPER_MAC_TEAM_IDENTIFIER=<10-character-team-id>
scripts/notarize_mac_dmg.sh
```

Developer ID buildでは以下以外を指定するとbuildを停止する。

```text
update_manifest_url = https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json
update_allowed_hosts = ["downloads.lacramy.com"]
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

Historical status on 2026-06-22（現行release手順ではない）:

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

`cloudflare/r2/upload-plan.json`を確認し、記載されたsource、key、content type、
cache policyだけを使用する。公開停止中のplanは`objects=[]`であり、uploadしてはならない。
手入力した旧versionのkeyは使わない。新しいverified stable-v2 releaseを生成した時だけ、
その生成直後のplanを使用する。

公開順序は固定する。

```text
0. Cloudflare deployment-topology gateのexternal attestationを記録する。
   LOCAL PASS / EXTERNAL-STATE UNVERIFIEDを混同しない。
1. upload-planのimmutable=true相当の4 object
   - DMG
   - SHA256SUMS.txt
   - RELEASE_NOTES.txt
   - release.json
2. 4 URLすべてがHTTP 200であることを確認
3. 公開DMGのContent-LengthとSHA-256がrelease.json、SHA256SUMS.txtに一致することを確認
4. 上記がすべてpassした後だけreleases/stable-v2/update.jsonをupload
5. stable-v2 URLを再取得し、version、file_size_bytes、SHA-256、download URLを再確認
6. `PROMOTION_RECEIPT.json`のversion、DMG size、SHA-256、download URLが
   再取得したstable-v2 metadataと一致することを確認
7. 公開用stageに`公開前`、macOS 13、0.3.0向けdownload/release-notes redirectが
   0件で、macOS 14以降の表記になっていることを確認
8. promotion materializerのpublic asset provenance gateが、HTML/CSS/JS/web manifest等から
   参照されるlocal assetと`cloudflare/pages/assets/ASSET_PROVENANCE.json`／preview台帳を
   reverse照合し、台帳にないassetが0件で、各assetの出所・利用条件・SHA-256を確認する。
   これはLOCAL PASSであり、Git auto-deploy/direct source deploymentを防ぐ証拠にはならない。
9. external attestationが両Pages projectについてなお有効であることを再確認する。
10. 最後に公開用stageのPagesをdeploy
```

versioned release directoryはimmutableである。同一versionの異なるbytes、
同一versionの異なるSHA-256、stable-v2のdowngradeは
`prepare_cloudflare_release.py`がローカルmetadata生成時に拒否する。R2上でも
既存versioned objectを上書きしない。

必要に応じてCloudflare側でcache ruleを設定する。

```text
downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json
  bypass cache or low TTL

downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/alpha/update.json
  bypass cache or low TTL

downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/<version>/*
  long TTL
```

## Pages deploy

```bash
npx wrangler pages deploy "${PROMOTED_PAGES_ROOT}/pages" --project-name totalsegmentator-wrapper-mac
npx wrangler pages deploy "${PROMOTED_PAGES_ROOT}/app-hub" --project-name lacramy-apps
```

PagesはDMG本体を持たない。app page と hub の `/download` と `/release-notes` は
R2 custom domainへ302 redirectする。
stable-v2公開時に追跡中の`cloudflare/pages/`または`cloudflare/app-hub/`を
直接deployしてはいけない。これらは0.3.0公開状態を保持するtemplateであり、
検証済みmetadataから生成した`PROMOTED_PAGES_ROOT`だけをdeploy対象にする。
このmanual `wrangler pages deploy`の直前にも、上記external deployment-topology
attestationが存在することを確認する。Cloudflare側にGit auto-deploy/direct source
deploymentが残る場合は、manual deployを実行しない。

## 公開前チェック

R2 object:

```bash
APP_VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
curl -fsS https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json | python3 -m json.tool
curl -fsS "https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/${APP_VERSION}/SHA256SUMS.txt"
```

Updater互換:

```bash
PYTHONPATH=src python -m totalsegmentator_wrapper_mac update-check \
  --manifest-url https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable-v2/update.json \
  --current-version 0.0.0 \
  --json /tmp/totalsegmentator-wrapper-update-check.json
```

DMG checksum:

```bash
curl -L \
  -o "/tmp/TotalSegmentator-Wrapper-for-Mac-${APP_VERSION}.dmg" \
  "$(python3 -c 'import json; print(json.load(open("cloudflare/r2/releases/stable-v2/update.json"))["download_url"])')"

shasum -a 256 "/tmp/TotalSegmentator-Wrapper-for-Mac-${APP_VERSION}.dmg"
```

期待SHA256は `cloudflare/r2/releases/<version>/SHA256SUMS.txt` と
`cloudflare/r2/releases/stable-v2/update.json` の値に一致させる。


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
- 0.4.1以降の公開appに埋め込む `update_manifest_url` は
  `totalsegmentator-wrapper-mac/releases/stable-v2/update.json` にする。
- `totalsegmentator-wrapper-mac/releases/stable/update.json` はlive 0.3.0のlegacy endpointであり、
  永久read-onlyとする。release tool・upload plan・手作業のいずれでも更新しない。
- localの0.4.0 recordはwithdrawnである。これをstableへ戻す、redirectする、upload-planへ入れる、
  または新しいreleaseの根拠として使うことを禁止する。
- 検証appだけ `totalsegmentator-wrapper-mac/releases/alpha/update.json` を使う。
- `totalsegmentator-wrapper-mac/releases/alpha/update.json` を更新すると、
  既存alpha appの更新ボタンに影響する。
- versioned DMG objectは上書きしない。stable-v2候補を作り直す場合はapp versionを上げる。
  検証channelは一意な`release-id`を使う。
- stable-v2はimmutable objectの公開URL、size、SHA-256を確認してから最後に更新する。
  同一versionのDMG差し替えで辻褄を合わせない。
