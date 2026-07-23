from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from totalsegmentator_wrapper_mac.benchmark import write_json
from totalsegmentator_wrapper_mac.disclaimers import NON_CLINICAL_NOTICE_EN
from totalsegmentator_wrapper_mac.mask_stats import collect_mask_stats
from totalsegmentator_wrapper_mac.outputs import CaseOutput


def generate_output_report(
    *,
    case: CaseOutput,
    source_volume_path: Path,
    task: str,
    run_result: Any,
) -> None:
    source_volume_path = source_volume_path.resolve()
    segmentations_dir = case.root / "segmentations"
    mask_stats = collect_mask_stats(segmentations_dir, recursive=True)
    write_json(case.mask_stats_path, mask_stats)
    case.readme_path.write_text(
        _readme(case, source_volume_path, task, _to_dict(run_result), mask_stats),
        encoding="utf-8",
    )


def _to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {}


def _readme(
    case: CaseOutput,
    source_volume_path: Path,
    task: str,
    run_result: dict[str, Any],
    mask_stats: dict[str, Any],
) -> str:
    status = run_result.get("status", "unknown")
    elapsed = run_result.get("elapsed_seconds")
    requested_device = run_result.get("requested_device", "unknown")
    actual_device = run_result.get("actual_device", "unknown")
    elapsed_text = f"{elapsed:.2f} seconds" if isinstance(elapsed, (int, float)) else "unknown"
    mask_lines = _mask_lines(mask_stats)
    source_note = _source_note(case, source_volume_path)
    diagnostics_dir = case.logs_dir.relative_to(case.root).as_posix()
    title = "ToothSeg Refinement Output" if case.report_filename == "TOOTHSEG_OUTPUT.md" else "TotalSegmentator Wrapper for Mac Output"
    return f"""# {title}

## Run

```text
task: {task}
status: {status}
requested_device: {requested_device}
actual_device: {actual_device}
elapsed: {elapsed_text}
```

## Files

```text
source volume: {source_note}
segmentations: segmentations/
benchmark log: {diagnostics_dir}/benchmark.json
environment log: {diagnostics_dir}/environment.json
run log: {diagnostics_dir}/run.log
mask stats: {diagnostics_dir}/mask_stats.json
```

## Segmentation Masks

{mask_lines}

## 3D Preview

通常は、アプリがプレビュー作成の後半でブラウザ用の3Dプレビューを用意します。
結果画面で `3Dプレビューを開く（ブラウザ）` を押して確認してください。

3Dプレビューが見当たらない場合は、アプリの結果画面で
`3Dプレビューを再生成` を押してください。それでも失敗する場合は、
結果フォルダと `logs/run.log` を確認してください。

## Notice

{NON_CLINICAL_NOTICE_EN}
"""


def _source_note(case: CaseOutput, source_volume_path: Path) -> str:
    try:
        relative = source_volume_path.resolve().relative_to(case.root.resolve())
    except ValueError:
        return f"original input path referenced by output summary: `{source_volume_path.name}`"
    return f"`{relative}`"


def _mask_lines(mask_stats: dict[str, Any]) -> str:
    masks = mask_stats.get("masks") or []
    if not masks:
        return "- none"
    lines = []
    for item in masks:
        name = item.get("name", "unknown")
        status = item.get("status", "unknown")
        if status == "ok":
            lines.append(f"- `{name}`: {item.get('nonzero_voxels')} nonzero voxels")
        else:
            lines.append(f"- `{name}`: unreadable as NIfTI mask ({item.get('error')})")
    return "\n".join(lines)
