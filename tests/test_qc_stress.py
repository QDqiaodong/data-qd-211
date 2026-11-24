import os
import unittest

from ctd_bottle_match import process_with_qc

RUN_MAX_STRESS = os.getenv("CTD_RUN_STRESS") == "1"


class QCStressTests(unittest.TestCase):
    def build_scale_payload(self, cast_count, samples_per_cast, bottle_count):
        casts = []
        for cast_index in range(cast_count):
            samples = []
            for sample_index in range(samples_per_cast):
                samples.append({
                    "id": f"c{cast_index:03d}-s{sample_index:05d}",
                    "depth_m": str(sample_index),
                    "temperature_c": str(20 + sample_index % 15),
                    "salinity_psu": "35",
                })
            casts.append({"id": f"cast-{cast_index:03d}", "samples": samples})

        bottles = []
        # One bottle per cast lands exactly on the sample revised below.
        for cast_index in range(min(cast_count, bottle_count)):
            bottles.append({
                "id": f"b-exact-{cast_index:03d}",
                "cast_id": f"cast-{cast_index:03d}",
                "depth_m": str(samples_per_cast // 2),
            })
        for bottle_index in range(cast_count, bottle_count):
            cast_index = bottle_index % cast_count
            bottles.append({
                "id": f"b-{bottle_index:05d}",
                "cast_id": f"cast-{cast_index:03d}",
                "depth_m": str((bottle_index * 37) % max(1, samples_per_cast // 2 - 1)),
            })

        middle = samples_per_cast // 2
        qc_revisions = []
        for cast_index in range(cast_count):
            sid = f"c{cast_index:03d}-s{middle:05d}"
            qc_revisions.extend([
                {
                    "id": f"invalid-{cast_index}",
                    "order": 3 * cast_index + 1,
                    "cast_id": f"cast-{cast_index:03d}",
                    "sample_ids": [sid],
                    "state": "invalid",
                    "reason": "first spike",
                },
                {
                    "id": f"restore-{cast_index}",
                    "order": 3 * cast_index + 2,
                    "cast_id": f"cast-{cast_index:03d}",
                    "sample_ids": [sid],
                    "state": "valid",
                    "reason": "temporary restoration",
                },
                {
                    "id": f"final-invalid-{cast_index}",
                    "order": 3 * cast_index + 3,
                    "cast_id": f"cast-{cast_index:03d}",
                    "sample_ids": [sid],
                    "state": "invalid",
                    "reason": "final invalidation",
                },
            ])

        return {
            "max_gap_m": "2",
            "casts": casts,
            "bottles": bottles,
            "qc_revisions": qc_revisions,
        }

    def test_repeated_revisions_and_shuffled_order_at_moderate_scale(self):
        data = self.build_scale_payload(
            cast_count=20,
            samples_per_cast=1000,
            bottle_count=1000,
        )
        result = process_with_qc(data)
        shuffled = dict(data)
        shuffled["qc_revisions"] = list(reversed(data["qc_revisions"]))
        reversed_result = process_with_qc(shuffled)

        self.assertEqual(result, reversed_result)
        self.assertEqual(len(result["qc_samples"]), 20_000)
        self.assertEqual(len(result["bottles"]), 1000)
        self.assertEqual(sum(result["counts"]["initial"].values()), 1000)
        self.assertEqual(sum(result["counts"]["final"].values()), 1000)
        self.assertEqual(len(result["impact"]), 20)
        self.assertTrue(all(item["from_status"] == "matched" for item in result["impact"]))
        self.assertTrue(all(item["to_status"] == "matched" for item in result["impact"]))

    @unittest.skipUnless(
        RUN_MAX_STRESS,
        "set CTD_RUN_STRESS=1 to run the full 100 casts x 10,000 samples scenario",
    )
    def test_contract_maximum_scale(self):
        data = self.build_scale_payload(
            cast_count=100,
            samples_per_cast=10_000,
            bottle_count=5000,
        )
        result = process_with_qc(data)

        self.assertEqual(len(result["qc_samples"]), 1_000_000)
        self.assertEqual(len(result["bottles"]), 5000)
        self.assertEqual([row["id"] for row in result["bottles"]], sorted(row["id"] for row in result["bottles"]))
        self.assertEqual(sum(result["counts"]["initial"].values()), 5000)
        self.assertEqual(sum(result["counts"]["final"].values()), 5000)
        self.assertEqual(len(result["impact"]), 100)
        self.assertEqual(
            [item["bottle_id"] for item in result["impact"]],
            sorted(item["bottle_id"] for item in result["impact"]),
        )
        self.assertTrue(all(sample["state"] in {"valid", "invalid"} for sample in result["qc_samples"]))


if __name__ == "__main__":
    unittest.main()
