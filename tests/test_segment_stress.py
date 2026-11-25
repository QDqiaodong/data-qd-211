import os
import time
import unittest

from ctd_bottle_match import process_with_segments

RUN_MAX_STRESS = os.getenv("CTD_RUN_STRESS") == "1"


class SegmentStressTests(unittest.TestCase):
    def build_scale_payload(self, cast_count, segments_per_cast, samples_per_segment, bottle_count):
        segments = []
        # Each later segment starts at its predecessor's last depth, so
        # adjacent segments share exactly one boundary depth.
        shift = samples_per_segment - 1
        canonical_depth_count = 1 + segments_per_cast * shift
        for cast_index in range(cast_count):
            cast_id = f"cast-{cast_index:03d}"
            for segment_index in range(segments_per_cast):
                samples = []
                start = segment_index * shift
                for sample_index in range(samples_per_segment):
                    depth = start + sample_index
                    samples.append({
                        "id": f"{cast_id}-seg{segment_index:02d}-s{sample_index:05d}",
                        "depth_m": str(depth),
                        "temperature_c": str(20 + (depth % 11)),
                        "salinity_psu": str(34 + (depth % 5)),
                    })
                segments.append({
                    "id": f"{cast_id}-seg{segment_index:02d}",
                    "cast_id": cast_id,
                    "sequence": segment_index + 1,
                    "samples": samples,
                })
        bottles = []
        seen_ids = set()
        bottle_index = 0
        while len(bottles) < bottle_count:
            cast_index = bottle_index % cast_count
            depth = (bottle_index * 37) % canonical_depth_count
            bottle_id = f"b-{bottle_index:05d}"
            if bottle_id not in seen_ids:
                seen_ids.add(bottle_id)
                bottles.append({
                    "id": bottle_id,
                    "cast_id": f"cast-{cast_index:03d}",
                    "depth_m": str(depth),
                })
            bottle_index += 1

        return {
            "max_gap_m": "3",
            "segments": segments,
            "bottles": bottles,
        }

    def test_moderate_scale_with_overlap_audit_and_shuffled_segments(self):
        data = self.build_scale_payload(
            cast_count=20,
            segments_per_cast=5,
            samples_per_segment=100,
            bottle_count=2000,
        )
        result = process_with_segments(data)

        self.assertEqual(len(result["canonical_profiles"]), 20)
        # 5 segments of 100 samples, each shifted by 99 and sharing one
        # boundary depth: union of depths 0..495 -> 496 canonical depths.
        for profile in result["canonical_profiles"]:
            self.assertEqual(len(profile["samples"]), 496)
            self.assertEqual(len(profile["segment_ids"]), 5)
            self.assertEqual(len(profile["overlap_resolutions"]), 4)
            depths = [sample["depth_m"] for sample in profile["samples"]]
            self.assertEqual(depths, sorted(depths, key=int))
        self.assertEqual(len(result["bottles"]), 2000)
        self.assertEqual(sum(result["counts"].values()), 2000)
        self.assertEqual(
            [row["id"] for row in result["bottles"]],
            sorted(row["id"] for row in result["bottles"]),
        )

        shuffled = dict(data)
        shuffled["segments"] = list(reversed(data["segments"]))
        self.assertEqual(process_with_segments(shuffled), result)

    @unittest.skipUnless(
        RUN_MAX_STRESS,
        "set CTD_RUN_STRESS=1 to run the full 100 casts x 10 segments x 1000 samples scenario",
    )
    def test_contract_maximum_scale(self):
        # 100 casts x 10 segments x 1000 samples = 100,000 samples, within the
        # 1000-segment and 10,000-samples-per-cast contract ceilings.
        data = self.build_scale_payload(
            cast_count=100,
            segments_per_cast=10,
            samples_per_segment=1000,
            bottle_count=5000,
        )
        started = time.perf_counter()
        result = process_with_segments(data)
        elapsed = time.perf_counter() - started

        self.assertEqual(len(result["canonical_profiles"]), 100)
        for profile in result["canonical_profiles"]:
            # 10 segments of 1000 samples shifted by 999: depths 0..9990.
            self.assertEqual(len(profile["samples"]), 9991)
            self.assertEqual(len(profile["overlap_resolutions"]), 9)
        self.assertEqual(sum(result["counts"].values()), 5000)
        self.assertLess(elapsed, 30)


if __name__ == "__main__":
    unittest.main()
