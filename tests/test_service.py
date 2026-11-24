import unittest

from ctd_bottle_match import process


class BottleMatchTests(unittest.TestCase):
    def base(self):
        return {
            "max_gap_m": "12",
            "casts": [{"id": "c1", "samples": [
                {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "s1", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
                {"id": "s2", "depth_m": "30", "temperature_c": "14", "salinity_psu": "36"},
            ]}],
            "bottles": [{"id": "b1", "cast_id": "c1", "depth_m": "5"}],
        }

    def test_interpolates_exactly(self):
        row = process(self.base())["bottles"][0]
        self.assertEqual((row["temperature_c"], row["salinity_psu"]), ("19", "69/2"))

    def test_exact_sample_uses_one_source(self):
        data = self.base(); data["bottles"][0]["depth_m"] = "10"
        self.assertEqual(process(data)["bottles"][0]["source_sample_ids"], ["s1"])

    def test_outside_is_reported(self):
        data = self.base(); data["bottles"][0]["depth_m"] = "31"
        self.assertEqual(process(data)["bottles"][0]["status"], "outside_profile")

    def test_gap_limit_is_business_status(self):
        data = self.base(); data["bottles"][0]["depth_m"] = "20"
        self.assertEqual(process(data)["bottles"][0]["status"], "gap_too_wide")

    def test_depths_must_increase(self):
        data = self.base(); data["casts"][0]["samples"][1]["depth_m"] = "0"
        with self.assertRaises(ValueError): process(data)

    def test_unknown_cast_fails(self):
        data = self.base(); data["bottles"][0]["cast_id"] = "missing"
        with self.assertRaises(ValueError): process(data)


if __name__ == "__main__":
    unittest.main()

