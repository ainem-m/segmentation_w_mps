"""Validate the pinned user-selected TGNet checkpoint set without inference."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .ios_tgnet_final import (
    TGNET_FINAL_CHECKPOINTS,
    _sha256,
    materialize_checkpoint_archive,
    validate_checkpoint_directory_layout,
)


class TGNetSelectionValidationError(ValueError):
    """Carry a stable UI-safe code separately from local diagnostic detail."""

    def __init__(self, code: str, safe_detail: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.code = code
        self.safe_detail = safe_detail


def _validate_paths(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for role in ("fps", "boundary"):
        path = paths[role]
        expected = str(TGNET_FINAL_CHECKPOINTS[role]["sha256"])
        actual = _sha256(path)
        if actual != expected:
            raise TGNetSelectionValidationError(
                "tgnet_checkpoint_hash_mismatch",
                "checkpointが指定の配布セットと一致しません。",
                (
                    f"{path.name} のSHA-256が指定セットと一致しません。"
                    f" expected={expected}, actual={actual}"
                ),
            )
        checkpoints.append(
            {
                "role": role,
                "filename": path.name,
                "sha256": actual,
                "validation": "pinned-checkpoint-sha256-passed",
            }
        )
    return checkpoints


def validate_selection(path: Path) -> dict[str, Any]:
    # Keep the user-selected leaf intact.  The UI preserves this exact path for
    # inference, where TGNet's archive/directory materializers reject symlinks.
    # Resolving here would validate a different object and let the later run
    # fail after the user already passed selection validation.
    selected = path.expanduser().absolute()
    if selected.is_symlink():
        raise TGNetSelectionValidationError(
            "tgnet_selection_invalid",
            "指定のckpts(new).zip、またはその展開済みフォルダを選択してください。",
            "TGNet checkpoint selection must not be a symbolic link.",
        )
    if selected.suffix.lower() == ".zip":
        try:
            with tempfile.TemporaryDirectory(prefix="tgnet-selection-validate-") as tmp:
                paths = materialize_checkpoint_archive(selected, Path(tmp))
                checkpoints = _validate_paths(paths)
        except TGNetSelectionValidationError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise TGNetSelectionValidationError(
                "tgnet_checkpoint_archive_invalid",
                "ZIPを安全に展開して確認できませんでした。",
                str(exc),
            ) from exc
        selection_type = "zip"
    elif selected.is_dir():
        try:
            checkpoints = _validate_paths(
                validate_checkpoint_directory_layout(selected)
            )
        except TGNetSelectionValidationError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise TGNetSelectionValidationError(
                "tgnet_checkpoint_set_incomplete",
                "必要な2つのcheckpointが揃っていないか、配置が異なります。",
                str(exc),
            ) from exc
        selection_type = "directory"
    else:
        raise TGNetSelectionValidationError(
            "tgnet_selection_invalid",
            "指定のckpts(new).zip、またはその展開済みフォルダを選択してください。",
            (
                "指定のckpts(new).zip、またはその展開済みフォルダを"
                "選択してください。"
            ),
        )
    return {
        "schema": "tgnet_checkpoint_selection_validation.v1",
        "status": "success",
        "model_family": "tgnet",
        "variant": "published-behavior-fps-plus-boundary",
        "selection_type": selection_type,
        "source": "user-provided",
        "license": "not-verified",
        "bundled_by_app": False,
        "checkpoints": checkpoints,
        "architecture_validation": (
            "exact-pinned-checkpoint-content; strict tensor validation at inference"
        ),
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = validate_selection(args.model)
    except TGNetSelectionValidationError as exc:
        print(f"TGNet checkpoint validation failed [{exc.code}]: {exc}", file=sys.stderr)
        _write_json(
            args.json,
            {
                "schema": "tgnet_checkpoint_selection_validation.v1",
                "status": "failed",
                "message": "TGNetの重みを確認できませんでした。",
                "error_code": exc.code,
                "safe_detail": exc.safe_detail,
            },
        )
        return 2
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"TGNet checkpoint validation failed [tgnet_validation_failed]: {exc}", file=sys.stderr)
        _write_json(
            args.json,
            {
                "schema": "tgnet_checkpoint_selection_validation.v1",
                "status": "failed",
                "message": "TGNetの重みを確認できませんでした。",
                "error_code": "tgnet_validation_failed",
                "safe_detail": "重みの検証処理を完了できませんでした。",
            },
        )
        return 2
    _write_json(args.json, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
