# 00 Agent Directive

## Mission

Build a **Mac Preview** proving that dental CBCT-derived volumes can be segmented locally on Apple Silicon using **PyTorch MPS + TotalSegmentator**, then handed back to **3D Slicer** for review, correction, and STL export.

The goal is not to build a complete clinical dental automation product. The goal is to produce a credible, demonstrable, benchmarked preview that can become a high-impact X.com post and a foundation for later productization.

## Core thesis

Previous public interest came from “free OSS dental CBCT segmentation.” That angle has already been used. The new angle is:

```text
Slicer extensions have historically been awkward or slow on Mac GPU.
With a modern PyTorch that supports the required MPS operators, we can run segmentation outside Slicer on Apple Silicon/MPS, then return results to Slicer.
```

Therefore, **the first product is a Mac/MPS proof, not a DICOM product**. DICOM
normalization/rescue is now a separate C++ Mac-binary track and must not be
folded into the Python preview package by accident.

## Non-negotiable constraints

1. **Run inference outside Slicer.**
   - Do not modify SlicerTotalSegmentator.
   - Do not depend on Slicer Python for inference.
   - Slicer is only a downstream viewer/editor/export tool.

2. **Start with NIfTI input.**
   - DICOM folder support may be best-effort only.
   - Do not build DICOM normalization into the Python preview package.
   - Keep DICOM normalization/rescue in the separate C++ binary track.

3. **Use TotalSegmentator first.**
   - Prioritize `craniofacial_structures`.
   - Then test `teeth`.
   - DentalSegmentator pure model support is later.

4. **MPS support must be proven, not assumed.**
   - First run ConvTranspose3D smoke test.
   - Then run TotalSegmentator smoke test.
   - Then benchmark CPU vs MPS.

5. **Keep everything non-clinical.**
   - No diagnosis claims.
   - No treatment planning claims.
   - No implant planning claims.
   - No airway risk assessment claims.
   - Use “research / education / non-clinical evaluation / visualization” language.

6. **Do not overbuild.**
   - No C++ inside the Python preview package.
   - No ONNX.
   - No Core ML.
   - No DICOM SEG.
   - No PACS.
   - No DICOM database.
   - No custom 3D editor.
   - No full segmentation correction UI.

## Definition of Done: Mac Preview v0.1

The preview is done when all of the following are true on one Apple Silicon Mac:

```text
[ ] ConvTranspose3D MPS smoke test passes in FP32.
[ ] TotalSegmentator `craniofacial_structures` runs with --device mps on a sample NIfTI.
[ ] TotalSegmentator `teeth` is attempted with --device mps and result documented.
[ ] CPU vs MPS benchmark is recorded in a machine-readable log.
[ ] Output segmentation can be loaded in Slicer by generated open_in_slicer.py.
[ ] Segment names and colors are human-readable enough for demo.
[ ] A README explains non-clinical scope and exact environment.
[ ] A demo video can be recorded from the working flow.
```

## Success metric for the project

The technical success metric is not “supports every DICOM.”

The success metric is:

```text
A viewer immediately understands that Apple Silicon Mac can now run dental-relevant segmentation locally at practical speed, with Slicer handoff.
```

## Implementation discipline

When uncertain, choose the path that produces a benchmarked demo faster.

```text
Prefer: NIfTI + TotalSegmentator + MPS + Slicer script
Avoid: DICOM perfect import + GUI polish + model zoo + native packaging
```

If a task does not directly support the Mac/MPS demo, defer it.
