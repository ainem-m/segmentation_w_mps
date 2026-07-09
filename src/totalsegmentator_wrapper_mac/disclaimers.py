from __future__ import annotations


NON_CLINICAL_NOTICE_EN = (
    "This is a non-clinical research/education preview. It is not a medical device "
    "and is not intended for diagnosis, treatment planning, surgical planning, or "
    "autonomous clinical decision-making. Outputs must be treated as preliminary "
    "segmentation model outputs and manually reviewed."
)

NON_CLINICAL_SCOPE_JA = (
    "研究・教育目的の非臨床プレビューです。医療機器ではなく、診断、治療方針の決定、"
    "治療計画、またはその他の医療上の判断には使用できません。"
)

NON_CLINICAL_NOTICE_JA = (
    f"{NON_CLINICAL_SCOPE_JA}研究・教育目的で結果を評価する場合も、"
    "原画像との照合と専門家による目視確認が必要です。"
)

SAMPLE_NOTICE_JA = (
    "同梱Sample 1はUI確認・動作確認用です。診断、治療方針の決定、治療計画、"
    "定量的な精度評価、または臨床利用には使用できません。"
)

UNOFFICIAL_WRAPPER_NOTICE_JA = (
    "TotalSegmentatorを利用した非公式wrapperです。TotalSegmentator公式アプリではありません。"
)
