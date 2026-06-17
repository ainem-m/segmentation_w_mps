# 01 Product Strategy

## Positioning

Working title:

```text
TotalSegmentator Wrapper for Mac
```

One-line positioning:

```text
Apple Silicon Macで歯科CBCT由来volumeをローカルMPS推論し、Slicerで修正できる形に戻す非臨床プレビュー。
```

English version:

```text
A non-clinical Apple Silicon Mac preview for local MPS dental-relevant CT segmentation, with Slicer handoff for review and correction.
```

## Public narrative

Do not market this as a replacement for commercial clinical tools. Do not compare against Relu, Diagnocat, CephX, or implant planning products.

The public story is:

```text
去年紹介したDentalSegmentatorはすごかったが、MacではGPUが使いにくく、導入やSlicer更新で詰まる人がいた。
今回はSlicerの外で最新PyTorch/MPSを使ってTotalSegmentatorを回し、結果をSlicerに戻す別ルートを試した。
```

## Why this is not a repost of the previous buzz

Previous buzz:

```text
無料OSSで歯科CBCT segmentationができる。
```

New angle:

```text
Apple Silicon Macで、ローカルGPU/MPSを使って高速化できる。
Slicer内ではなく、Slicer外で推論し、Slicerに戻す。
```

The demo must emphasize:

```text
- Apple Silicon
- MPS
- local inference
- speed comparison
- Slicer handoff
```

## Audience

Primary:

```text
- Dental clinicians and technicians who previously saved/shared DentalSegmentator content
- Mac users who could not use CUDA
- Slicer-curious dental users
- Dental CBCT / CAD automation hobbyists and researchers
```

Secondary:

```text
- Orthodontic researchers
- Dental lab technologists
- Students
- OSS medical imaging users
```

## Not the target

```text
- Clinics seeking regulatory-grade diagnostic automation
- Implant planning workflows
- Surgical guide production
- Airway diagnosis
- PACS-integrated enterprise users
```

## Launch artifact

The initial public artifact should be:

```text
- 30–60 sec video
- benchmark table
- minimal GitHub repo
- script or preview app
- clear non-clinical disclaimer
```

The launch artifact does not need broad DICOM compatibility.

## Launch post structure

Suggested X.com post:

```text
去年バズったDentalSegmentatorは、MacではGPUが使いにくいのがネックでした。

今回はSlicerの外でTotalSegmentatorをApple Silicon/MPS実行し、歯科CBCT由来volumeをローカルsegmentation → Slicerで確認・修正できる流れを試しています。

CPU vs MPSの実測も取りました。
非臨床・研究/教育用です。
```

Video flow:

```text
0–3s: “Apple Silicon Mac / device: mps”
3–10s: task selection and benchmark timer
10–25s: segmentation output
25–40s: Slicer opens volume + segmentation
40–50s: label toggles: mandible, teeth, sinus, canal/pharynx if available
50–60s: benchmark table + non-clinical notice
```

## Productization after buzz

Only after the preview receives strong signal:

```text
Signal A: “I want to run this on my own data”
→ Add Python package / setup command.

Signal B: “DICOM import fails”
→ Add dcm2niix and then native DICOM helper.

Signal C: “I want one-click Mac app”
→ Bundle Python runtime and weights manager.

Signal D: “I need DentalSegmentator exact output”
→ Add nnU-Net/DentalSegmentator backend.
```

No signal, no expansion.
