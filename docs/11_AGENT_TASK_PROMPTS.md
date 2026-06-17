# 11 Agent Task Prompts

Use these as direct prompts for a coding agent.

## Prompt 1: repository skeleton

```text
Create a Python repository for TotalSegmentator Wrapper for Mac. The goal is to run TotalSegmentator outside Slicer on Apple Silicon/MPS and generate Slicer handoff scripts. Do not implement DICOM normalizer, ONNX, GUI, or DentalSegmentator exact mode. Add modules for device checks, TotalSegmentator runner, benchmark logging, output folder layout, and Slicer export. Include non-clinical disclaimers in README.
```

## Prompt 2: MPS smoke test

```text
Implement scripts/smoke_test_mps_convtranspose3d.py. It must print Python version, torch version, MPS built/available flags, then run torch.nn.ConvTranspose3d on device=mps with dtype=float32. It must fail nonzero if MPS is unavailable or the op fails. Do not test fp16/bf16. Save a JSON result if --json path is provided.
```

## Prompt 3: TotalSegmentator runner

```text
Implement a TotalSegmentator subprocess runner. Inputs: input NIfTI path, output directory, task, device. Capture stdout/stderr, exit code, elapsed time. Generate logs/run.log and logs/benchmark.json. Do not include patient identifiers. Provide a CLI command: totalsegmentator-wrapper-mac run --input ... --task craniofacial_structures --device mps --output ...
```

## Prompt 4: benchmark command

```text
Implement totalsegmentator-wrapper-mac benchmark. It runs the same task on CPU and MPS, if MPS smoke test passes, then writes benchmark_summary.json and benchmark_summary.md. Include machine, macOS, Python, torch, TotalSegmentator version, input shape if readable, elapsed times, speedup, and status.
```

## Prompt 5: Slicer handoff

```text
Implement generation of open_in_slicer.py in the output folder. The script should load the source NIfTI and segmentation output files into 3D Slicer. It should attempt to set display names/colors and open Segment Editor if possible. If conversion to a Slicer Segmentation node is not reliable, load labelmaps and print manual instructions. Keep the script self-contained with absolute paths.
```

## Prompt 6: minimal Mac Preview UI

```text
After CLI works, create a minimal Apple Silicon Mac preview UI. It can be SwiftUI, PySide6, or a simple app wrapper. It must let the user select a NIfTI file, select TotalSegmentator task, select device, run the backend, show logs/progress, show elapsed time, open output folder, and open Slicer script. Do not implement 3D preview, DICOM browser, or segmentation editor.
```

## Prompt 7: launch README

```text
Write a launch README for TotalSegmentator Wrapper for Mac. It must explain the non-clinical research/education scope, Apple Silicon/MPS purpose, how to run smoke tests, how to run segmentation, how to open in Slicer, how to interpret benchmark logs, and known limitations. Avoid diagnostic/treatment language.
```

## Prompt 8: do not overbuild reminder

```text
Before implementing any requested feature, check whether it directly supports MPS proof, speed benchmark, Slicer handoff, or launch video. If not, put it in Deferred Scope and do not implement it in MVP.
```
