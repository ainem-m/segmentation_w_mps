#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from totalsegmentator_wrapper_mac.surface_preview import (
    VIEWER_BUNDLE_FILENAME,
    _html_document,
)


DATA_PATTERN = re.compile(r"const DATA = (\{.*?\});\nconst canvas", re.DOTALL)
DATA_JSON_PATTERN = re.compile(
    r'<script id="viewerData" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def parse_source(value: str) -> tuple[str, str, Path]:
    parts = value.split("=", 2)
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError("source must be KEY=TITLE=INDEX_HTML")
    return parts[0], parts[1], Path(parts[2]).expanduser().resolve()


def read_payload(path: Path) -> dict:
    document = path.read_text(encoding="utf-8")
    json_match = DATA_JSON_PATTERN.search(document)
    if json_match is not None:
        return json.loads(json_match.group(1))
    match = DATA_PATTERN.search(document)
    payload_path = path
    if match is None:
        payload_path = path.with_name(VIEWER_BUNDLE_FILENAME)
        if not payload_path.exists():
            raise ValueError(
                f"viewer payload not found in {path} or {payload_path}"
            )
        match = DATA_PATTERN.search(payload_path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"viewer payload not found: {payload_path}")
    return json.loads(match.group(1))


def build_comparison_viewer(*, sources: list[tuple[str, str, Path]], output: Path) -> None:
    if not sources:
        raise ValueError("at least one --source is required")

    models = []
    meshes = []
    label_count = 0
    for source_index, (key, title, path) in enumerate(sources):
        payload = read_payload(path)
        models.append({"key": key, "title": title})
        label_count += int(payload.get("labelCount", 0))
        source_meshes = list(payload.get("meshes", []))
        has_default_visible = any(bool(mesh.get("defaultVisible")) for mesh in source_meshes)
        for mesh in source_meshes:
            item = dict(mesh)
            kind = str(item["name"])
            item["name"] = f"{key}__{kind}"
            item["kind"] = kind
            item["model"] = key
            item["modelTitle"] = title
            item["modelDefaultVisible"] = bool(item.get("defaultVisible")) or (
                not has_default_visible and kind == "all_nonzero"
            )
            item["defaultVisible"] = source_index == 0 and item["modelDefaultVisible"]
            meshes.append(item)

    payload = {
        "dataLabel": "モデル比較（同一CT・共通カメラ）",
        "labelCount": label_count,
        "smoothing": {"preset": "slicer_like", "iterations": 10, "lambda": 0.5, "mu": -0.53},
        "materialPreset": "rich",
        "models": models,
        "comparisonCamera": {
            "yawDegrees": -22.0,
            "pitchDegrees": 89.0,
            "rollDegrees": 180.0,
            "zoom": 1.64,
            "pan": [0.0, 0.0],
        },
        "meshes": meshes,
    }
    html = _html_document(payload)
    html = html.replace(
        '<h2>表示</h2>',
        '<h2>比較モデル</h2><label class="controlRow"><span class="controlLabel">表示中</span>'
        '<select id="comparisonModel" aria-label="比較モデル"></select></label>'
        '<button id="comparisonAngle" type="button" aria-label="比較用固定アングルを適用">'
        '比較用固定アングル</button><h2>表示</h2>',
        1,
    )
    html = html.replace(
        "const visible = Object.fromEntries(DATA.meshes.map(m => [m.name, !!m.defaultVisible]));",
        "const visible = Object.fromEntries(DATA.meshes.map(m => [m.name, !!m.defaultVisible]));\n"
        "const comparisonModel = document.getElementById('comparisonModel');\n"
        "for (const model of DATA.models || []) {\n"
        "  const option = document.createElement('option'); option.value = model.key; option.textContent = model.title; comparisonModel.appendChild(option);\n"
        "}\n"
        "comparisonModel.onchange = () => selectComparisonModel(comparisonModel.value);\n"
        "document.getElementById('comparisonAngle').onclick = () => applyComparisonCamera();",
        1,
    )
    html = html.replace(
        "mesh.layerCountNode = countNode;",
        "mesh.layerCountNode = countNode; mesh.layerInput = input;",
        1,
    )
    html = html.replace(
        "const material = materialFor(raw.name,",
        "const material = materialFor(raw.kind || raw.name,",
        1,
    )
    html = html.replace(
        "const mesh = { raw, name: raw.name, labels:",
        "const mesh = { raw, name: raw.name, kind: raw.kind, model: raw.model, "
        "modelDefaultVisible: raw.modelDefaultVisible, labels:",
        1,
    )
    html = html.replace(
        "meshDisplayName(mesh.name)",
        "meshDisplayName(mesh.kind || mesh.name)",
    )
    html = html.replace(
        "materialFor(mesh.name,",
        "materialFor(mesh.kind || mesh.name,",
    )
    html = html.replace(
        "effectiveSmoothingConfig(mesh.name,",
        "effectiveSmoothingConfig(mesh.kind || mesh.name,",
    )
    html = html.replace(
        "mesh.name === 'jaws'",
        "(mesh.kind || mesh.name) === 'jaws'",
    )
    html = html.replace(
        "function setInputMode(mode) {",
        "function selectComparisonModel(modelKey) {\n"
        "  for (const mesh of preparedMeshes) {\n"
        "    const checked = mesh.model === modelKey && !!mesh.modelDefaultVisible;\n"
        "    visible[mesh.name] = checked;\n"
        "    if (mesh.layerInput) mesh.layerInput.checked = checked;\n"
        "  }\n"
        "  updateLayerStats(); draw();\n"
        "}\n"
        "function applyComparisonCamera() {\n"
        "  const preset = DATA.comparisonCamera;\n"
        "  fitVisible(Number(preset.zoom));\n"
        "  const base = orientationFromYawPitch(Number(preset.yawDegrees), Number(preset.pitchDegrees));\n"
        "  const roll = axisAngleMatrix([0, 0, 1], Number(preset.rollDegrees) * Math.PI / 180);\n"
        "  camera.orientation = orthonormalizeOrientation(mat3Multiply(roll, base));\n"
        "  camera.pan = [Number(preset.pan[0]), Number(preset.pan[1])];\n"
        "  draw();\n"
        "}\n"
        "function setInputMode(mode) {",
        1,
    )
    if "comparisonModel" not in html or "selectComparisonModel" not in html:
        raise RuntimeError("comparison controls were not inserted")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", type=parse_source, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_comparison_viewer(sources=args.source, output=args.output.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
