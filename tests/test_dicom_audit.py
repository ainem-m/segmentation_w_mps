from __future__ import annotations

import unittest

from totalsegmentator_wrapper_mac.dicom_audit import (
    ORIGINAL_CT_GEOMETRY_OK,
    REJECT,
    SECONDARY_CAPTURE_RESCUE_CANDIDATE,
    classify_series,
)


class DicomAuditTests(unittest.TestCase):
    def test_original_ct_with_complete_geometry_is_accepted_for_conversion(self) -> None:
        result = classify_series(
            {
                "modality": "CT",
                "sop_class_uid": "1.2.840.10008.5.1.4.1.1.2",
                "sop_class_name": "CT Image Storage",
                "image_type": ["ORIGINAL", "PRIMARY", "AXIAL"],
                "series_description": "AXIAL",
                "file_count": 314,
                "shape_consistent": True,
                "has_pixel_spacing": True,
                "image_position_patient_count": 314,
                "image_orientation_patient_count": 314,
            }
        )

        self.assertEqual(result.status, ORIGINAL_CT_GEOMETRY_OK)
        self.assertIn("ct_geometry_tags_present", result.reasons)
        self.assertIn("dcm2niix", result.recommendation)

    def test_ct_without_original_image_type_is_accepted_when_geometry_is_complete(self) -> None:
        result = classify_series(
            {
                "modality": "CT",
                "sop_class_uid": "1.2.840.10008.5.1.4.1.1.2",
                "sop_class_name": "CT Image Storage",
                "image_type": ["DERIVED", "PRIMARY", "AXIAL"],
                "series_description": "AXIAL",
                "file_count": 180,
                "shape_consistent": True,
                "has_pixel_spacing": True,
                "image_position_patient_count": 180,
                "image_orientation_patient_count": 180,
            }
        )

        self.assertEqual(result.status, ORIGINAL_CT_GEOMETRY_OK)
        self.assertIn("ct_geometry_tags_present", result.reasons)
        self.assertIn("image_type_not_original_but_geometry_complete", result.reasons)

    def test_secondary_capture_axial_stack_is_rescue_only_candidate(self) -> None:
        result = classify_series(
            {
                "modality": "OT",
                "sop_class_uid": "1.2.840.10008.5.1.4.1.1.7",
                "sop_class_name": "Secondary Capture Image Storage",
                "image_type": ["DERIVED", "SECONDARY", "SCREEN SAVE", "AVERAGE"],
                "series_description": "AXIAL BO",
                "file_count": 138,
                "shape_consistent": True,
                "has_pixel_spacing": False,
                "image_position_patient_count": 0,
                "image_orientation_patient_count": 0,
            }
        )

        self.assertEqual(result.status, SECONDARY_CAPTURE_RESCUE_CANDIDATE)
        self.assertEqual(result.grade, "C: rescue only")
        self.assertIn("manual_spacing_required", result.reasons)
        self.assertIn("outside this package", result.recommendation)

    def test_secondary_capture_coronal_stack_is_rejected(self) -> None:
        result = classify_series(
            {
                "modality": "OT",
                "sop_class_uid": "1.2.840.10008.5.1.4.1.1.7",
                "sop_class_name": "Secondary Capture Image Storage",
                "image_type": ["DERIVED", "SECONDARY", "SCREEN SAVE"],
                "series_description": "CORONAL BO",
                "file_count": 125,
                "shape_consistent": True,
            }
        )

        self.assertEqual(result.status, REJECT)
        self.assertIn("secondary_capture_not_axial_volume_candidate", result.reasons)

    def test_dose_report_is_rejected(self) -> None:
        result = classify_series(
            {
                "modality": "SR",
                "sop_class_name": "X-Ray Radiation Dose SR Document Storage",
                "image_type": [],
                "series_description": "Dose Report",
                "file_count": 1,
                "shape_consistent": True,
            }
        )

        self.assertEqual(result.status, REJECT)
        self.assertIn("dose_report_or_structured_report", result.reasons)

    def test_ct_with_missing_geometry_is_rejected(self) -> None:
        result = classify_series(
            {
                "modality": "CT",
                "sop_class_name": "CT Image Storage",
                "image_type": ["ORIGINAL", "PRIMARY", "AXIAL"],
                "series_description": "AXIAL",
                "file_count": 80,
                "shape_consistent": True,
                "has_pixel_spacing": True,
                "image_position_patient_count": 0,
                "image_orientation_patient_count": 80,
            }
        )

        self.assertEqual(result.status, REJECT)
        self.assertIn("missing_or_incomplete_image_position_patient", result.reasons)


if __name__ == "__main__":
    unittest.main()
