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
    toothseg_dir: Path
    logs_dir: Path
    report_filename: str = "README_OUTPUT.md"

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
    def toothseg_input_dir(self) -> Path:
        return self.input_dir / "toothseg_nnunet"

    @property
    def toothseg_roi_path(self) -> Path:
        return self.logs_dir / "toothseg_roi.json"

    @property
    def toothseg_roi_input_path(self) -> Path:
        return self.input_dir / "toothseg_roi.nii.gz"

    @property
    def toothseg_semantic_input_dir(self) -> Path:
        return self.toothseg_input_dir / "semantic"

    @property
    def toothseg_instance_input_dir(self) -> Path:
        return self.toothseg_input_dir / "instance_0p2mm"

    @property
    def toothseg_semantic_predictions_dir(self) -> Path:
        return self.toothseg_dir / "semantic_predictions"

    @property
    def toothseg_instance_predictions_dir(self) -> Path:
        return self.toothseg_dir / "instance_border_core_predictions"

    @property
    def toothseg_instances_dir(self) -> Path:
        return self.toothseg_dir / "instances"

    @property
    def toothseg_multilabel_path(self) -> Path:
        return self.toothseg_dir / "toothseg_fdi_multilabel.nii.gz"

    @property
    def toothseg_multilabel_roi_path(self) -> Path:
        return self.toothseg_dir / "toothseg_fdi_multilabel_roi.nii.gz"

    @property
    def readme_path(self) -> Path:
        return self.root / self.report_filename


def prepare_case_output(
    root: Path,
    *,
    diagnostics_subdir: str | None = None,
    report_filename: str = "README_OUTPUT.md",
) -> CaseOutput:
    root = root.resolve()
    logs_dir = root / "logs"
    if diagnostics_subdir is not None:
        if not diagnostics_subdir or Path(diagnostics_subdir).name != diagnostics_subdir:
            raise ValueError("diagnostics_subdir must be a single directory name")
        logs_dir = logs_dir / diagnostics_subdir
    case = CaseOutput(
        root=root,
        input_dir=root / "input",
        raw_segmentations_dir=root / "segmentations" / "raw_totalseg",
        teeth_experimental_dir=root / "segmentations" / "teeth_experimental",
        dentalsegmentator_dir=root / "segmentations" / "dentalsegmentator",
        toothseg_dir=root / "segmentations" / "toothseg",
        logs_dir=logs_dir,
        report_filename=report_filename,
    )
    case.input_dir.mkdir(parents=True, exist_ok=True)
    case.raw_segmentations_dir.mkdir(parents=True, exist_ok=True)
    case.teeth_experimental_dir.mkdir(parents=True, exist_ok=True)
    case.dentalsegmentator_dir.mkdir(parents=True, exist_ok=True)
    case.toothseg_dir.mkdir(parents=True, exist_ok=True)
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
