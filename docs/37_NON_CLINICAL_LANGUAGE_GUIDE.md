# 37 Non-Clinical Language Guide

This repo keeps medical-scope warnings deliberately repetitive. Do not rewrite
them ad hoc for each page or UI surface.

## Source of truth

Canonical wording lives in:

```text
src/totalsegmentator_wrapper_mac/disclaimers.py
```

Use these constants by intent:

```text
NON_CLINICAL_NOTICE_EN
NON_CLINICAL_SCOPE_JA
NON_CLINICAL_NOTICE_JA
SAMPLE_NOTICE_JA
UNOFFICIAL_WRAPPER_NOTICE_JA
```

## Current Japanese standard copy

Short public scope notice:

```text
研究・教育目的の非臨床プレビューです。医療機器ではなく、診断、治療方針の決定、治療計画、またはその他の医療上の判断には使用できません。
```

Full public notice:

```text
研究・教育目的の非臨床プレビューです。医療機器ではなく、診断、治療方針の決定、治療計画、またはその他の医療上の判断には使用できません。出力は参考表示として扱い、元画像や処理ログと照合してください。
```

Bundled Sample notice:

```text
同梱Sample 1はUI確認・動作確認用です。診断、治療方針の決定、治療計画、定量的な精度評価、または臨床利用には使用できません。
```

Unofficial wrapper notice:

```text
TotalSegmentatorを利用した非公式wrapperです。TotalSegmentator公式アプリではありません。
```

## Guardrail

Run this before release copy changes:

```text
env PYTHONPATH=src .venv/bin/python -m unittest tests.test_non_clinical_language
```

The test checks the public Pages site, app hub, Swift UI, support card, and
Python report generation. If a new public surface is added, add it to that test.

## Do not reintroduce

Avoid one-off variants such as:

```text
診断、治療計画、精度評価には使わないでください
診断・治療計画・精度評価には使いません
診断、治療計画、精度評価、患者説明の根拠には使わないでください
```
