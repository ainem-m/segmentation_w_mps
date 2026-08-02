#!/usr/bin/env python3
"""Tune unpublished TGNet grouping parameters on fixed official-GT cases.

This evaluation-only harness consumes cached PGM outputs. Ground-truth labels
are used only for scoring and are never passed to TGNet or product inference.
Only cases marked ``tuning`` participate in Optuna. Pareto candidates are
evaluated on cases marked ``validation`` after the study has finished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from totalsegmentator_wrapper_mac.ios_tgnet import _select_instances


METRIC_NAMES = (
    "mean_golden_instance_iou",
    "tooth_iou",
    "matched_golden_tooth_accuracy",
    "instance_f1_at_iou_0_5",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _float_pair(text: str) -> tuple[float, float]:
    values = tuple(float(value) for value in text.split(","))
    if len(values) != 2 or values[0] >= values[1]:
        raise argparse.ArgumentTypeError("expected ascending FLOAT,FLOAT")
    return values


def _int_pair(text: str) -> tuple[int, int]:
    values = tuple(int(value) for value in text.split(","))
    if len(values) != 2 or values[0] >= values[1]:
        raise argparse.ArgumentTypeError("expected ascending INT,INT")
    return values


def _score_instances(predicted: np.ndarray, golden: np.ndarray) -> dict[str, float]:
    predicted_ids = [int(value) for value in np.unique(predicted) if value]
    golden_ids = [int(value) for value in np.unique(golden) if value]
    pair_iou = np.zeros((len(predicted_ids), len(golden_ids)), dtype=np.float64)
    intersections = np.zeros_like(pair_iou, dtype=np.int64)
    for row, predicted_id in enumerate(predicted_ids):
        predicted_mask = predicted == predicted_id
        for column, golden_id in enumerate(golden_ids):
            golden_mask = golden == golden_id
            intersection = int(np.logical_and(predicted_mask, golden_mask).sum())
            union = int(np.logical_or(predicted_mask, golden_mask).sum())
            intersections[row, column] = intersection
            pair_iou[row, column] = intersection / union if union else 0.0
    rows, columns = (
        linear_sum_assignment(-pair_iou)
        if pair_iou.size
        else (np.array([], dtype=int), np.array([], dtype=int))
    )
    matched = int(intersections[rows, columns].sum()) if len(rows) else 0
    true_positives = int(np.sum(pair_iou[rows, columns] >= 0.5))
    false_positives = len(predicted_ids) - true_positives
    false_negatives = len(golden_ids) - true_positives
    f1_denominator = (
        2 * true_positives + false_positives + false_negatives
    )
    golden_tooth = golden > 0
    predicted_tooth = predicted > 0
    intersection = int(np.logical_and(predicted_tooth, golden_tooth).sum())
    union = int(np.logical_or(predicted_tooth, golden_tooth).sum())
    return {
        "mean_golden_instance_iou": (
            float(pair_iou[rows, columns].sum() / len(golden_ids))
            if golden_ids
            else 1.0
        ),
        "tooth_iou": intersection / union if union else 1.0,
        "matched_golden_tooth_accuracy": (
            matched / int(golden_tooth.sum()) if golden_tooth.any() else 1.0
        ),
        "instance_f1_at_iou_0_5": (
            2 * true_positives / f1_denominator
            if f1_denominator
            else 1.0
        ),
        "instance_count_score": (
            1.0
            - abs(len(predicted_ids) - len(golden_ids))
            / max(len(predicted_ids), len(golden_ids))
            if predicted_ids or golden_ids
            else 1.0
        ),
    }


def _load_cases(
    manifest: dict[str, Any], cache_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    by_role: dict[str, list[dict[str, Any]]] = {
        "tuning": [],
        "validation": [],
    }
    for case in manifest["cases"]:
        role = str(case.get("role"))
        if role not in by_role:
            raise ValueError(f"unsupported or missing case role: {role!r}")
        cache_path = cache_dir / f"{case['key']}.npz"
        if not cache_path.is_file():
            raise FileNotFoundError(f"missing fixed PGM cache: {cache_path}")
        with np.load(cache_path, allow_pickle=False) as cached:
            required = {"shifted", "tooth_mask", "golden_instances"}
            missing = sorted(required - set(cached.files))
            if missing:
                raise ValueError(f"{cache_path} misses arrays: {missing}")
            shifted = np.asarray(cached["shifted"], dtype=np.float32)
            tooth_mask = np.asarray(cached["tooth_mask"], dtype=bool)
            golden = np.asarray(cached["golden_instances"], dtype=np.int16)
        if shifted.shape != (len(tooth_mask), 3) or golden.shape != tooth_mask.shape:
            raise ValueError(
                f"incompatible cached array shapes for {case['key']}: "
                f"{shifted.shape}, {tooth_mask.shape}, {golden.shape}"
            )
        by_role[role].append(
            {
                "key": str(case["key"]),
                "jaw": str(case["jaw"]),
                "stratum": str(case["stratum"]),
                "golden_teeth": int(case["teeth"]),
                "cache": str(cache_path.resolve()),
                "cache_sha256": _sha256(cache_path),
                "shifted": shifted,
                "tooth_mask": tooth_mask,
                "golden": golden,
            }
        )
    if not by_role["tuning"] or not by_role["validation"]:
        raise ValueError("both tuning and validation roles require at least one case")
    return by_role


def _evaluate_cases(
    cases: list[dict[str, Any]], params: dict[str, float | int]
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            labels, clusters = _select_instances(
                case["shifted"],
                case["tooth_mask"],
                epsilon=float(params["epsilon"]),
                min_points=int(params["min_points"]),
                mean_shift_bandwidth=float(params["mean_shift_bandwidth"]),
                minimum_cluster_points=int(params["minimum_cluster_points"]),
                maximum_clusters=int(params["maximum_clusters"]),
            )
            metrics = _score_instances(labels, case["golden"])
            result = {
                "key": case["key"],
                "jaw": case["jaw"],
                "stratum": case["stratum"],
                "golden_teeth": case["golden_teeth"],
                "clusters": len(clusters),
                "status": "pass",
                **metrics,
            }
        except (RuntimeError, ValueError) as error:
            # A failed grouping is an observed trial outcome, not a fallback.
            result = {
                "key": case["key"],
                "jaw": case["jaw"],
                "stratum": case["stratum"],
                "golden_teeth": case["golden_teeth"],
                "clusters": 0,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                **{
                    name: 0.0
                    for name in (*METRIC_NAMES, "instance_count_score")
                },
            }
        results.append(result)
    aggregate = {
        name: float(np.mean([float(case[name]) for case in results]))
        for name in (*METRIC_NAMES, "instance_count_score")
    }
    aggregate["failed_cases"] = sum(case["status"] == "failed" for case in results)
    aggregate["mean_cluster_count"] = float(
        np.mean([int(case["clusters"]) for case in results])
    )
    return {"aggregate_macro_mean": aggregate, "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture-mode", required=True)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument(
        "--epsilon-range", type=_float_pair, default=(0.010, 0.100)
    )
    parser.add_argument(
        "--min-points-range", type=_int_pair, default=(4, 40)
    )
    parser.add_argument(
        "--bandwidth-range", type=_float_pair, default=(0.040, 0.200)
    )
    parser.add_argument(
        "--minimum-cluster-points-range",
        type=_int_pair,
        default=(20, 300),
    )
    parser.add_argument(
        "--maximum-clusters-range", type=_int_pair, default=(16, 32)
    )
    parser.add_argument(
        "--storage",
        type=Path,
        help="Resume-capable Optuna SQLite file (defaults beside --output).",
    )
    parser.add_argument(
        "--journal",
        type=Path,
        help="Append-only per-trial JSONL (defaults beside --output).",
    )
    parser.add_argument(
        "--optuna-module-path",
        type=Path,
        default=Path("artifacts/research_python"),
    )
    args = parser.parse_args()
    if args.trials <= 0:
        parser.error("--trials must be positive")

    started = time.perf_counter()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = _load_cases(manifest, args.cache_dir)
    manifest_sha256 = _sha256(args.manifest)
    storage_path = (
        args.storage
        if args.storage is not None
        else args.output.with_suffix(".optuna.sqlite3")
    ).resolve()
    journal_path = (
        args.journal
        if args.journal is not None
        else args.output.with_suffix(".trials.jsonl")
    ).resolve()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    sys.path.append(str(args.optuna_module_path.resolve()))
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name=(
            f"tgnet-grouping-{args.architecture_mode}-"
            f"{manifest_sha256[:12]}-{args.seed}"
        ),
        storage=f"sqlite:///{storage_path}",
        load_if_exists=True,
        directions=tuple("maximize" for _ in METRIC_NAMES),
        sampler=optuna.samplers.TPESampler(seed=args.seed),
    )
    tuning_results: dict[int, dict[str, Any]] = {
        trial.number: trial.user_attrs["tuning"]
        for trial in study.trials
        if "tuning" in trial.user_attrs
    }

    def objective(trial: optuna.Trial) -> tuple[float, float, float]:
        params: dict[str, float | int] = {
            "epsilon": trial.suggest_float("epsilon", *args.epsilon_range),
            "min_points": trial.suggest_int(
                "min_points", *args.min_points_range
            ),
            "mean_shift_bandwidth": trial.suggest_float(
                "mean_shift_bandwidth", *args.bandwidth_range
            ),
            "minimum_cluster_points": trial.suggest_int(
                "minimum_cluster_points",
                *args.minimum_cluster_points_range,
                log=True,
            ),
            "maximum_clusters": trial.suggest_int(
                "maximum_clusters", *args.maximum_clusters_range
            ),
        }
        evaluation = _evaluate_cases(cases["tuning"], params)
        tuning_results[trial.number] = evaluation
        trial.set_user_attr("tuning", evaluation)
        with journal_path.open("a", encoding="utf-8") as journal:
            journal.write(
                json.dumps(
                    {
                        "trial": trial.number,
                        "params": params,
                        "tuning": evaluation,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        aggregate = evaluation["aggregate_macro_mean"]
        print(
            f"trial={trial.number}",
            f"instance={aggregate['mean_golden_instance_iou']:.4f}",
            f"tooth={aggregate['tooth_iou']:.4f}",
            f"matched={aggregate['matched_golden_tooth_accuracy']:.4f}",
            f"f1={aggregate['instance_f1_at_iou_0_5']:.4f}",
            f"failed={aggregate['failed_cases']}",
            flush=True,
        )
        return tuple(float(aggregate[name]) for name in METRIC_NAMES)

    completed_before = sum(
        trial.state == optuna.trial.TrialState.COMPLETE
        for trial in study.trials
    )
    remaining_trials = max(0, args.trials - completed_before)
    if remaining_trials:
        study.optimize(objective, n_trials=remaining_trials)
    pareto_numbers = sorted(trial.number for trial in study.best_trials)
    validation_results: dict[int, dict[str, Any]] = {}
    trials: list[dict[str, Any]] = []
    for trial in study.trials:
        item = {
            "number": trial.number,
            "params": trial.params,
            "values": list(trial.values) if trial.values is not None else None,
            "state": trial.state.name,
            "is_pareto": trial.number in pareto_numbers,
            "tuning": tuning_results.get(trial.number),
        }
        if trial.number in pareto_numbers:
            validation = _evaluate_cases(cases["validation"], trial.params)
            validation_results[trial.number] = validation
            item["validation"] = validation
        trials.append(item)

    validation_ranking = sorted(
        (
            {
                "trial": number,
                **evaluation["aggregate_macro_mean"],
            }
            for number, evaluation in validation_results.items()
        ),
        key=lambda item: (
            item["mean_golden_instance_iou"],
            item["matched_golden_tooth_accuracy"],
            item["tooth_iou"],
        ),
        reverse=True,
    )
    document = {
        "schema": "tgnet_grouping_multicase_optimization.v1",
        "evaluation_only": True,
        "ground_truth_used_by_inference": False,
        "architecture_mode": args.architecture_mode,
        "batchnorm_mode": "per-scan",
        "fixed_pgm_outputs": True,
        "paper_fixed_parameters": {"high_variance_multiplier": 3.0},
        "search": {
            "method": "optuna-tpe-multi-objective",
            "optuna_version": optuna.__version__,
            "seed": args.seed,
            "requested_total_trials": args.trials,
            "completed_trials": sum(
                trial.state == optuna.trial.TrialState.COMPLETE
                for trial in study.trials
            ),
            "resumed_completed_trials": completed_before,
            "storage": str(storage_path),
            "journal": str(journal_path),
            "directions": [f"maximize {name}" for name in METRIC_NAMES],
            "ranges": {
                "epsilon": list(args.epsilon_range),
                "min_points": list(args.min_points_range),
                "mean_shift_bandwidth": list(args.bandwidth_range),
                "minimum_cluster_points": list(
                    args.minimum_cluster_points_range
                ),
                "maximum_clusters": list(args.maximum_clusters_range),
            },
            "failure_policy": "zero metrics for the failed case; error retained",
            "tuning_roles": ["tuning"],
            "validation_excluded_during_optimization": True,
            "pareto_trial_numbers": pareto_numbers,
        },
        "manifest": {
            "path": str(args.manifest.resolve()),
            "sha256": manifest_sha256,
            "dataset": manifest["dataset"],
            "split": manifest["split"],
            "tuning_case_keys": [case["key"] for case in cases["tuning"]],
            "validation_case_keys": [case["key"] for case in cases["validation"]],
        },
        "cache": {
            "directory": str(args.cache_dir.resolve()),
            "cases": [
                {
                    "key": case["key"],
                    "role": role,
                    "path": case["cache"],
                    "sha256": case["cache_sha256"],
                }
                for role, role_cases in cases.items()
                for case in role_cases
            ],
        },
        "validation_ranking": validation_ranking,
        "trials": trials,
        "seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.output} with {len(pareto_numbers)} Pareto candidates",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
