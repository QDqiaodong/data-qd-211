"""Runnable archive handoff demonstration.

Scenario:
  * A bottle at 5 m is an interpolation between CTD samples s0 (0 m) and
    s10 (10 m), so its archive record depends on both source samples.
  * The archive centre first accepts only one endpoint. The bottle must stay
    blocked: a source "sent" or even reserved in another open batch is not a
    source "received".
  * Once the missing endpoint is acknowledged, a new request finally delivers
    the bottle. The already accepted source is never sent twice.
  * The store is closed and reopened midway to show the facts survive in the
    local ledger; no network or real archive service is involved.

Run:

    PYTHONPATH=src python3 examples/run_archive_handoff.py
"""
import json
import tempfile
from pathlib import Path

from ctd_bottle_match import ArchiveHandoff

DATA = {
    "max_gap_m": "12",
    "casts": [{"id": "cast-a", "samples": [
        {"id": "s0", "depth_m": "0", "temperature_c": "20", "salinity_psu": "34"},
        {"id": "s10", "depth_m": "10", "temperature_c": "18", "salinity_psu": "35"},
    ]}],
    "bottles": [{"id": "niskin-5", "cast_id": "cast-a", "depth_m": "5"}],
}


def show(title, value):
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    with tempfile.TemporaryDirectory() as directory:
        store_path = str(Path(directory) / "archive_ledger.json")
        handoff = ArchiveHandoff(store_path)
        show("register", handoff.register("cruise-1", DATA))

        first_batch = handoff.next_batch("cruise-1", "req-1", 100)
        show("first batch delivers the two CTD source samples", first_batch)

        # Only one interpolation endpoint is received by the archive centre.
        receipt = handoff.acknowledge("cruise-1", "req-1",
                                      [{"kind": "sample", "id": "s0"}])
        show("partial acknowledgement: s10 is requeued", receipt)

        # Reopen the same ledger in a fresh instance, as a new process would.
        handoff.close()
        handoff = ArchiveHandoff(store_path)

        view = handoff.inspect("cruise-1")
        show("after reopen: bottle is still blocked, waiting for s10", view)

        # s0 is accepted and never re-sent; a new request only redelivers the
        # rejected endpoint s10. The dependent bottle still cannot join.
        retry_batch = handoff.next_batch("cruise-1", "req-2", 100)
        show("only the missing source is redelivered; bottle stays blocked",
             retry_batch)
        handoff.acknowledge("cruise-1", "req-2",
                            [{"kind": "sample", "id": "s10"}])

        final_batch = handoff.next_batch("cruise-1", "req-3", 100)
        show("both sources received: the bottle is now deliverable", final_batch)
        handoff.acknowledge(
            "cruise-1", "req-3",
            [{"kind": item["kind"], "id": item["id"]} for item in final_batch["items"]],
        )
        show("final ledger state", handoff.inspect("cruise-1"))
        handoff.close()


if __name__ == "__main__":
    main()
