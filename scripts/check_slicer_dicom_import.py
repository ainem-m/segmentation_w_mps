"""Run inside 3D Slicer to check whether a DICOM folder imports and loads.

Usage:
  TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_DICOM_DIR=/path/to/dicom \
  TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_REPORT_JSON=/tmp/slicer_report.json \
  Slicer --no-main-window --python-script scripts/check_slicer_dicom_import.py
"""

from __future__ import annotations

import json
import os
import traceback


def _node_summary(node):
    image = node.GetImageData() if node else None
    spacing = node.GetSpacing() if node else None
    origin = node.GetOrigin() if node else None
    dims = image.GetDimensions() if image else None
    return {
        "name": node.GetName() if node else None,
        "class": node.GetClassName() if node else None,
        "dimensions": list(dims) if dims else None,
        "spacing": list(spacing) if spacing else None,
        "origin": list(origin) if origin else None,
    }


def main():
    dicom_dir = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_DICOM_DIR")
    report_json = os.environ.get("TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_REPORT_JSON")
    if not dicom_dir or not report_json:
        raise RuntimeError("TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_DICOM_DIR and TOTALSEGMENTATOR_WRAPPER_MAC_SLICER_REPORT_JSON are required")

    report = {
        "dicom_dir_basename": os.path.basename(os.path.normpath(dicom_dir)),
        "import_ok": False,
        "load_ok": False,
        "patient_count": 0,
        "study_count": 0,
        "series_count": 0,
        "series": [],
        "scalar_volumes": [],
        "errors": [],
    }

    try:
        import slicer  # type: ignore
        from DICOMLib import DICOMUtils  # type: ignore

        with DICOMUtils.TemporaryDICOMDatabase() as db:
            import_ok = DICOMUtils.importDicom(dicom_dir, db)
            report["import_ok"] = bool(import_ok)
            patients = list(db.patients())
            report["patient_count"] = len(patients)
            for patient in patients:
                studies = list(db.studiesForPatient(patient))
                report["study_count"] += len(studies)
                for study in studies:
                    series_uids = list(db.seriesForStudy(study))
                    report["series_count"] += len(series_uids)
                    for series_uid in series_uids:
                        files = list(db.filesForSeries(series_uid))
                        report["series"].append(
                            {
                                "patient_uid": patient,
                                "study_uid": study,
                                "series_uid": series_uid,
                                "file_count": len(files),
                            }
                        )

            load_results = []
            for patient in patients:
                load_results.append(bool(DICOMUtils.loadPatientByUID(patient)))
            report["load_ok"] = any(load_results)
            report["load_results"] = load_results

            volume_nodes = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
            report["scalar_volumes"] = [_node_summary(node) for node in volume_nodes]
    except Exception as exc:  # noqa: BLE001 - report Slicer-side import failures.
        report["errors"].append(
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )

    with open(report_json, "w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2, ensure_ascii=False)


main()
