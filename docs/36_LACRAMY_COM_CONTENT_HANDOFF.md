# 36 Lacramy.com Content Handoff

> **Historical handoff (0.1.2 distribution, 2026-07-08).** This record is
> retained for audit context and must not be used for current release, upload,
> redirect, or update-manifest instructions. The current policy is in
> [`docs/35_CLOUDFLARE_DISTRIBUTION.md`](35_CLOUDFLARE_DISTRIBUTION.md): legacy
> `stable` remains the exact live 0.3.0 record, while 0.4.1+ can be promoted
> only through the verified `stable-v2` route after all release gates pass.

lacramy.com のコンテンツ管理スレッドへ引き継ぐためのメモ。
このリポジトリ側では、TotalSegmentator Wrapper for Mac の配布ページを
`app.lacramy.com` から app-specific domain へ移し、`app.lacramy.com` を
複数アプリ用hubとして使う方針に変更した。

## 引き継ぎ目的

- `app.lacramy.com` を特定アプリ専用にしない。
- TotalSegmentator Wrapper for Mac の正式公開URLを
  `https://totalsegmentator.lacramy.com/` にする。
- `https://app.lacramy.com/` は Lacramy Apps hub として運用する。
- 既存互換として `https://app.lacramy.com/download` は当面 0.1.2 DMG へ
  redirectし続ける。
- アプリ内更新は `downloads.lacramy.com` の stable manifest を使うため、
  lacramy.comコンテンツ側では変更しない。

## Historical Cloudflare Snapshot

Cloudflare Pages:

```text
totalsegmentator-wrapper-mac
  default domain: totalsegmentator-wrapper-mac.pages.dev
  custom domain:  totalsegmentator.lacramy.com
  deploy source:  segmentation_w_mps/cloudflare/pages/
  purpose:        TotalSegmentator Wrapper for Mac app page

lacramy-apps
  default domain: lacramy-apps.pages.dev
  custom domain:  app.lacramy.com
  deploy source:  segmentation_w_mps/cloudflare/app-hub/
  purpose:        Lacramy Apps hub
```

Cloudflare DNS:

```text
app.lacramy.com
  CNAME lacramy-apps.pages.dev
  Proxied

totalsegmentator.lacramy.com
  CNAME totalsegmentator-wrapper-mac.pages.dev
  Proxied
```

R2は変更していない。

```text
downloads.lacramy.com
  bucket: lacramy-downloads
  prefix: totalsegmentator-wrapper-mac/
```

## Historical Public URL Contract

0.1.2配布metadata生成後の公開導線の期待状態:

```text
https://totalsegmentator.lacramy.com/
  TotalSegmentator Wrapper for Mac 0.1.2 public alpha page

https://totalsegmentator.lacramy.com/download
  302 -> https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.2/TotalSegmentator%20Wrapper%20for%20Mac-0.1.2-20260708-modelsetup-arm64.dmg

https://totalsegmentator.lacramy.com/release-notes
  302 -> https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/0.1.2/RELEASE_NOTES.txt

https://app.lacramy.com/
  Lacramy Apps hub

https://app.lacramy.com/download
  compatibility route
  302 -> same 0.1.2 DMG

https://app.lacramy.com/totalsegmentator-wrapper-mac
  302 -> https://totalsegmentator.lacramy.com/
```

アプリ内更新manifest:

```text
https://downloads.lacramy.com/totalsegmentator-wrapper-mac/releases/stable/update.json
  latest_version: 0.1.2
  sha256: 636d0e071dd68a60f13054165c4ef8ab7ef3f51ba535231128759810e5264a3a
```

## Repo側の変更

主な変更:

- `cloudflare/pages/index.html`
  - canonical URLを `https://totalsegmentator.lacramy.com/` に変更。
  - OG metadataを追加。
  - 配布情報を `0.1.2` DMGの実SHA/サイズに更新。
- `cloudflare/app-hub/`
  - `app.lacramy.com` 用のLacramy Apps hubを追加。
  - `/download`, `/release-notes`, `/totalsegmentator-wrapper-mac` のredirectを定義し、`/download` を `0.1.2` DMGへ向ける。
- `cloudflare/r2/releases/0.1.2/`
  - `release.json`, `SHA256SUMS.txt`, `RELEASE_NOTES.txt` を実DMGから生成。
- `cloudflare/r2/releases/stable/update.json`
  - `latest_version=0.1.2`, `minimum_supported_version=0.1.1` に更新。
- `docs/34_ALPHA_DISTRIBUTION_SUPPORT_CARD.md`, `docs/35_CLOUDFLARE_DISTRIBUTION.md`
  - 0.1.2の実SHA、サイズ、release evidenceを記録。
- `tests/test_cloudflare_distribution.py`
  - app page、hub、stable manifest、release metadata、SHA256SUMS、ローカルDMG実SHAの整合を検証。

## Verification

0.1.2 local release gate:

ローカルテスト:

```text
git diff --check
  OK

env PYTHONPATH=src python3 -m unittest tests.test_cloudflare_distribution tests.test_non_clinical_language tests.test_update_check tests.test_mac_app_packaging tests.test_license_inventory
  41 tests OK

env PYTHONPATH=src dist/license_inventory_env/bin/python -m unittest discover -s tests
  130 tests OK

scripts/verify_zero_env_mac_dmg.sh "dist/TotalSegmentator Wrapper for Mac-0.1.2-20260708-modelsetup-arm64.dmg"
  Zero-env DMG install verification passed
```

notarization / Gatekeeper:

```text
notarytool submit: Accepted, id=a325757c-234e-4324-a542-6f9450469b83
xcrun stapler validate: The validate action worked
spctl --assess --type open: accepted, source=Notarized Developer ID
mounted app spctl --assess --type execute: accepted, source=Notarized Developer ID
DMG SHA256: 636d0e071dd68a60f13054165c4ef8ab7ef3f51ba535231128759810e5264a3a
```

公開deploy後に再確認する項目:

Cloudflare Pages domain status:

```text
totalsegmentator-wrapper-mac
  totalsegmentator-wrapper-mac.pages.dev
  totalsegmentator.lacramy.com

lacramy-apps
  lacramy-apps.pages.dev
  app.lacramy.com
```

Cloudflare API上のcustom domain statusはどちらも `active`。

Public DNS:

```text
dig +short totalsegmentator.lacramy.com @1.1.1.1
dig +short totalsegmentator.lacramy.com @8.8.8.8
dig +short app.lacramy.com @1.1.1.1
dig +short app.lacramy.com @8.8.8.8
```

いずれもCloudflare edge IPへ解決済み。

HTTP verification:

```text
https://app.lacramy.com/ -> 200
https://app.lacramy.com/download -> 302 to 0.1.2 DMG
https://app.lacramy.com/totalsegmentator-wrapper-mac -> 302 to https://totalsegmentator.lacramy.com/
```

`totalsegmentator.lacramy.com` はCloudflare edge直指定で確認済み。

```text
curl --resolve totalsegmentator.lacramy.com:443:104.21.92.90 -I https://totalsegmentator.lacramy.com/
  200

curl --resolve totalsegmentator.lacramy.com:443:104.21.92.90 -I https://totalsegmentator.lacramy.com/download
  302 to 0.1.2 DMG

curl --resolve totalsegmentator.lacramy.com:443:104.21.92.90 -I https://totalsegmentator.lacramy.com/release-notes
  302 to RELEASE_NOTES.txt
```

注意:

- この実行環境のdefault resolverだけ、直後は
  `totalsegmentator.lacramy.com` を解決できず `curl` が `Could not resolve host`
  になった。
- 1.1.1.1 / 8.8.8.8 / Cloudflare edge直指定では正常なので、公開DNSではなく
  ローカルresolver/negative cacheの問題として扱う。

## lacramy.com側でやること

1. lacramy.comコンテンツ管理側で、`app.lacramy.com` を単一アプリLPとして扱う
   前提が残っていないか確認する。
2. 外部共有・告知文・ナビゲーションでは
   `https://totalsegmentator.lacramy.com/` を正式URLとして使う。
3. `app.lacramy.com` を「Lacramy Apps」の入口として育てる。
4. 将来別アプリを増やす場合は、`downloads.lacramy.com/<app-prefix>/...` と
   app-specific canonical domainを追加し、hubにカードを足す。
5. `app.lacramy.com/download` は互換routeなので、すぐ消さない。

## 別スレッドへの依頼文

```text
lacramy.com のコンテンツ管理側で、app.lacramy.com を Lacramy Apps hub として扱う前提に更新してください。

現在、TotalSegmentator Wrapper for Mac の正式公開URLは https://totalsegmentator.lacramy.com/ です。
app.lacramy.com は複数アプリの入口で、/download は互換用として TotalSegmentator Wrapper 0.1.2 DMG にredirectしています。

Cloudflare Pagesは以下です。
- totalsegmentator-wrapper-mac -> totalsegmentator.lacramy.com
- lacramy-apps -> app.lacramy.com

R2/update manifestは downloads.lacramy.com/totalsegmentator-wrapper-mac/... のまま変更しません。
このrepo側の詳細は docs/36_LACRAMY_COM_CONTENT_HANDOFF.md と docs/35_CLOUDFLARE_DISTRIBUTION.md を見てください。
```
