from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from fractions import Fraction

from .service import process, _fraction, _text

_KINDS = ("sample", "bottle")
# Empty dataset ledger written once when the store file is first created.
_EMPTY_STATE = {"datasets": {}}
# Schema tag stored at the top level so an older or foreign ledger is rejected.
_SCHEMA_VERSION = 1


class ArchiveHandoff:
    """Recoverable local ledger for CTD evidence and bottle handoff.

    The whole ledger is kept in memory and every successful mutating call is
    persisted atomically to a single JSON file. Reopening the same path, even
    from another process, reconstructs the exact committed state. Writes are
    only expected to be serialized for one store; multiple instances may take
    turns on the same path as long as the previous one is closed.
    """

    def __init__(self, store_path: str | os.PathLike):
        self._path = Path(store_path)
        self._closed = False
        self._state = self._read_state()

    # ------------------------------------------------------------------
    # Persistence primitives
    # ------------------------------------------------------------------
    def _read_state(self) -> dict:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            state = copy.deepcopy(_EMPTY_STATE)
            self._atomic_write(json.dumps(state, ensure_ascii=False, sort_keys=True))
            return state
        try:
            return self._decode_state(raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ValueError("unrecognized archive ledger format") from exc

    def _decode_state(self, raw: str) -> dict:
        state = json.loads(raw)
        if (not isinstance(state, dict) or state.get("version") != _SCHEMA_VERSION
                or not isinstance(state.get("datasets"), dict)):
            raise ValueError("unrecognized archive ledger format")
        # JSON arrays decode as lists; record identities are tuples in memory.
        for dataset in state["datasets"].values():
            if not isinstance(dataset, dict) or not isinstance(dataset.get("requests"), dict):
                raise ValueError("unrecognized archive ledger format")
            dataset["accepted"] = {tuple(key) for key in dataset.get("accepted", ())}
            for request in dataset["requests"].values():
                request["items"] = [tuple(key) for key in request["items"]]
        return state

    def _atomic_write(self, payload: str) -> None:
        """One persistence boundary: replace the ledger file atomically.

        Tests inject failures here to prove the in-memory state is rolled back
        to the last committed content when the boundary does not complete.
        """
        temporary = self._path.with_name(self._path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)
        directory = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _commit(self, mutator) -> None:
        """Run *mutator* against in-memory state and persist exactly once.

        Any failure before or inside the persistence boundary rolls the
        in-memory ledger back to the last committed state, so a crashed or
        failed operation never leaves half-applied reservations.
        """
        snapshot = copy.deepcopy(self._state)
        try:
            mutator()
            state = copy.deepcopy(self._state)
            state["version"] = _SCHEMA_VERSION
            # Sets are emitted as sorted arrays; _read_state restores them.
            self._atomic_write(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                          default=_json_default))
        except BaseException:
            self._state = snapshot
            raise

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("this ArchiveHandoff instance has been closed")

    def _identity_key(self, value: object) -> tuple[str, str]:
        if not isinstance(value, dict):
            raise ValueError("record identity must be an object with kind and id")
        kind = value.get("kind")
        record_id = value.get("id")
        if kind not in _KINDS or not isinstance(record_id, str) or not record_id:
            raise ValueError("record identity needs kind 'sample' or 'bottle' and a nonempty id")
        return kind, record_id

    def _require_dataset(self, dataset_id: object) -> dict:
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError("dataset_id must be a nonempty string")
        dataset = self._state["datasets"].get(dataset_id)
        if dataset is None:
            raise ValueError(f"unknown dataset_id: {dataset_id}")
        return dataset

    @staticmethod
    def _sort_key(key: tuple[str, str]):
        return (0 if key[0] == "sample" else 1, key[1])

    def _sorted_identities(self, keys) -> list[tuple[str, str]]:
        return sorted(keys, key=self._sort_key)

    def _payloads_of(self, dataset: dict) -> dict[tuple[str, str], dict]:
        payloads: dict[tuple[str, str], dict] = {}
        for sample in dataset["samples"]:
            payloads[("sample", sample["id"])] = sample["payload"]
        for bottle in dataset["bottles"]:
            payloads[("bottle", bottle["id"])] = bottle["payload"]
        return payloads

    # ------------------------------------------------------------------
    # 1. Registration
    # ------------------------------------------------------------------
    def register(self, dataset_id: str, data: dict) -> dict:
        self._require_open()
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError("dataset_id must be a nonempty string")
        if not isinstance(data, dict):
            raise ValueError("input must be an object")
        if "qc_revisions" in data:
            raise ValueError("archive handoff does not accept qc_revisions; use process_with_qc separately")
        if dataset_id in self._state["datasets"]:
            raise ValueError(f"dataset already registered: {dataset_id}")

        # Reuses every baseline validation, size limit and Fraction rule.
        result = process(data)

        sample_index: dict[str, dict] = {}
        for cast in data["casts"]:
            for sample in cast["samples"]:
                depth = _fraction(sample.get("depth_m"), "depth_m")
                sample_index[sample["id"]] = {
                    "id": sample["id"],
                    "cast_id": cast["id"],
                    "depth_m": _text(depth),
                    "temperature_c": _text(_fraction(sample.get("temperature_c"), "temperature_c")),
                    "salinity_psu": _text(_fraction(sample.get("salinity_psu"), "salinity_psu")),
                }

        referenced: list[str] = []
        seen: set[str] = set()
        bottles: list[dict] = []
        for row in result["bottles"]:
            # Deep copy: later caller mutation of the input must never reach
            # the ledger; payloads are also plain JSON-safe value objects.
            payload = copy.deepcopy(row)
            sources = list(payload.get("source_sample_ids", ()))
            bottles.append({"id": payload["id"], "payload": payload, "sources": sources})
            for source_id in sources:
                if source_id not in seen:
                    seen.add(source_id)
                    referenced.append(source_id)

        samples = [
            {"id": sample_id, "payload": copy.deepcopy(sample_index[sample_id])}
            for sample_id in sorted(referenced)
        ]

        dataset = {
            "samples": samples,
            "bottles": bottles,
            "accepted": set(),
            "requests": {},
        }

        def apply() -> None:
            self._state["datasets"][dataset_id] = dataset

        self._commit(apply)
        return {
            "dataset_id": dataset_id,
            "sample_count": len(samples),
            "bottle_count": len(bottles),
        }

    # ------------------------------------------------------------------
    # 2. Batch selection with dependency gating
    # ------------------------------------------------------------------
    def next_batch(self, dataset_id: str, request_id: str, limit: int) -> dict:
        self._require_open()
        dataset = self._require_dataset(dataset_id)
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a nonempty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")

        existing = dataset["requests"].get(request_id)
        if existing is not None:
            if existing["limit"] != limit:
                raise ValueError("request_id is already bound to a different limit")
            return self._batch_response(dataset_id, request_id, existing)

        accepted: set[tuple[str, str]] = dataset["accepted"]
        reserved: set[tuple[str, str]] = set()
        for request in dataset["requests"].values():
            if not request["closed"]:
                reserved.update(request["items"])

        sample_keys = self._sorted_identities(
            ("sample", sample["id"])
            for sample in dataset["samples"]
            if ("sample", sample["id"]) not in accepted
            and ("sample", sample["id"]) not in reserved
        )

        bottle_candidates: list[tuple[str, str]] = []
        for bottle in sorted(dataset["bottles"], key=lambda item: item["id"]):
            key = ("bottle", bottle["id"])
            if key in accepted or key in reserved:
                continue
            # Only acknowledgements from previously committed batches count:
            # samples in this batch or in other open batches are not accepted.
            if all(("sample", source_id) in accepted for source_id in bottle["sources"]):
                bottle_candidates.append(key)

        chosen = (sample_keys + bottle_candidates)[:limit]
        request_record = {
            "limit": limit,
            "items": chosen,
            "closed": len(chosen) == 0,
            "receipt": None,
        }

        def apply() -> None:
            dataset["requests"][request_id] = request_record

        self._commit(apply)
        return self._batch_response(dataset_id, request_id, request_record)

    def _batch_response(self, dataset_id: str, request_id: str, request_record: dict) -> dict:
        payloads = self._payloads_of(self._state["datasets"][dataset_id])
        items = [
            {
                "kind": key[0],
                "id": key[1],
                "payload": copy.deepcopy(payloads[key]),
            }
            for key in request_record["items"]
        ]
        return {"dataset_id": dataset_id, "request_id": request_id, "items": items}

    # ------------------------------------------------------------------
    # 3. Partial acknowledgements
    # ------------------------------------------------------------------
    def acknowledge(self, dataset_id: str, request_id: str, accepted: list) -> dict:
        self._require_open()
        dataset = self._require_dataset(dataset_id)
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a nonempty string")
        if not isinstance(accepted, list):
            raise ValueError("accepted must be an array of record identities")

        request = dataset["requests"].get(request_id)
        if request is None:
            raise ValueError(f"unknown request_id: {request_id}")

        parsed: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for identity in accepted:
            key = self._identity_key(identity)
            if key in seen:
                raise ValueError("accepted contains a duplicated record identity")
            seen.add(key)
            parsed.append(key)

        batch_keys = set(request["items"])
        for key in parsed:
            if key not in batch_keys:
                raise ValueError("accepted identity does not belong to this batch")
        accepted_set = set(parsed)

        receipt = {
            "dataset_id": dataset_id,
            "request_id": request_id,
            "accepted": [
                {"kind": key[0], "id": key[1]}
                for key in self._sorted_identities(accepted_set)
            ],
            "requeued": [
                {"kind": key[0], "id": key[1]}
                for key in self._sorted_identities(batch_keys - accepted_set)
            ],
        }

        if request["closed"]:
            # Settled batches replay their fixed receipt when the same accepted
            # set is presented; empty batches only accept an empty array.
            if request["items"]:
                fixed = {self._identity_key(identity)
                         for identity in request["receipt"]["accepted"]}
                if fixed != accepted_set:
                    raise ValueError("this batch is already settled with a different accepted set")
            elif accepted_set:
                raise ValueError("an empty batch can only be acknowledged with an empty accepted array")
            elif request["receipt"] is not None:
                return copy.deepcopy(request["receipt"])
            # First acknowledgement of a recorded empty batch: persist the
            # fixed empty receipt so replay works after close/reopen too.
            def settle_empty() -> None:
                request["receipt"] = copy.deepcopy(receipt)

            self._commit(settle_empty)
            return copy.deepcopy(receipt)

        def apply() -> None:
            dataset["accepted"].update(accepted_set)
            request["closed"] = True
            request["receipt"] = copy.deepcopy(receipt)

        self._commit(apply)
        return copy.deepcopy(receipt)

    # ------------------------------------------------------------------
    # 4. Read-only observation
    # ------------------------------------------------------------------
    def inspect(self, dataset_id: str) -> dict:
        self._require_open()
        dataset = self._require_dataset(dataset_id)

        reserved: set[tuple[str, str]] = set()
        open_request_ids: list[str] = []
        for request_id, request in dataset["requests"].items():
            if not request["closed"]:
                reserved.update(request["items"])
                open_request_ids.append(request_id)

        samples = []
        for sample in sorted(dataset["samples"], key=lambda item: item["id"]):
            key = ("sample", sample["id"])
            if key in dataset["accepted"]:
                state = "accepted"
            elif key in reserved:
                state = "reserved"
            else:
                state = "pending"
            samples.append({"id": sample["id"], "state": state})

        bottles = []
        for bottle in sorted(dataset["bottles"], key=lambda item: item["id"]):
            key = ("bottle", bottle["id"])
            waiting = [
                source_id for source_id in bottle["sources"]
                if ("sample", source_id) not in dataset["accepted"]
            ]
            if key in dataset["accepted"]:
                state = "accepted"
            elif key in reserved:
                state = "reserved"
            else:
                state = "blocked" if waiting else "pending"
            bottles.append({
                "id": bottle["id"],
                "state": state,
                "waiting_for_sample_ids": waiting,
            })

        total = len(dataset["samples"]) + len(dataset["bottles"])
        complete = total > 0 and len(dataset["accepted"]) == total
        return {
            "dataset_id": dataset_id,
            "samples": samples,
            "bottles": bottles,
            "open_request_ids": sorted(open_request_ids),
            "complete": complete,
        }

    def close(self) -> None:
        self._closed = True


def _json_default(value):
    if isinstance(value, Fraction):  # defensive: payloads are already textual
        return _text(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"object of type {type(value).__name__} is not ledger serializable")
