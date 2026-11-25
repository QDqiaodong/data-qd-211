import copy
import unittest

from ctd_bottle_match import process_with_segments
from ctd_bottle_match import service


class SegmentMatchingTests(unittest.TestCase):
    def payload(self, **changes):
        data = {
            "max_gap_m": "12",
            "segments": [
                {
                    "id": "seg-a-1",
                    "cast_id": "cast-a",
                    "sequence": 1,
                    "samples": [
                        {"id": "ctd-0", "depth_m": "0", "temperature_c": "21", "salinity_psu": "34"},
                        {"id": "ctd-10-old", "depth_m": "10", "temperature_c": "19", "salinity_psu": "35"},
                    ],
                },
                {
                    "id": "seg-a-2",
                    "cast_id": "cast-a",
                    "sequence": 2,
                    "samples": [
                        {"id": "ctd-10-new", "depth_m": "10", "temperature_c": "20", "salinity_psu": "35"},
                        {"id": "ctd-20", "depth_m": "20", "temperature_c": "18", "salinity_psu": "36"},
                    ],
                },
            ],
            "bottles": [
                {"id": "niskin-1", "cast_id": "cast-a", "depth_m": "15"},
            ],
        }
        data.update(changes)
        return data

    def row(self, result, bottle_id):
        return next(row for row in result["bottles"] if row["id"] == bottle_id)

    def profile(self, result, cast_id="cast-a"):
        return next(item for item in result["canonical_profiles"] if item["cast_id"] == cast_id)

    # ------------------------------------------------------------------ core

    def test_overlap_keeps_largest_sequence_with_full_audit(self):
        result = process_with_segments(self.payload())
        profile = self.profile(result)

        self.assertEqual(profile["segment_ids"], ["seg-a-1", "seg-a-2"])
        self.assertEqual(
            [(sample["id"], sample["segment_id"]) for sample in profile["samples"]],
            [("ctd-0", "seg-a-1"), ("ctd-10-new", "seg-a-2"), ("ctd-20", "seg-a-2")],
        )
        self.assertEqual(profile["overlap_resolutions"], [{
            "depth_m": "10",
            "selected_sample_id": "ctd-10-new",
            "selected_segment_id": "seg-a-2",
            "discarded_sample_ids": ["ctd-10-old"],
        }])
        # Exact input strings are preserved rather than re-serialized.
        self.assertEqual(profile["samples"][1]["depth_m"], "10")

    def test_interpolation_uses_winning_segment_values_and_trace(self):
        result = process_with_segments(self.payload())
        row = self.row(result, "niskin-1")
        # 15 m is between ctd-10-new (20 C at 10 m) and ctd-20 (18 C at 20 m).
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["temperature_c"], "19")
        self.assertEqual(row["salinity_psu"], "71/2")
        self.assertEqual(row["source_sample_ids"], ["ctd-10-new", "ctd-20"])
        self.assertEqual(row["source_segment_ids"], ["seg-a-2"])
        self.assertEqual(row["gap_m"], "10")
        self.assertEqual(result["counts"], {
            "matched": 1,
            "outside_profile": 0,
            "gap_too_wide": 0,
        })

    def test_exact_hit_on_overlap_depth_cites_only_selected_sample(self):
        data = self.payload()
        data["bottles"] = [{"id": "b10", "cast_id": "cast-a", "depth_m": "10"}]
        row = process_with_segments(data)["bottles"][0]
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["source_sample_ids"], ["ctd-10-new"])
        self.assertEqual(row["source_segment_ids"], ["seg-a-2"])
        self.assertEqual(row["gap_m"], "0")
        self.assertEqual(row["temperature_c"], "20")

    def test_replaced_sample_would_have_changed_interpolation(self):
        # With the old 10 m sample (19 C) the 15 m temperature would be 37/2;
        # the newer authoritative segment must instead yield 19 C.
        data = self.payload()
        data["segments"][1]["samples"][0]["temperature_c"] = "20"
        row = process_with_segments(data)["bottles"][0]
        self.assertEqual(row["temperature_c"], "19")
        self.assertNotIn("ctd-10-old", row["source_sample_ids"])

    def test_adjacent_segments_without_overlap_have_empty_audit(self):
        data = {
            "max_gap_m": "12",
            "segments": [
                {
                    "id": "s1", "cast_id": "c", "sequence": 1,
                    "samples": [
                        {"id": "a0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                        {"id": "a10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
                    ],
                },
                {
                    "id": "s2", "cast_id": "c", "sequence": 2,
                    "samples": [
                        {"id": "b20", "depth_m": "20", "temperature_c": "16", "salinity_psu": "36"},
                        {"id": "b30", "depth_m": "30", "temperature_c": "14", "salinity_psu": "37"},
                    ],
                },
            ],
            "bottles": [{"id": "bot", "cast_id": "c", "depth_m": "5"}],
        }
        result = process_with_segments(data)
        profile = self.profile(result, "c")
        self.assertEqual([s["id"] for s in profile["samples"]], ["a0", "a10", "b20", "b30"])
        self.assertEqual(profile["segment_ids"], ["s1", "s2"])
        self.assertEqual(profile["overlap_resolutions"], [])
        row = result["bottles"][0]
        self.assertEqual(row["source_sample_ids"], ["a0", "a10"])
        self.assertEqual(row["source_segment_ids"], ["s1"])

    def test_three_way_competition_lists_all_losers_in_sequence_order(self):
        data = {
            "max_gap_m": "12",
            "segments": [
                {
                    "id": "seg-3", "cast_id": "c", "sequence": 3,
                    "samples": [
                        {"id": "win-0", "depth_m": "0", "temperature_c": "1", "salinity_psu": "1"},
                        {"id": "win-10", "depth_m": "10", "temperature_c": "3", "salinity_psu": "3"},
                    ],
                },
                {
                    "id": "seg-1", "cast_id": "c", "sequence": 1,
                    "samples": [
                        {"id": "old1-0", "depth_m": "0", "temperature_c": "9", "salinity_psu": "9"},
                        {"id": "old1-10", "depth_m": "10", "temperature_c": "9", "salinity_psu": "9"},
                    ],
                },
                {
                    "id": "seg-2", "cast_id": "c", "sequence": 2,
                    "samples": [
                        {"id": "old2-0", "depth_m": "0", "temperature_c": "8", "salinity_psu": "8"},
                        {"id": "old2-10", "depth_m": "10", "temperature_c": "8", "salinity_psu": "8"},
                    ],
                },
            ],
            "bottles": [{"id": "bot", "cast_id": "c", "depth_m": "10"}],
        }
        result = process_with_segments(data)
        profile = self.profile(result, "c")
        self.assertEqual(profile["segment_ids"], ["seg-1", "seg-2", "seg-3"])
        self.assertEqual([s["id"] for s in profile["samples"]], ["win-0", "win-10"])
        self.assertEqual([r["depth_m"] for r in profile["overlap_resolutions"]], ["0", "10"])
        at_ten = next(r for r in profile["overlap_resolutions"] if r["depth_m"] == "10")
        self.assertEqual(at_ten["selected_sample_id"], "win-10")
        self.assertEqual(at_ten["selected_segment_id"], "seg-3")
        self.assertEqual(at_ten["discarded_sample_ids"], ["old1-10", "old2-10"])
        row = result["bottles"][0]
        self.assertEqual(row["source_sample_ids"], ["win-10"])
        self.assertEqual(row["source_segment_ids"], ["seg-3"])

    def test_empty_range_between_segments_is_a_real_gap(self):
        data = {
            "max_gap_m": "12",
            "segments": [
                {
                    "id": "s1", "cast_id": "c", "sequence": 1,
                    "samples": [
                        {"id": "a0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                        {"id": "a10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
                    ],
                },
                {
                    "id": "s2", "cast_id": "c", "sequence": 2,
                    "samples": [
                        {"id": "b30", "depth_m": "30", "temperature_c": "14", "salinity_psu": "37"},
                        {"id": "b40", "depth_m": "40", "temperature_c": "12", "salinity_psu": "38"},
                    ],
                },
            ],
            "bottles": [{"id": "bot", "cast_id": "c", "depth_m": "20"}],
        }
        row = process_with_segments(data)["bottles"][0]
        self.assertEqual(row["status"], "gap_too_wide")
        self.assertEqual(row["source_sample_ids"], ["a10", "b30"])
        self.assertEqual(row["source_segment_ids"], ["s1", "s2"])
        self.assertEqual(row["gap_m"], "20")
        self.assertNotIn("temperature_c", row)
        self.assertNotIn("salinity_psu", row)

    def test_outside_profile_has_empty_segment_sources(self):
        data = self.payload()
        data["bottles"] = [
            {"id": "above", "cast_id": "cast-a", "depth_m": "21"},
            {"id": "below", "cast_id": "cast-a", "depth_m": "100"},
        ]
        result = process_with_segments(data)
        for bottle_id in ("above", "below"):
            row = self.row(result, bottle_id)
            self.assertEqual(row["status"], "outside_profile")
            self.assertEqual(row["source_segment_ids"], [])
            self.assertNotIn("source_sample_ids", row)
        self.assertEqual(result["counts"]["outside_profile"], 2)

    def test_cross_cast_isolation_and_sorting(self):
        data = {
            "max_gap_m": "12",
            "segments": [
                {
                    "id": "zb", "cast_id": "cast-b", "sequence": 1,
                    "samples": [
                        {"id": "b0", "depth_m": "0", "temperature_c": "4", "salinity_psu": "33"},
                        {"id": "b10", "depth_m": "10", "temperature_c": "5", "salinity_psu": "33"},
                    ],
                },
                {
                    "id": "za", "cast_id": "cast-a", "sequence": 1,
                    "samples": [
                        {"id": "a0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                        {"id": "a10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
                    ],
                },
            ],
            "bottles": [
                {"id": "bot-b", "cast_id": "cast-b", "depth_m": "5"},
                {"id": "bot-a", "cast_id": "cast-a", "depth_m": "5"},
            ],
        }
        result = process_with_segments(data)
        self.assertEqual([p["cast_id"] for p in result["canonical_profiles"]], ["cast-a", "cast-b"])
        self.assertEqual([r["id"] for r in result["bottles"]], ["bot-a", "bot-b"])
        self.assertEqual(self.row(result, "bot-a")["source_segment_ids"], ["za"])
        self.assertEqual(self.row(result, "bot-b")["source_segment_ids"], ["zb"])

    def test_segment_array_position_does_not_override_sequence(self):
        data = self.payload()
        data["segments"] = list(reversed(data["segments"]))
        shuffled = process_with_segments(data)
        normal = process_with_segments(self.payload())
        self.assertEqual(shuffled, normal)

    def test_exact_numeric_text_is_preserved_in_canonical_samples(self):
        data = self.payload()
        data["segments"][0]["samples"][0]["depth_m"] = "0"
        data["segments"][0]["samples"][0]["temperature_c"] = "41/2"
        data["segments"][0]["samples"][0]["salinity_psu"] = "69/2"
        result = process_with_segments(data)
        sample = self.profile(result)["samples"][0]
        self.assertEqual(sample["temperature_c"], "41/2")
        self.assertEqual(sample["salinity_psu"], "69/2")

    # ------------------------------------------------------------- validation

    def test_invalid_inputs_are_rejected_before_any_matching(self):
        bad_payloads = []

        dup_segment = self.payload()
        dup_segment["segments"][1]["id"] = "seg-a-1"
        bad_payloads.append(("duplicate segment id", dup_segment))

        dup_sample = self.payload()
        dup_sample["segments"][1]["samples"][0]["id"] = "ctd-0"
        bad_payloads.append(("duplicate sample id", dup_sample))

        dup_sequence = self.payload()
        dup_sequence["segments"][1]["sequence"] = 1
        bad_payloads.append(("duplicate sequence", dup_sequence))

        disorder = self.payload()
        disorder["segments"][0]["samples"][0]["depth_m"], disorder["segments"][0]["samples"][1]["depth_m"] = "10", "0"
        bad_payloads.append(("depth disorder", disorder))

        unknown_cast = self.payload()
        unknown_cast["bottles"][0]["cast_id"] = "missing"
        bad_payloads.append(("unknown bottle cast", unknown_cast))

        bad_number = self.payload()
        bad_number["segments"][0]["samples"][0]["depth_m"] = "abc"
        bad_payloads.append(("invalid number", bad_number))

        negative = self.payload()
        negative["segments"][0]["samples"][0]["temperature_c"] = "-1"
        bad_payloads.append(("negative number", negative))

        max_gap_bad = self.payload()
        max_gap_bad["max_gap_m"] = "1.5.2"
        bad_payloads.append(("invalid max gap", max_gap_bad))

        for sequence_value in (0, -3, 1.5, "2", True):
            wrong_sequence = self.payload()
            wrong_sequence["segments"][0]["sequence"] = sequence_value
            bad_payloads.append((f"sequence {sequence_value!r}", wrong_sequence))

        empty_segments = self.payload()
        empty_segments["segments"] = []
        bad_payloads.append(("empty segments", empty_segments))

        too_many_segments = self.payload()
        too_many_segments["segments"] = [
            {"id": f"x{i}", "cast_id": "cast-a", "sequence": i + 100,
             "samples": [
                 {"id": f"x{i}-0", "depth_m": "0", "temperature_c": "1", "salinity_psu": "1"},
                 {"id": f"x{i}-1", "depth_m": "1", "temperature_c": "1", "salinity_psu": "1"},
             ]}
            for i in range(1001)
        ]
        bad_payloads.append(("too many segments", too_many_segments))

        one_sample = self.payload()
        one_sample["segments"][0]["samples"] = one_sample["segments"][0]["samples"][:1]
        bad_payloads.append(("segment with one sample", one_sample))

        too_many_cast_samples = self.payload()
        too_many_cast_samples["segments"] = [
            {"id": "big-1", "cast_id": "cast-a", "sequence": 9,
             "samples": [
                 {"id": f"big1-{i}", "depth_m": str(i), "temperature_c": "1", "salinity_psu": "1"}
                 for i in range(9999)
             ]},
            {"id": "big-2", "cast_id": "cast-a", "sequence": 10,
             "samples": [
                 {"id": "big2-0", "depth_m": "0", "temperature_c": "1", "salinity_psu": "1"},
                 {"id": "big2-1", "depth_m": "1", "temperature_c": "1", "salinity_psu": "1"},
             ]},
        ]
        bad_payloads.append(("cast over 10000 total samples", too_many_cast_samples))

        empty_bottles = self.payload()
        empty_bottles["bottles"] = []
        bad_payloads.append(("empty bottles", empty_bottles))

        dup_bottle = self.payload()
        dup_bottle["bottles"].append({"id": "niskin-1", "cast_id": "cast-a", "depth_m": "5"})
        bad_payloads.append(("duplicate bottle id", dup_bottle))

        blank_id = self.payload()
        blank_id["segments"][0]["id"] = ""
        bad_payloads.append(("blank segment id", blank_id))

        non_object = self.payload()
        non_object["segments"][0] = "not-an-object"
        bad_payloads.append(("non-object segment", non_object))

        too_many_casts = self.payload()
        too_many_casts["segments"] = [
            {"id": f"seg-{i}", "cast_id": f"cast-{i}", "sequence": 1,
             "samples": [
                 {"id": f"s-{i}-0", "depth_m": "0", "temperature_c": "1", "salinity_psu": "1"},
                 {"id": f"s-{i}-1", "depth_m": "1", "temperature_c": "1", "salinity_psu": "1"},
             ]}
            for i in range(101)
        ]
        bad_payloads.append(("more than 100 casts", too_many_casts))

        for label, bad in bad_payloads:
            with self.subTest(label=label):
                builder_called = False
                matcher_called = False
                original_builder = service._build_canonical_profile
                original_matcher = service._match_profile

                def fail_builder(*args, **kwargs):
                    nonlocal builder_called
                    builder_called = True
                    return original_builder(*args, **kwargs)

                def fail_matcher(*args, **kwargs):
                    nonlocal matcher_called
                    matcher_called = True
                    return original_matcher(*args, **kwargs)

                service._build_canonical_profile = fail_builder
                service._match_profile = fail_matcher
                try:
                    with self.assertRaises(ValueError):
                        process_with_segments(bad)
                finally:
                    service._build_canonical_profile = original_builder
                    service._match_profile = original_matcher
                self.assertFalse(builder_called, "canonical profile built before validation completed")
                self.assertFalse(matcher_called, "bottle matching ran before validation completed")

    def test_input_is_not_mutated_and_repeated_calls_are_stable(self):
        data = self.payload()
        snapshot = copy.deepcopy(data)
        first = process_with_segments(data)
        second = process_with_segments(data)
        self.assertEqual(first, second)
        self.assertEqual(data, snapshot)

    def test_output_contains_no_float_values(self):
        result = process_with_segments(self.payload())

        def visit(value):
            self.assertNotIsInstance(value, float)
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(result)

    def test_every_cast_gets_a_profile_even_without_overlap(self):
        data = self.payload()
        data["segments"].append({
            "id": "seg-b-1",
            "cast_id": "cast-b",
            "sequence": 1,
            "samples": [
                {"id": "b0", "depth_m": "0", "temperature_c": "9", "salinity_psu": "32"},
                {"id": "b10", "depth_m": "10", "temperature_c": "8", "salinity_psu": "32"},
            ],
        })
        data["bottles"].append({"id": "niskin-b", "cast_id": "cast-b", "depth_m": "10"})
        result = process_with_segments(data)
        profiles = {p["cast_id"]: p for p in result["canonical_profiles"]}
        self.assertEqual(set(profiles), {"cast-a", "cast-b"})
        self.assertEqual(profiles["cast-b"]["overlap_resolutions"], [])
        self.assertEqual(sum(result["counts"].values()), 2)

    def test_source_segment_from_older_segment_is_kept_when_not_overlapped(self):
        data = self.payload()
        data["bottles"] = [{"id": "b0", "cast_id": "cast-a", "depth_m": "0"}]
        row = process_with_segments(data)["bottles"][0]
        self.assertEqual(row["source_sample_ids"], ["ctd-0"])
        self.assertEqual(row["source_segment_ids"], ["seg-a-1"])


if __name__ == "__main__":
    unittest.main()
