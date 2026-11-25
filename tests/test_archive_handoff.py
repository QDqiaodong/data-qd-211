import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctd_bottle_match import ArchiveHandoff, process

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def acceptance_data():
    return {
        "max_gap_m": "12",
        "casts": [{"id": "c", "samples": [
            {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
            {"id": "s10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            {"id": "s20", "depth_m": "20", "temperature_c": "14", "salinity_psu": "36"},
            {"id": "s40", "depth_m": "40", "temperature_c": "10", "salinity_psu": "37"},
        ]}],
        "bottles": [
            {"id": "a", "cast_id": "c", "depth_m": "5"},
            {"id": "b", "cast_id": "c", "depth_m": "10"},
            {"id": "c", "cast_id": "c", "depth_m": "30"},
            {"id": "d", "cast_id": "c", "depth_m": "50"},
        ],
    }


def item_keys(items):
    return [(item["kind"], item["id"]) for item in items]


def ident(kind, record_id):
    return {"kind": kind, "id": record_id}


class AcceptanceScenarioTests(unittest.TestCase):
    """The executable acceptance sequence from the handoff contract."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = str(Path(self.temp.name) / "ledger.json")

    def open_handoff(self):
        return ArchiveHandoff(self.store)

    def test_full_acceptance_sequence_with_reopen_and_subprocess(self):
        handoff = self.open_handoff()
        registration = handoff.register("D", acceptance_data())
        self.assertEqual(registration, {
            "dataset_id": "D", "sample_count": 4, "bottle_count": 4,
        })

        # Frozen bottle payloads match process(data) exactly, including the
        # unsuccessful states; c keeps two endpoints and no temperature.
        baseline = {row["id"]: row for row in process(acceptance_data())["bottles"]}
        self.assertEqual(baseline["a"]["status"], "matched")
        self.assertEqual(baseline["a"]["source_sample_ids"], ["s0", "s10"])
        self.assertEqual(baseline["b"]["source_sample_ids"], ["s10"])
        self.assertEqual(baseline["c"]["status"], "gap_too_wide")
        self.assertEqual(baseline["c"]["source_sample_ids"], ["s20", "s40"])
        self.assertNotIn("temperature_c", baseline["c"])
        self.assertNotIn("salinity_psu", baseline["c"])
        self.assertEqual(baseline["d"]["status"], "outside_profile")
        self.assertNotIn("source_sample_ids", baseline["d"])

        # --- Step 1 -----------------------------------------------------
        r1 = handoff.next_batch("D", "r1", 2)
        self.assertEqual(item_keys(r1["items"]), [("sample", "s0"), ("sample", "s10")])
        # A second request cannot reach bottles while their sources are open.
        r2 = handoff.next_batch("D", "r2", 1)
        self.assertEqual(item_keys(r2["items"]), [("sample", "s20")])
        # d has no sources and is deliverable now; a/b still cannot move.
        blocked = handoff.next_batch("D", "r-block", 10)
        self.assertEqual(item_keys(blocked["items"]),
                         [("sample", "s40"), ("bottle", "d")])
        self.assertEqual(handoff.inspect("D")["open_request_ids"],
                         ["r-block", "r1", "r2"])

        # r1 partially accepted: only s10 becomes accepted, s0 is released.
        receipt = handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        self.assertEqual(receipt["accepted"], [ident("sample", "s10")])
        self.assertEqual(receipt["requeued"], [ident("sample", "s0")])
        handoff.acknowledge("D", "r-block", [])

        # --- Reopen at a waiting point (new instance) -------------------
        handoff.close()
        handoff = self.open_handoff()
        self.assertEqual(handoff.inspect("D")["open_request_ids"], ["r2"])

        # --- Step 2 -----------------------------------------------------
        r3 = handoff.next_batch("D", "r3", 2)
        self.assertEqual(item_keys(r3["items"]),
                         [("sample", "s0"), ("sample", "s40")])
        r4 = handoff.next_batch("D", "r4", 10)
        # b's only source s10 is accepted; d has no sources. a still waits on
        # s0, c waits on s20/s40 even though both are currently in flight.
        self.assertEqual(item_keys(r4["items"]),
                         [("bottle", "b"), ("bottle", "d")])
        receipt = handoff.acknowledge("D", "r4", [ident("bottle", "d")])
        self.assertEqual(receipt["accepted"], [ident("bottle", "d")])
        self.assertEqual(receipt["requeued"], [ident("bottle", "b")])

        # --- Step 3 -----------------------------------------------------
        r5 = handoff.next_batch("D", "r5", 10)
        self.assertEqual(item_keys(r5["items"]), [("bottle", "b")])
        handoff.acknowledge("D", "r5", [ident("bottle", "b")])
        r6 = handoff.next_batch("D", "r6", 10)
        self.assertEqual(r6["items"], [])
        self.assertEqual(handoff.inspect("D")["open_request_ids"], ["r2", "r3"])

        handoff.acknowledge("D", "r2", [ident("sample", "s20")])
        handoff.acknowledge("D", "r3",
                            [ident("sample", "s0"), ident("sample", "s40")])
        # r6 replay is still the permanently empty batch.
        self.assertEqual(handoff.next_batch("D", "r6", 10)["items"], [])
        r7 = handoff.next_batch("D", "r7", 10)
        self.assertEqual(item_keys(r7["items"]),
                         [("bottle", "a"), ("bottle", "c")])
        c_item = next(item for item in r7["items"] if item["id"] == "c")
        self.assertEqual(c_item["payload"], baseline["c"])

        # --- Continue from a brand new process --------------------------
        handoff.close()
        job = {
            "store": self.store,
            "ops": [
                ["acknowledge", "D", "r7",
                 [ident("bottle", "a"), ident("bottle", "c")]],
                ["next_batch", "D", "r6", 10],
                ["inspect", "D"],
            ],
        }
        outcome = self.run_in_new_process(job)
        self.assertEqual(outcome[1]["items"], [])
        self.assertTrue(outcome[2]["complete"])

        handoff = self.open_handoff()
        view = handoff.inspect("D")
        self.assertTrue(view["complete"])
        self.assertEqual(view["open_request_ids"], [])
        self.assertEqual({s["id"]: s["state"] for s in view["samples"]}, {
            "s0": "accepted", "s10": "accepted",
            "s20": "accepted", "s40": "accepted",
        })
        self.assertEqual({b["id"]: b["state"] for b in view["bottles"]}, {
            "a": "accepted", "b": "accepted", "c": "accepted", "d": "accepted",
        })

        # --- Step 4: fixed replays never move current facts -------------
        replayed_batch = handoff.next_batch("D", "r1", 2)
        self.assertEqual(item_keys(replayed_batch["items"]),
                         [("sample", "s0"), ("sample", "s10")])
        replayed_receipt = handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        self.assertEqual(replayed_receipt["requeued"], [ident("sample", "s0")])
        # s0 stays accepted and is never delivered again.
        self.assertEqual(handoff.inspect("D")["samples"][0]["state"], "accepted")
        fresh = handoff.next_batch("D", "r8", 10)
        self.assertEqual(fresh["items"], [])
        replayed_r4 = handoff.acknowledge(
            "D", "r4", [ident("bottle", "d")])
        self.assertEqual(replayed_r4["requeued"], [ident("bottle", "b")])
        self.assertEqual(
            next(b for b in handoff.inspect("D")["bottles"] if b["id"] == "b")["state"],
            "accepted",
        )
        handoff.close()

    def run_in_new_process(self, job):
        worker = Path(self.temp.name) / "worker.py"
        worker.write_text(
            "import json, sys\n"
            f"sys.path.insert(0, {str(SRC_DIR)!r})\n"
            "from ctd_bottle_match import ArchiveHandoff\n"
            "job = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "h = ArchiveHandoff(job['store'])\n"
            "out = []\n"
            "for op in job['ops']:\n"
            "    name = op[0]\n"
            "    if name == 'next_batch':\n"
            "        out.append(h.next_batch(op[1], op[2], op[3]))\n"
            "    elif name == 'acknowledge':\n"
            "        out.append(h.acknowledge(op[1], op[2], op[3]))\n"
            "    else:\n"
            "        out.append(h.inspect(op[1]))\n"
            "h.close()\n"
            "json.dump(out, open(sys.argv[2], 'w', encoding='utf-8'))\n",
            encoding="utf-8",
        )
        job_path = Path(self.temp.name) / "job.json"
        out_path = Path(self.temp.name) / "out.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        # -I: isolated mode, the worker inserts the absolute src path itself.
        completed = subprocess.run(
            [sys.executable, "-I", str(worker), str(job_path), str(out_path)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0)
        return json.loads(out_path.read_text(encoding="utf-8"))


class HandoffRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = str(Path(self.temp.name) / "ledger.json")
        self.handoff = ArchiveHandoff(self.store)

    def reopen(self):
        self.handoff.close()
        self.handoff = ArchiveHandoff(self.store)
        return self.handoff

    def test_sample_and_bottle_may_share_an_id(self):
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "x", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "y", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            ]}],
            "bottles": [{"id": "x", "cast_id": "c", "depth_m": "5"}],
        }
        self.handoff.register("D", data)
        batch = self.handoff.next_batch("D", "r", 10)["items"]
        # The bottle is blocked until both sources are acknowledged, even with
        # a shared id: identities stay distinct (kind, id) pairs.
        self.assertEqual(item_keys(batch), [("sample", "x"), ("sample", "y")])
        self.handoff.acknowledge(
            "D", "r", [ident("sample", "x"), ident("sample", "y")])
        follow_up = self.handoff.next_batch("D", "r2", 10)["items"]
        self.assertEqual(item_keys(follow_up), [("bottle", "x")])
        self.handoff.acknowledge("D", "r2", [ident("bottle", "x")])
        self.assertTrue(self.handoff.inspect("D")["complete"])

    def test_dataset_ids_isolate_reception_facts(self):
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "s10", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "s20", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            ]}],
            "bottles": [{"id": "b", "cast_id": "c", "depth_m": "10"}],
        }
        self.handoff.register("D1", copy.deepcopy(data))
        self.handoff.register("D2", copy.deepcopy(data))
        # The bottle exactly hits depth 10, so only s20 participates.
        first = self.handoff.next_batch("D1", "r1", 10)
        self.assertEqual(item_keys(first["items"]), [("sample", "s20")])
        self.handoff.acknowledge("D1", "r1", [ident("sample", "s20")])
        # D2 has received nothing: its own s20 fact is still pending.
        d2_batch = self.handoff.next_batch("D2", "r2", 10)
        self.assertEqual(item_keys(d2_batch["items"]), [("sample", "s20")])
        view = self.handoff.inspect("D2")
        self.assertFalse(view["complete"])
        self.assertEqual(view["bottles"][0]["waiting_for_sample_ids"], ["s20"])

    def test_exact_fraction_strings_are_normalized_without_floats(self):
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34/2"},
                {"id": "s3", "depth_m": "3", "temperature_c": "19", "salinity_psu": "71/2"},
            ]}],
            "bottles": [{"id": "b", "cast_id": "c", "depth_m": "1"}],
        }
        self.handoff.register("D", data)
        batch = self.handoff.next_batch("D", "r", 10)
        s0 = next(item for item in batch["items"] if item["id"] == "s0")
        self.assertEqual(s0["payload"], {
            "id": "s0", "cast_id": "c", "depth_m": "0",
            "temperature_c": "20", "salinity_psu": "17",
        })
        self.handoff.acknowledge("D", "r",
                                 [ident("sample", "s0"), ident("sample", "s3")])
        bottle_batch = self.handoff.next_batch("D", "r2", 10)
        payload = bottle_batch["items"][0]["payload"]
        # 1/3 of the way from s0 to s3:
        # temperature 20 + (1/3)*(19-20) = 59/3
        # salinity 17 + (1/3)*(71/2-17) = 17 + 37/6 = 139/6
        self.assertEqual(payload["temperature_c"], "59/3")
        self.assertEqual(payload["salinity_psu"], "139/6")

        def visit(value):
            self.assertNotIsInstance(value, float)
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(bottle_batch)

    def test_all_outside_profile_completes_with_zero_samples(self):
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "s10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            ]}],
            "bottles": [
                {"id": "b1", "cast_id": "c", "depth_m": "50"},
                {"id": "b2", "cast_id": "c", "depth_m": "99"},
            ],
        }
        self.assertEqual(self.handoff.register("D", data)["sample_count"], 0)
        batch = self.handoff.next_batch("D", "r", 10)
        self.assertEqual(item_keys(batch["items"]),
                         [("bottle", "b1"), ("bottle", "b2")])
        self.handoff.acknowledge("D", "r",
                                 [ident("bottle", "b1"), ident("bottle", "b2")])
        view = self.handoff.inspect("D")
        self.assertEqual(view["samples"], [])
        self.assertTrue(view["complete"])

    def test_sources_accepted_in_first_batch_release_bottles_only_afterwards(self):
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "s10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            ]}],
            "bottles": [{"id": "b", "cast_id": "c", "depth_m": "5"}],
        }
        self.handoff.register("D", data)
        first = self.handoff.next_batch("D", "r1", 100)
        # The dependent bottle cannot be unlocked inside the same batch.
        self.assertEqual(item_keys(first["items"]),
                         [("sample", "s0"), ("sample", "s10")])
        self.handoff.acknowledge("D", "r1",
                                 [ident("sample", "s0"), ident("sample", "s10")])
        second = self.handoff.next_batch("D", "r2", 100)
        self.assertEqual(item_keys(second["items"]), [("bottle", "b")])

    def test_one_source_confirmation_fans_out_to_many_bottles(self):
        bottles = [{"id": f"b{i:03d}", "cast_id": "c", "depth_m": "10"}
                   for i in range(200)]
        data = {
            "max_gap_m": "12",
            "casts": [{"id": "c", "samples": [
                {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
                {"id": "s10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
            ]}],
            "bottles": bottles,
        }
        # Every bottle exactly hits s10: only one sample record participates.
        self.assertEqual(self.handoff.register("D", data),
                         {"dataset_id": "D", "sample_count": 1, "bottle_count": 200})
        first = self.handoff.next_batch("D", "r1", 1000)
        self.assertEqual(item_keys(first["items"]), [("sample", "s10")])
        self.handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        second = self.handoff.next_batch("D", "r2", 1000)
        self.assertEqual(len(second["items"]), 200)
        self.assertTrue(all(item["kind"] == "bottle" for item in second["items"]))
        # A single accepted sample fact backs all bottles; no duplicated facts.
        self.assertEqual(len(self.handoff.inspect("D")["samples"]), 1)

    def test_invalid_ack_with_one_foreign_identity_changes_nothing(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.next_batch("D", "r1", 2)
        with self.assertRaises(ValueError):
            self.handoff.acknowledge("D", "r1", [
                ident("sample", "s10"), ident("sample", "s20")])
        # Still open, nothing accepted, reservation intact.
        view = self.handoff.inspect("D")
        self.assertEqual(view["open_request_ids"], ["r1"])
        self.assertEqual(view["samples"][0]["state"], "reserved")
        # The valid identity alone would have been legal; batch still usable.
        receipt = self.handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        self.assertEqual(receipt["requeued"], [ident("sample", "s0")])

    def test_acknowledge_reorder_replays_but_changed_set_conflicts(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.next_batch("D", "r1", 2)
        first = self.handoff.acknowledge(
            "D", "r1", [ident("sample", "s10"), ident("sample", "s0")])
        # Same set, different order -> fixed receipt, no state change.
        replay = self.handoff.acknowledge(
            "D", "r1", [ident("sample", "s10"), ident("sample", "s0")])
        self.assertEqual(replay, first)
        with self.assertRaises(ValueError):
            self.handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        with self.assertRaises(ValueError):
            self.handoff.acknowledge("D", "r1", [])

    def test_limit_conflict_and_empty_batch_ack_rules(self):
        self.handoff.register("D", acceptance_data())
        self.assertEqual(len(self.handoff.next_batch("D", "r", 2)["items"]), 2)
        with self.assertRaises(ValueError):
            self.handoff.next_batch("D", "r", 3)
        # Same limit replays the original batch unchanged.
        again = self.handoff.next_batch("D", "r", 2)
        self.assertEqual(item_keys(again["items"]),
                         [("sample", "s0"), ("sample", "s10")])
        self.handoff.acknowledge(
            "D", "r", [ident("sample", "s0"), ident("sample", "s10")])
        # Drain every remaining record before an empty batch is possible.
        second = self.handoff.next_batch("D", "r2", 10)
        self.handoff.acknowledge("D", "r2",
                                 [{"kind": key[0], "id": key[1]}
                                  for key in item_keys(second["items"])])
        # Bottles a, b, c are now unblocked (d already accepted above).
        third = self.handoff.next_batch("D", "r3", 10)
        self.handoff.acknowledge("D", "r3",
                                 [{"kind": key[0], "id": key[1]}
                                  for key in item_keys(third["items"])])
        empty = self.handoff.next_batch("D", "e", 10)
        self.assertEqual(empty["items"], [])
        # A permanently empty batch only accepts an empty array.
        with self.assertRaises(ValueError):
            self.handoff.acknowledge("D", "e", [ident("sample", "s0")])
        receipt = self.handoff.acknowledge("D", "e", [])
        self.assertEqual(receipt, {
            "dataset_id": "D", "request_id": "e",
            "accepted": [], "requeued": [],
        })
        # Later availability cannot revive the recorded empty batch.
        self.assertEqual(self.handoff.next_batch("D", "e", 10)["items"], [])

    def test_boolean_limit_rejected_and_other_argument_validation(self):
        self.handoff.register("D", acceptance_data())
        for bad_limit in (0, 1001, True, False, 1.5, "2", None):
            with self.subTest(bad_limit=bad_limit):
                with self.assertRaises(ValueError):
                    self.handoff.next_batch("D", "r", bad_limit)
        with self.assertRaises(ValueError):
            self.handoff.next_batch("", "r", 2)
        with self.assertRaises(ValueError):
            self.handoff.next_batch("D", "", 2)
        with self.assertRaises(ValueError):
            self.handoff.next_batch("missing", "r", 2)
        with self.assertRaises(ValueError):
            self.handoff.acknowledge("D", "r", "not-a-list")
        with self.assertRaises(ValueError):
            self.handoff.inspect("missing")
        with self.assertRaises(ValueError):
            self.handoff.register("D2", "not-a-dict")

    def test_qc_revisions_field_is_rejected_and_duplicate_registration_fails(self):
        data = acceptance_data()
        self.handoff.register("D", data)
        with self.assertRaises(ValueError):
            self.handoff.register("D", acceptance_data())
        qc_data = acceptance_data()
        qc_data["qc_revisions"] = []
        with self.assertRaises(ValueError):
            self.handoff.register("D2", qc_data)
        # Failed registration leaves no partial dataset behind.
        with self.assertRaises(ValueError):
            self.handoff.inspect("D2")

    def test_caller_input_and_returned_objects_cannot_pollute_ledger(self):
        data = acceptance_data()
        self.handoff.register("D", data)
        data["casts"][0]["samples"][0]["temperature_c"] = "1"
        data["bottles"][0]["depth_m"] = "999"
        batch = self.handoff.next_batch("D", "r1", 10)
        s0 = next(item for item in batch["items"] if item["id"] == "s0")
        self.assertEqual(s0["payload"]["temperature_c"], "20")
        s0["payload"]["temperature_c"] = "hacked"
        batch["items"].pop()
        replay = self.handoff.next_batch("D", "r1", 10)
        # Four samples plus the dependency-free bottle d; mutation did nothing.
        self.assertEqual(len(replay["items"]), 5)
        self.assertEqual(replay["items"][0]["payload"]["temperature_c"], "20")
        receipt = self.handoff.acknowledge(
            "D", "r1", [ident("sample", s) for s in ("s0", "s10", "s20", "s40")])
        receipt["accepted"].append(ident("bottle", "a"))
        again = self.handoff.next_batch("D", "r2", 10)
        self.assertEqual(item_keys(again["items"]),
                         [("bottle", "a"), ("bottle", "b"),
                          ("bottle", "c"), ("bottle", "d")])
        registration = self.handoff.register("E", acceptance_data())
        registration["sample_count"] = 999
        self.assertEqual(len(self.handoff.inspect("E")["samples"]), 4)

    def test_response_lost_then_reopen_replays_identical_results(self):
        self.handoff.register("D", acceptance_data())
        lost_batch = self.handoff.next_batch("D", "r1", 2)
        lost_receipt_input = [ident("sample", "s10")]
        self.handoff.acknowledge("D", "r1", lost_receipt_input)
        self.reopen()
        batch = self.handoff.next_batch("D", "r1", 2)
        self.assertEqual(batch, lost_batch)
        receipt = self.handoff.acknowledge("D", "r1", lost_receipt_input)
        self.assertEqual(receipt["requeued"], [ident("sample", "s0")])

    def test_failed_batch_persist_leaves_no_orphan_reservation(self):
        self.handoff.register("D", acceptance_data())
        with patch.object(self.handoff, "_atomic_write",
                          side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.handoff.next_batch("D", "r1", 2)
        # In-memory state rolled back: request and reservation do not exist.
        view = self.handoff.inspect("D")
        self.assertEqual(view["open_request_ids"], [])
        self.assertTrue(all(s["state"] == "pending" for s in view["samples"]))
        # Disk never saw the operation: a reopened instance agrees.
        reopened = ArchiveHandoff(self.store)
        self.assertEqual(reopened.inspect("D")["open_request_ids"], [])
        # The request id is still free for a genuine first pull.
        batch = reopened.next_batch("D", "r1", 2)
        self.assertEqual(item_keys(batch["items"]),
                         [("sample", "s0"), ("sample", "s10")])

    def test_failed_replace_keeps_last_committed_ledger_and_allows_retry(self):
        self.handoff.register("D", acceptance_data())
        with patch("ctd_bottle_match.archive.os.replace",
                   side_effect=OSError("rename failed")):
            with self.assertRaises(OSError):
                self.handoff.next_batch("D", "r1", 2)
        self.assertEqual(self.handoff.inspect("D")["open_request_ids"], [])
        # Leftover temp file is truncated and reused by the next commit.
        batch = self.handoff.next_batch("D", "r1", 2)
        self.assertEqual(item_keys(batch["items"]),
                         [("sample", "s0"), ("sample", "s10")])
        self.reopen()
        self.assertEqual(self.handoff.inspect("D")["open_request_ids"], ["r1"])

    def test_failed_acknowledge_persist_keeps_batch_open_and_retryable(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.next_batch("D", "r1", 2)
        with patch.object(self.handoff, "_atomic_write",
                          side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        view = self.handoff.inspect("D")
        self.assertEqual(view["open_request_ids"], ["r1"])
        self.assertTrue(all(s["state"] == "reserved" for s in view["samples"][:2]))
        # Nothing was partially accepted; retry now completes the batch.
        receipt = self.handoff.acknowledge("D", "r1", [ident("sample", "s10")])
        self.assertEqual(receipt["requeued"], [ident("sample", "s0")])
        self.reopen()
        states = {s["id"]: s["state"] for s in self.handoff.inspect("D")["samples"]}
        self.assertEqual(states["s10"], "accepted")
        self.assertEqual(states["s0"], "pending")

    def test_closed_instance_rejects_calls_but_path_can_reopen(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.close()
        with self.assertRaises(ValueError):
            self.handoff.inspect("D")
        with self.assertRaises(ValueError):
            self.handoff.next_batch("D", "r", 2)
        reopened = ArchiveHandoff(self.store)
        self.assertEqual(len(reopened.inspect("D")["samples"]), 4)

    def test_inspect_states_and_waiting_lists(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.next_batch("D", "r1", 2)  # reserves s0, s10
        view = self.handoff.inspect("D")
        self.assertEqual([s["id"] for s in view["samples"]],
                         ["s0", "s10", "s20", "s40"])
        self.assertEqual([s["state"] for s in view["samples"]],
                         ["reserved", "reserved", "pending", "pending"])
        bottles = {b["id"]: b for b in view["bottles"]}
        self.assertEqual(bottles["a"]["state"], "blocked")
        self.assertEqual(bottles["a"]["waiting_for_sample_ids"], ["s0", "s10"])
        self.assertEqual(bottles["d"]["state"], "pending")
        self.assertEqual(bottles["d"]["waiting_for_sample_ids"], [])
        self.handoff.acknowledge("D", "r1",
                                 [ident("sample", "s0"), ident("sample", "s10")])
        view = self.handoff.inspect("D")
        bottle_a = next(b for b in view["bottles"] if b["id"] == "a")
        self.assertEqual(bottle_a["state"], "pending")
        self.assertEqual(bottle_a["waiting_for_sample_ids"], [])

    def test_malformed_ledger_is_rejected_with_value_error(self):
        self.handoff.register("D", acceptance_data())
        self.handoff.close()
        Path(self.store).write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            ArchiveHandoff(self.store)

    def test_multiple_reopen_cycles_and_no_leftover_temp_files(self):
        self.handoff.register("D", acceptance_data())
        for index in range(3):
            self.handoff.next_batch("D", f"r{index}", 1)
            self.reopen()
        # Each commit reuses one .tmp name; nothing lingers after success.
        leftovers = list(Path(self.temp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])
        self.assertEqual(self.handoff.inspect("D")["open_request_ids"],
                         ["r0", "r1", "r2"])

    def test_rejected_records_return_to_pool_and_accepted_ones_stay(self):
        self.handoff.register("D", acceptance_data())
        first = self.handoff.next_batch("D", "r1", 10)
        # d has no sources, so it joins the four samples in the first pull.
        self.assertEqual(item_keys(first["items"]),
                         [("sample", "s0"), ("sample", "s10"),
                          ("sample", "s20"), ("sample", "s40"),
                          ("bottle", "d")])
        # Reject the whole first batch.
        self.handoff.acknowledge("D", "r1", [])
        again = self.handoff.next_batch("D", "r2", 1)
        self.assertEqual(item_keys(again["items"]), [("sample", "s0")])
        self.handoff.acknowledge("D", "r2", [ident("sample", "s0")])
        self.reopen()
        states = {s["id"]: s["state"] for s in self.handoff.inspect("D")["samples"]}
        self.assertEqual(states["s0"], "accepted")
        next_one = self.handoff.next_batch("D", "r3", 10)
        # s0 is never redelivered; remaining samples come first, d is still
        # pending after its earlier rejection and follows the sample block.
        self.assertEqual(item_keys(next_one["items"]),
                         [("sample", "s10"), ("sample", "s20"),
                          ("sample", "s40"), ("bottle", "d")])


if __name__ == "__main__":
    unittest.main()
