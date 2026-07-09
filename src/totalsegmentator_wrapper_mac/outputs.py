from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaseOutput:
    root: Path
    input_dir: Path
    raw_segmentations_dir: Path
    teeth_experimental_dir: Path
    dentalsegmentator_dir: Path
    logs_dir: Path

    @property
    def source_path(self) -> Path:
        return self.input_dir / "source.nii.gz"

    @property
    def run_log_path(self) -> Path:
        return self.logs_dir / "run.log"

    @property
    def benchmark_path(self) -> Path:
        return self.logs_dir / "benchmark.json"

    @property
    def environment_path(self) -> Path:
        return self.logs_dir / "environment.json"

    @property
    def mask_stats_path(self) -> Path:
        return self.logs_dir / "mask_stats.json"

    @property
    def teeth_child_benchmark_path(self) -> Path:
        return self.logs_dir / "teeth_child_benchmark.json"

    @property
    def teeth_roi_path(self) -> Path:
        return self.logs_dir / "teeth_roi.json"

    @property
    def teeth_roi_input_path(self) -> Path:
        return self.input_dir / "teeth_roi.nii.gz"

    @property
    def teeth_multilabel_roi_path(self) -> Path:
        return self.teeth_experimental_dir / "teeth_multilabel_roi.nii.gz"

    @property
    def teeth_multilabel_fullspace_path(self) -> Path:
        return self.teeth_experimental_dir / "teeth_multilabel_fullspace.nii.gz"

    @property
    def teeth_multilabel_path(self) -> Path:
        return self.teeth_multilabel_fullspace_path

    @property
    def dentalseg_input_dir(self) -> Path:
        return self.input_dir / "dentalsegmentator_nnunet"

    @property
    def dentalseg_predictions_dir(self) -> Path:
        return self.dentalsegmentator_dir / "nnunet_predictions"

    @property
    def dentalseg_multilabel_path(self) -> Path:
        return self.dentalsegmentator_dir / "dentalsegmentator_multilabel.nii.gz"

    @property
    def readme_path(self) -> Path:
        return self.root / "README_OUTPUT.md"


def prepare_case_output(root: Path) -> CaseOutput:
    root = root.resolve()
    case = CaseOutput(
        root=root,
        input_dir=root / "input",
        raw_segmentations_dir=root / "segmentations" / "raw_totalseg",
        teeth_experimental_dir=root / "segmentations" / "teeth_experimental",
        dentalsegmentator_dir=root / "segmentations" / "dentalsegmentator",
        logs_dir=root / "logs",
    )
    case.input_dir.mkdir(parents=True, exist_ok=True)
    case.raw_segmentations_dir.mkdir(parents=True, exist_ok=True)
    case.teeth_experimental_dir.mkdir(parents=True, exist_ok=True)
    case.dentalsegmentator_dir.mkdir(parents=True, exist_ok=True)
    case.logs_dir.mkdir(parents=True, exist_ok=True)
    return case


def copy_source_if_requested(input_path: Path, case: CaseOutput, copy_input: bool) -> Path | None:
    if not copy_input:
        return None
    input_path = input_path.resolve()
    suffix = ".nii.gz" if input_path.name.endswith(".nii.gz") else input_path.suffix
    destination = case.input_dir / f"source{suffix}"
    if input_path != destination:
        shutil.copy2(input_path, destination)
    return destination
