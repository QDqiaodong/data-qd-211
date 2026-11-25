from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction


def _fraction(value: object, name: str) -> Fraction:
    if not isinstance(value, str) or not value or len(value) > 32:
        raise ValueError(f"{name} must be a bounded decimal string")
    try:
        number = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


def _text(number: Fraction) -> str:
    return str(number.numerator) if number.denominator == 1 else f"{number.numerator}/{number.denominator}"


def _parse_input(data: dict):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    max_gap = _fraction(data.get("max_gap_m"), "max_gap_m")
    casts = data.get("casts")
    bottles = data.get("bottles")
    if not isinstance(casts, list) or not 1 <= len(casts) <= 100:
        raise ValueError("casts must contain 1..100 items")
    if not isinstance(bottles, list) or not 1 <= len(bottles) <= 5000:
        raise ValueError("bottles must contain 1..5000 items")

    # Sample tuple: (depth, temperature, salinity, sample_id)
    profiles: dict[str, list[tuple[Fraction, Fraction, Fraction, str]]] = {}
    depths: dict[str, list[Fraction]] = {}
    sample_locations: dict[str, tuple[str, int]] = {}
    for cast in casts:
        cast_id = cast.get("id") if isinstance(cast, dict) else None
        samples = cast.get("samples") if isinstance(cast, dict) else None
        if not isinstance(cast_id, str) or not cast_id or cast_id in profiles:
            raise ValueError("cast ids must be unique nonblank strings")
        if not isinstance(samples, list) or not 2 <= len(samples) <= 10000:
            raise ValueError("each cast needs 2..10000 samples")
        profile = []
        depth_values = []
        previous = None
        for index, sample in enumerate(samples):
            sid = sample.get("id") if isinstance(sample, dict) else None
            if not isinstance(sid, str) or not sid or sid in sample_locations:
                raise ValueError("sample ids must be globally unique")
            depth = _fraction(sample.get("depth_m"), "depth_m")
            temperature = _fraction(sample.get("temperature_c"), "temperature_c")
            salinity = _fraction(sample.get("salinity_psu"), "salinity_psu")
            if previous is not None and depth <= previous:
                raise ValueError("sample depths must be strictly increasing")
            previous = depth
            sample_locations[sid] = (cast_id, index)
            profile.append((depth, temperature, salinity, sid))
            depth_values.append(depth)
        profiles[cast_id] = profile
        depths[cast_id] = depth_values

    # Validate every bottle before matching so an invalid later bottle cannot be
    # discovered only after part of the business calculation has run.
    parsed_bottles: list[tuple[str, str, Fraction]] = []
    bottle_ids: set[str] = set()
    for bottle in bottles:
        bottle_id = bottle.get("id") if isinstance(bottle, dict) else None
        cast_id = bottle.get("cast_id") if isinstance(bottle, dict) else None
        if not isinstance(bottle_id, str) or not bottle_id or bottle_id in bottle_ids:
            raise ValueError("bottle ids must be unique nonblank strings")
        if not isinstance(cast_id, str) or cast_id not in profiles:
            raise ValueError("unknown cast_id")
        bottle_ids.add(bottle_id)
        depth = _fraction(bottle.get("depth_m"), "depth_m")
        parsed_bottles.append((bottle_id, cast_id, depth))

    return max_gap, profiles, depths, parsed_bottles, sample_locations


def _base_row(bottle_id: str, cast_id: str, depth: Fraction) -> dict:
    return {"id": bottle_id, "cast_id": cast_id, "depth_m": _text(depth)}


def _match_profile(
    bottle_id: str,
    cast_id: str,
    depth: Fraction,
    profile: list[tuple[Fraction, Fraction, Fraction, str]],
    depth_values: list[Fraction],
    max_gap: Fraction,
) -> tuple[dict, str]:
    row = _base_row(bottle_id, cast_id, depth)
    if depth < depth_values[0] or depth > depth_values[-1]:
        row["status"] = "outside_profile"
        return row, "outside_profile"

    position = bisect_left(depth_values, depth)
    if depth_values[position] == depth:
        lower = upper = profile[position]
        gap = Fraction(0)
    else:
        lower = profile[position - 1]
        upper = profile[position]
        gap = upper[0] - lower[0]

    if gap > max_gap:
        row.update({
            "status": "gap_too_wide",
            "source_sample_ids": [lower[3], upper[3]],
            "gap_m": _text(gap),
        })
        return row, "gap_too_wide"

    ratio = Fraction(0) if gap == 0 else (depth - lower[0]) / gap
    temperature = lower[1] + ratio * (upper[1] - lower[1])
    salinity = lower[2] + ratio * (upper[2] - lower[2])
    row.update({
        "status": "matched",
        "temperature_c": _text(temperature),
        "salinity_psu": _text(salinity),
        "source_sample_ids": [lower[3]] if gap == 0 else [lower[3], upper[3]],
        "gap_m": _text(gap),
    })
    return row, "matched"


def process(data: dict) -> dict:
    max_gap, profiles, depth_values, parsed_bottles, _ = _parse_input(data)
    results = []
    counts = {"matched": 0, "outside_profile": 0, "gap_too_wide": 0}
    for bottle_id, cast_id, depth in parsed_bottles:
        row, status = _match_profile(
            bottle_id,
            cast_id,
            depth,
            profiles[cast_id],
            depth_values[cast_id],
            max_gap,
        )
        counts[status] += 1
        results.append(row)
    return {"bottles": sorted(results, key=lambda item: item["id"]), "counts": counts}


def _parse_qc_revisions(
    qc_revisions: object,
    profiles: dict[str, list[tuple[Fraction, Fraction, Fraction, str]]],
    sample_locations: dict[str, tuple[str, int]],
) -> list[tuple[int, str, str, list[str], str, str]]:
    if not isinstance(qc_revisions, list):
        raise ValueError("qc_revisions must be an array when provided")

    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    revisions = []
    for revision in qc_revisions:
        if not isinstance(revision, dict):
            raise ValueError("each qc revision must be an object")

        revision_id = revision.get("id")
        if not isinstance(revision_id, str) or not revision_id:
            raise ValueError("qc revision id must be a nonempty string")
        if revision_id in seen_ids:
            raise ValueError("qc revision ids must be globally unique")

        order = revision.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or order <= 0:
            raise ValueError("qc revision order must be a positive integer")
        if order in seen_orders:
            raise ValueError("qc revision orders must be globally unique")

        cast_id = revision.get("cast_id")
        if not isinstance(cast_id, str) or cast_id not in profiles:
            raise ValueError("qc revision cast_id must reference an existing cast")

        sample_ids = revision.get("sample_ids")
        if not isinstance(sample_ids, list) or not sample_ids:
            raise ValueError("qc revision sample_ids must be a nonempty array")
        seen_sample_ids: set[str] = set()
        for sample_id in sample_ids:
            if not isinstance(sample_id, str) or not sample_id:
                raise ValueError("qc revision sample ids must be nonempty strings")
            if sample_id in seen_sample_ids:
                raise ValueError("sample_ids within a qc revision must not repeat")
            location = sample_locations.get(sample_id)
            if location is None or location[0] != cast_id:
                raise ValueError("qc revision references an unknown sample for its cast")
            seen_sample_ids.add(sample_id)

        state = revision.get("state")
        if state not in ("invalid", "valid"):
            raise ValueError("qc revision state must be invalid or valid")

        reason = revision.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError("qc revision reason must be a nonempty string")

        seen_ids.add(revision_id)
        seen_orders.add(order)
        # Keep a read-only reference to the caller's validated sample id list;
        # sorting this new tuple list never reorders the caller's input.
        revisions.append((order, revision_id, cast_id, sample_ids, state, reason))

    # Explicit order makes the caller's list order irrelevant.
    revisions.sort(key=lambda item: item[0])
    return revisions


def _excluded_sample_ids(
    cast_id: str,
    lower_id: str,
    upper_id: str,
    profiles: dict[str, list[tuple[Fraction, Fraction, Fraction, str]]],
    sample_locations: dict[str, tuple[str, int]],
    valid_flags: dict[str, list[bool]],
) -> list[str]:
    lower_index = sample_locations[lower_id][1]
    upper_index = sample_locations[upper_id][1]
    flags = valid_flags[cast_id]
    profile = profiles[cast_id]
    return [
        profile[index][3]
        for index in range(lower_index + 1, upper_index)
        if not flags[index]
    ]


def _match_final_profile(
    bottle_id: str,
    cast_id: str,
    depth: Fraction,
    max_gap: Fraction,
    profiles,
    valid_profiles,
    valid_depths,
    valid_flags,
    sample_locations,
) -> tuple[dict, str]:
    row = _base_row(bottle_id, cast_id, depth)
    valid_profile = valid_profiles[cast_id]
    valid_depth_values = valid_depths[cast_id]

    if len(valid_profile) < 2:
        row.update({
            "status": "insufficient_valid_samples",
            "qc_excluded_sample_ids": [],
        })
        return row, "insufficient_valid_samples"

    if depth < valid_depth_values[0] or depth > valid_depth_values[-1]:
        row.update({"status": "outside_profile", "qc_excluded_sample_ids": []})
        return row, "outside_profile"

    position = bisect_left(valid_depth_values, depth)
    if valid_depth_values[position] == depth:
        lower = upper = valid_profile[position]
        gap = Fraction(0)
        excluded: list[str] = []
    else:
        lower = valid_profile[position - 1]
        upper = valid_profile[position]
        gap = upper[0] - lower[0]
        excluded = _excluded_sample_ids(
            cast_id,
            lower[3],
            upper[3],
            profiles,
            sample_locations,
            valid_flags,
        )

    if gap > max_gap:
        row.update({
            "status": "gap_too_wide",
            "source_sample_ids": [lower[3], upper[3]],
            "gap_m": _text(gap),
            "qc_excluded_sample_ids": excluded,
        })
        return row, "gap_too_wide"

    ratio = Fraction(0) if gap == 0 else (depth - lower[0]) / gap
    temperature = lower[1] + ratio * (upper[1] - lower[1])
    salinity = lower[2] + ratio * (upper[2] - lower[2])
    row.update({
        "status": "matched",
        "temperature_c": _text(temperature),
        "salinity_psu": _text(salinity),
        "source_sample_ids": [lower[3]] if gap == 0 else [lower[3], upper[3]],
        "gap_m": _text(gap),
        "qc_excluded_sample_ids": excluded,
    })
    return row, "matched"


def process_with_qc(data: dict) -> dict:
    # Parse and validate the complete baseline input before touching revisions
    # or matching. QC references are then fully validated before any results.
    max_gap, profiles, depths, parsed_bottles, sample_locations = _parse_input(data)
    revisions = _parse_qc_revisions(
        data.get("qc_revisions", []),
        profiles,
        sample_locations,
    )

    valid_flags: dict[str, list[bool]] = {
        cast_id: [True] * len(profile) for cast_id, profile in profiles.items()
    }
    last_revision_id: dict[str, list[str | None]] = {
        cast_id: [None] * len(profile) for cast_id, profile in profiles.items()
    }
    last_reason: dict[str, list[str | None]] = {
        cast_id: [None] * len(profile) for cast_id, profile in profiles.items()
    }

    # Revisions are already sorted by globally unique order. Repeated changes to
    # one sample are intentionally applied here and need no history copies.
    for _order, revision_id, cast_id, sample_ids, state, reason in revisions:
        is_valid = state == "valid"
        flags = valid_flags[cast_id]
        revision_ids = last_revision_id[cast_id]
        reasons = last_reason[cast_id]
        for sample_id in sample_ids:
            _cast_id, index = sample_locations[sample_id]
            flags[index] = is_valid
            revision_ids[index] = revision_id
            reasons[index] = reason

    valid_profiles: dict[str, list[tuple[Fraction, Fraction, Fraction, str]]] = {}
    valid_depths: dict[str, list[Fraction]] = {}
    for cast_id, profile in profiles.items():
        filtered_profile = []
        filtered_depths = []
        flags = valid_flags[cast_id]
        for index, sample in enumerate(profile):
            if flags[index]:
                filtered_profile.append(sample)
                filtered_depths.append(sample[0])
        valid_profiles[cast_id] = filtered_profile
        valid_depths[cast_id] = filtered_depths

    qc_samples = []
    for cast_id, profile in profiles.items():
        for index, sample in enumerate(profile):
            qc_samples.append({
                "cast_id": cast_id,
                "sample_id": sample[3],
                "state": "valid" if valid_flags[cast_id][index] else "invalid",
                "last_revision_id": last_revision_id[cast_id][index],
                "reason": last_reason[cast_id][index],
            })

    bottle_rows = []
    initial_counts = {"matched": 0, "outside_profile": 0, "gap_too_wide": 0}
    final_counts = {
        "matched": 0,
        "outside_profile": 0,
        "gap_too_wide": 0,
        "insufficient_valid_samples": 0,
    }

    for bottle_id, cast_id, bottle_depth in parsed_bottles:
        initial, initial_status = _match_profile(
            bottle_id,
            cast_id,
            bottle_depth,
            profiles[cast_id],
            depths[cast_id],
            max_gap,
        )
        final, final_status = _match_final_profile(
            bottle_id,
            cast_id,
            bottle_depth,
            max_gap,
            profiles,
            valid_profiles,
            valid_depths,
            valid_flags,
            sample_locations,
        )
        initial_counts[initial_status] += 1
        final_counts[final_status] += 1

        # qc_excluded_sample_ids is QC trace metadata added only to the final
        # view. Change detection compares the complete shared business schema.
        final_business_result = {
            key: value
            for key, value in final.items()
            if key != "qc_excluded_sample_ids"
        }
        changed = initial != final_business_result

        bottle_rows.append({
            "id": bottle_id,
            "cast_id": cast_id,
            "initial": initial,
            "final": final,
            "changed": changed,
        })

    bottle_rows.sort(key=lambda row: row["id"])
    impact = [
        {
            "bottle_id": row["id"],
            "from_status": row["initial"]["status"],
            "to_status": row["final"]["status"],
        }
        for row in bottle_rows
        if row["changed"]
    ]

    return {
        "bottles": bottle_rows,
        "counts": {
            "initial": initial_counts,
            "final": final_counts,
        },
        "qc_samples": qc_samples,
        "impact": impact,
    }


# Candidate tuple stored per canonical depth while parsing segments:
# (sequence, segment_id, sample_index, sample_id, depth, temperature,
#  salinity, original_sample_dict)
def _parse_segments_input(data: dict):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    max_gap = _fraction(data.get("max_gap_m"), "max_gap_m")
    segments = data.get("segments")
    bottles = data.get("bottles")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 1000:
        raise ValueError("segments must contain 1..1000 items")
    if not isinstance(bottles, list) or not 1 <= len(bottles) <= 5000:
        raise ValueError("bottles must contain 1..5000 items")

    # cast_id -> {"sequences", "segment_order", "sample_count", "depths"} where
    # depths maps each depth to every segment candidate covering that depth.
    casts: dict[str, dict] = {}
    segment_ids: set[str] = set()
    sample_ids: set[str] = set()

    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("each segment must be an object")
        seg_id = segment.get("id")
        if not isinstance(seg_id, str) or not seg_id or seg_id in segment_ids:
            raise ValueError("segment ids must be globally unique nonblank strings")
        cast_id = segment.get("cast_id")
        if not isinstance(cast_id, str) or not cast_id:
            raise ValueError("segment cast_id must be a nonblank string")
        sequence = segment.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("segment sequence must be a positive integer")
        samples = segment.get("samples")
        if not isinstance(samples, list) or not 2 <= len(samples) <= 10000:
            raise ValueError("each segment needs 2..10000 samples")

        cast = casts.get(cast_id)
        if cast is None:
            cast = {"sequences": set(), "segment_order": [], "sample_count": 0, "depths": {}}
            casts[cast_id] = cast
        if sequence in cast["sequences"]:
            raise ValueError("segment sequence must be unique within a cast")
        cast["sequences"].add(sequence)
        cast["segment_order"].append((sequence, seg_id))
        cast["sample_count"] += len(samples)
        if cast["sample_count"] > 10000:
            raise ValueError("a cast cannot contain more than 10000 samples across its segments")
        segment_ids.add(seg_id)

        previous = None
        for sample_index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError("each sample must be an object")
            sample_id = sample.get("id")
            if not isinstance(sample_id, str) or not sample_id or sample_id in sample_ids:
                raise ValueError("sample ids must be globally unique nonblank strings")
            sample_ids.add(sample_id)
            depth = _fraction(sample.get("depth_m"), "depth_m")
            temperature = _fraction(sample.get("temperature_c"), "temperature_c")
            salinity = _fraction(sample.get("salinity_psu"), "salinity_psu")
            if previous is not None and depth <= previous:
                raise ValueError("sample depths must be strictly increasing within a segment")
            previous = depth
            cast["depths"].setdefault(depth, []).append(
                (sequence, seg_id, sample_index, sample_id, depth, temperature, salinity, sample)
            )

    if not 1 <= len(casts) <= 100:
        raise ValueError("segments must reference 1..100 unique cast ids")

    # Validate every bottle only after the full segment structure is known, so
    # no business result can be produced before validation completes.
    parsed_bottles: list[tuple[str, str, Fraction]] = []
    bottle_ids: set[str] = set()
    for bottle in bottles:
        if not isinstance(bottle, dict):
            raise ValueError("each bottle must be an object")
        bottle_id = bottle.get("id")
        if not isinstance(bottle_id, str) or not bottle_id or bottle_id in bottle_ids:
            raise ValueError("bottle ids must be globally unique nonblank strings")
        bottle_ids.add(bottle_id)
        cast_id = bottle.get("cast_id")
        if not isinstance(cast_id, str) or not cast_id or cast_id not in casts:
            raise ValueError("bottle cast_id must reference a cast with at least one segment")
        depth = _fraction(bottle.get("depth_m"), "depth_m")
        parsed_bottles.append((bottle_id, cast_id, depth))

    return max_gap, casts, parsed_bottles


def _build_canonical_profile(cast_id: str, cast: dict) -> dict:
    segment_ids_sorted = [seg_id for _sequence, seg_id in sorted(cast["segment_order"])]
    profile: list[tuple[Fraction, Fraction, Fraction, str]] = []
    depth_values: list[Fraction] = []
    sample_segments: dict[str, str] = {}
    canonical_samples: list[dict] = []
    overlap_resolutions: list[dict] = []

    for depth in sorted(cast["depths"]):
        # sequence is unique inside a cast, so this order is independent of the
        # input array position of the segments.
        candidates = sorted(cast["depths"][depth], key=lambda item: item[0])
        sequence, seg_id, _index, sample_id, _depth, temperature, salinity, sample = candidates[-1]
        profile.append((depth, temperature, salinity, sample_id))
        depth_values.append(depth)
        sample_segments[sample_id] = seg_id
        # Rebuild the output row from the caller's original strings so exact
        # numeric text is never rewritten through Fraction.
        canonical_samples.append({
            "id": sample_id,
            "segment_id": seg_id,
            "depth_m": sample["depth_m"],
            "temperature_c": sample["temperature_c"],
            "salinity_psu": sample["salinity_psu"],
        })
        if len(candidates) > 1:
            overlap_resolutions.append({
                "depth_m": _text(depth),
                "selected_sample_id": sample_id,
                "selected_segment_id": seg_id,
                "discarded_sample_ids": [candidate[3] for candidate in candidates[:-1]],
            })

    return {
        "document": {
            "cast_id": cast_id,
            "segment_ids": segment_ids_sorted,
            "samples": canonical_samples,
            "overlap_resolutions": overlap_resolutions,
        },
        "profile": profile,
        "depth_values": depth_values,
        "sample_segments": sample_segments,
    }


def process_with_segments(data: dict) -> dict:
    # Full validation happens inside the parser; building and matching only
    # run once every segment, sample and bottle has been accepted.
    max_gap, casts, parsed_bottles = _parse_segments_input(data)

    canonical: dict[str, dict] = {}
    canonical_documents = []
    for cast_id in sorted(casts):
        built = _build_canonical_profile(cast_id, casts[cast_id])
        canonical[cast_id] = built
        canonical_documents.append(built["document"])

    bottle_rows = []
    counts = {"matched": 0, "outside_profile": 0, "gap_too_wide": 0}
    for bottle_id, cast_id, depth in parsed_bottles:
        built = canonical[cast_id]
        row, status = _match_profile(
            bottle_id,
            cast_id,
            depth,
            built["profile"],
            built["depth_values"],
            max_gap,
        )
        if status == "outside_profile":
            row["source_segment_ids"] = []
        else:
            source_segments: list[str] = []
            for source_sample_id in row["source_sample_ids"]:
                source_segment_id = built["sample_segments"][source_sample_id]
                if source_segment_id not in source_segments:
                    source_segments.append(source_segment_id)
            row["source_segment_ids"] = source_segments
        counts[status] += 1
        bottle_rows.append(row)

    bottle_rows.sort(key=lambda row: row["id"])
    return {
        "bottles": bottle_rows,
        "counts": counts,
        "canonical_profiles": canonical_documents,
    }
