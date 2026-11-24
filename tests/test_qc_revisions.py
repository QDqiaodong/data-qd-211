import copy
import unittest
from unittest.mock import patch

from ctd_bottle_match import process, process_with_qc
from ctd_bottle_match import service


class QCMatchingTests(unittest.TestCase):
    def payload(self):
        return {
            "max_gap_m": "12",
            "casts": [
                {
                    "id": "c1",
                    "samples": [
                        {"id": "c1-s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                        {"id": "c1-s1", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
                        {"id": "c1-s2", "depth_m": "20", "temperature_c": "14", "salinity_psu": "36"},
                        {"id": "c1-s3", "depth_m": "30", "temperature_c": "10", "salinity_psu": "37"},
                    ],
                },
                {
                    "id": "c2",
                    "samples": [
                        {"id": "c2-s0", "depth_m": "0", "temperature_c": "4", "salinity_psu": "33"},
                        {"id": "c2-s1", "depth_m": "10", "temperature_c": "8", "salinity_psu": "34"},
                    ],
                },
            ],
            "bottles": [
                {"id": "b10", "cast_id": "c1", "depth_m": "10"},
                {"id": "b15", "cast_id": "c1", "depth_m": "15"},
                {"id": "b25", "cast_id": "c1", "depth_m": "25"},
                {"id": "bc2", "cast_id": "c2", "depth_m": "5"},
            ],
        }

    def revision(self, **changes):
        revision = {
            "id": "qc-1",
            "order": 1,
            "cast_id": "c1",
            "sample_ids": ["c1-s1"],
            "state": "invalid",
            "reason": "sensor spike",
        }
        revision.update(changes)
        return revision

    def row(self, result, bottle_id):
        return next(row for row in result["bottles"] if row["id"] == bottle_id)

    def test_no_revisions_preserves_process_rows_and_adds_empty_trace(self):
        data = self.payload()
        baseline = process(data)
        result = process_with_qc(data)
        baseline_by_id = {row["id"]: row for row in baseline["bottles"]}

        self.assertEqual([row["id"] for row in result["bottles"]], sorted(baseline_by_id))
        for row in result["bottles"]:
            expected_initial = baseline_by_id[row["id"]]
            self.assertEqual(row["initial"], expected_initial)
            expected_final = copy.deepcopy(expected_initial)
            expected_final["qc_excluded_sample_ids"] = []
            self.assertEqual(row["final"], expected_final)
            self.assertFalse(row["changed"])

        self.assertEqual(result["counts"]["initial"], baseline["counts"])
        self.assertEqual(result["counts"]["initial"], {
            "matched": 4,
            "outside_profile": 0,
            "gap_too_wide": 0,
        })
        self.assertEqual(result["counts"]["final"]["matched"], 4)
        self.assertEqual(result["counts"]["final"]["insufficient_valid_samples"], 0)
        self.assertEqual(result["impact"], [])
        self.assertEqual([item["sample_id"] for item in result["qc_samples"]], [
            "c1-s0", "c1-s1", "c1-s2", "c1-s3", "c2-s0", "c2-s1",
        ])
        self.assertTrue(all(item["state"] == "valid" for item in result["qc_samples"]))
        self.assertTrue(all(item["last_revision_id"] is None for item in result["qc_samples"]))
        self.assertTrue(all(item["reason"] is None for item in result["qc_samples"]))
        self.assertEqual(process_with_qc({**data, "qc_revisions": []}), result)

    def test_process_remains_compatible_with_qc_key(self):
        without_qc = self.payload()
        with_qc = copy.deepcopy(without_qc)
        with_qc["qc_revisions"] = [self.revision()]
        self.assertEqual(process(with_qc), process(without_qc))

    def test_revival_uses_largest_order_and_preserves_last_evidence(self):
        first = self.revision(id="qc-invalid", order=1, state="invalid", reason="spike")
        second = self.revision(id="qc-restore", order=2, state="valid", reason="review restored")

        ordered = self.payload()
        ordered["qc_revisions"] = [first, second]
        reversed_input = copy.deepcopy(ordered)
        reversed_input["qc_revisions"] = [second, first]

        result = process_with_qc(ordered)
        reversed_result = process_with_qc(reversed_input)
        self.assertEqual(result, reversed_result)

        sample = next(item for item in result["qc_samples"] if item["sample_id"] == "c1-s1")
        self.assertEqual(sample["state"], "valid")
        self.assertEqual(sample["last_revision_id"], "qc-restore")
        self.assertEqual(sample["reason"], "review restored")
        self.assertFalse(self.row(result, "b10")["changed"])
        self.assertEqual(result["impact"], [])

    def test_invalid_sample_at_bottle_depth_can_change_values_but_not_status(self):
        data = self.payload()
        data["max_gap_m"] = "20"
        data["bottles"] = [{"id": "b10", "cast_id": "c1", "depth_m": "10"}]
        data["qc_revisions"] = [self.revision()]

        result = process_with_qc(data)
        row = result["bottles"][0]
        self.assertEqual(row["initial"]["status"], "matched")
        self.assertEqual(row["initial"]["source_sample_ids"], ["c1-s1"])
        self.assertEqual(row["final"]["status"], "matched")
        self.assertEqual(row["final"]["temperature_c"], "17")
        self.assertEqual(row["final"]["salinity_psu"], "35")
        self.assertEqual(row["final"]["source_sample_ids"], ["c1-s0", "c1-s2"])
        self.assertEqual(row["final"]["gap_m"], "20")
        self.assertEqual(row["final"]["qc_excluded_sample_ids"], ["c1-s1"])
        self.assertTrue(row["changed"])
        self.assertEqual(result["impact"], [{
            "bottle_id": "b10",
            "from_status": "matched",
            "to_status": "matched",
        }])

    def test_invalid_sample_between_endpoints_is_excluded_from_exact_interpolation(self):
        data = self.payload()
        data["max_gap_m"] = "20"
        data["bottles"] = [{"id": "b15", "cast_id": "c1", "depth_m": "15"}]
        data["qc_revisions"] = [self.revision()]

        row = process_with_qc(data)["bottles"][0]
        self.assertEqual(row["initial"]["source_sample_ids"], ["c1-s1", "c1-s2"])
        self.assertEqual(row["final"]["status"], "matched")
        self.assertEqual(row["final"]["temperature_c"], "31/2")
        self.assertEqual(row["final"]["salinity_psu"], "71/2")
        self.assertEqual(row["final"]["source_sample_ids"], ["c1-s0", "c1-s2"])
        self.assertEqual(row["final"]["gap_m"], "20")
        self.assertEqual(row["final"]["qc_excluded_sample_ids"], ["c1-s1"])
        self.assertTrue(row["changed"])

    def test_exclusion_can_make_valid_endpoint_gap_too_wide(self):
        data = self.payload()
        data["bottles"] = [{"id": "b15", "cast_id": "c1", "depth_m": "15"}]
        data["qc_revisions"] = [self.revision()]

        result = process_with_qc(data)
        final = result["bottles"][0]["final"]
        self.assertEqual(final["status"], "gap_too_wide")
        self.assertEqual(final["source_sample_ids"], ["c1-s0", "c1-s2"])
        self.assertEqual(final["gap_m"], "20")
        self.assertEqual(final["qc_excluded_sample_ids"], ["c1-s1"])
        self.assertNotIn("temperature_c", final)
        self.assertNotIn("salinity_psu", final)
        self.assertEqual(result["impact"], [{
            "bottle_id": "b15",
            "from_status": "matched",
            "to_status": "gap_too_wide",
        }])

    def test_fewer_than_two_valid_samples_is_uniform_for_cast_and_isolated(self):
        data = self.payload()
        data["qc_revisions"] = [self.revision(
            id="qc-delete-three",
            order=7,
            sample_ids=["c1-s0", "c1-s1", "c1-s2"],
        )]

        result = process_with_qc(data)
        for bottle_id in ("b10", "b15", "b25"):
            final = self.row(result, bottle_id)["final"]
            self.assertEqual(final["status"], "insufficient_valid_samples")
            self.assertEqual(final["qc_excluded_sample_ids"], [])
            self.assertNotIn("temperature_c", final)
            self.assertNotIn("salinity_psu", final)
            self.assertNotIn("source_sample_ids", final)
            self.assertTrue(self.row(result, bottle_id)["changed"])

        c2_row = self.row(result, "bc2")
        self.assertEqual(c2_row["final"]["status"], "matched")
        self.assertFalse(c2_row["changed"])
        self.assertEqual([item["bottle_id"] for item in result["impact"]], ["b10", "b15", "b25"])
        self.assertEqual(result["counts"]["final"]["insufficient_valid_samples"], 3)
        self.assertEqual(result["counts"]["final"]["matched"], 1)

    def test_unrelated_invalid_sample_does_not_change_bottle(self):
        data = self.payload()
        data["qc_revisions"] = [self.revision(
            id="qc-last",
            sample_ids=["c1-s3"],
            reason="last sensor failed",
        )]

        result = process_with_qc(data)
        self.assertFalse(self.row(result, "b10")["changed"])
        self.assertFalse(self.row(result, "b15")["changed"])
        b25 = self.row(result, "b25")
        self.assertTrue(b25["changed"])
        self.assertEqual(b25["final"]["status"], "outside_profile")
        self.assertEqual(b25["final"]["qc_excluded_sample_ids"], [])
        self.assertEqual([item["bottle_id"] for item in result["impact"]], ["b25"])

    def test_outside_after_excluding_first_sample_has_empty_excluded_trace(self):
        data = self.payload()
        data["bottles"] = [{"id": "b0", "cast_id": "c1", "depth_m": "0"}]
        data["qc_revisions"] = [self.revision(sample_ids=["c1-s0"])]

        row = process_with_qc(data)["bottles"][0]
        self.assertEqual(row["initial"]["status"], "matched")
        self.assertEqual(row["final"]["status"], "outside_profile")
        self.assertEqual(row["final"]["qc_excluded_sample_ids"], [])
        self.assertTrue(row["changed"])

    def test_exact_valid_hit_ignores_invalid_samples_outside_point_interval(self):
        data = self.payload()
        data["bottles"] = [{"id": "b20", "cast_id": "c1", "depth_m": "20"}]
        data["qc_revisions"] = [self.revision()]  # invalidates c1-s1

        row = process_with_qc(data)["bottles"][0]
        self.assertEqual(row["initial"]["source_sample_ids"], ["c1-s2"])
        self.assertEqual(row["final"]["source_sample_ids"], ["c1-s2"])
        self.assertEqual(row["final"]["qc_excluded_sample_ids"], [])
        self.assertFalse(row["changed"])

    def test_multiple_excluded_samples_returned_in_original_depth_order(self):
        data = self.payload()
        data["max_gap_m"] = "30"
        data["bottles"] = [{"id": "b15", "cast_id": "c1", "depth_m": "15"}]
        data["qc_revisions"] = [self.revision(
            sample_ids=["c1-s2", "c1-s1"],
        )]

        final = process_with_qc(data)["bottles"][0]["final"]
        self.assertEqual(final["source_sample_ids"], ["c1-s0", "c1-s3"])
        self.assertEqual(final["qc_excluded_sample_ids"], ["c1-s1", "c1-s2"])

    def test_invalid_revisions_are_rejected_before_matching(self):
        duplicate_id = [self.revision(id="same", order=1), self.revision(id="same", order=2)]
        duplicate_order = [self.revision(id="a", order=4), self.revision(id="b", order=4)]
        bad_revisions = [
            {},
            ["not-an-object"],
            [self.revision(id="")],
            [self.revision(id=7)],
            duplicate_id,
            [self.revision(order=0)],
            [self.revision(order=-2)],
            [self.revision(order=True)],
            [self.revision(order=1.0)],
            [self.revision(order="1")],
            duplicate_order,
            [self.revision(cast_id="missing")],
            [self.revision(sample_ids=None)],
            [self.revision(sample_ids=[])],
            [self.revision(sample_ids="c1-s1")],
            [self.revision(sample_ids=["c1-s1", "c1-s1"])],
            [self.revision(sample_ids=["missing-sample"])],
            [self.revision(sample_ids=["c2-s0"])],
            [self.revision(state="deleted")],
            [self.revision(reason="")],
            [self.revision(reason=3)],
        ]

        for bad in bad_revisions:
            data = self.payload()
            data["qc_revisions"] = bad
            with self.subTest(qc_revisions=bad):
                with patch.object(service, "_match_profile") as initial_match, \
                        patch.object(service, "_match_final_profile") as final_match:
                    with self.assertRaises(ValueError):
                        process_with_qc(data)
                    initial_match.assert_not_called()
                    final_match.assert_not_called()

    def test_input_is_not_mutated_and_repeated_calls_are_stable(self):
        data = self.payload()
        data["qc_revisions"] = [
            self.revision(id="a", order=2, state="valid", reason="restore"),
            self.revision(id="b", order=1, sample_ids=["c1-s2"], reason="spike"),
        ]
        snapshot = copy.deepcopy(data)

        first = process_with_qc(data)
        second = process_with_qc(data)
        self.assertEqual(first, second)
        self.assertEqual(data, snapshot)

    def test_output_contains_no_float_values(self):
        result = process_with_qc(self.payload())

        def visit(value):
            self.assertNotIsInstance(value, float)
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(result)

    def test_output_order_is_stable_under_input_shuffling(self):
        data = self.payload()
        data["bottles"] = list(reversed(data["bottles"]))
        data["qc_revisions"] = [
            self.revision(id="r2", order=2, sample_ids=["c1-s2"], reason="two"),
            self.revision(id="r1", order=1, reason="one"),
        ]
        result = process_with_qc(data)
        self.assertEqual([row["id"] for row in result["bottles"]], sorted(row["id"] for row in result["bottles"]))
        self.assertEqual([item["bottle_id"] for item in result["impact"]], sorted(item["bottle_id"] for item in result["impact"]))
        self.assertEqual([item["sample_id"] for item in result["qc_samples"]], [
            "c1-s0", "c1-s1", "c1-s2", "c1-s3", "c2-s0", "c2-s1",
        ])


if __name__ == "__main__":
    unittest.main()
