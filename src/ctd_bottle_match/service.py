from __future__ import annotations

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


def process(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    casts = data.get("casts")
    bottles = data.get("bottles")
    max_gap = _fraction(data.get("max_gap_m"), "max_gap_m")
    if not isinstance(casts, list) or not 1 <= len(casts) <= 100:
        raise ValueError("casts must contain 1..100 items")
    if not isinstance(bottles, list) or not 1 <= len(bottles) <= 5000:
        raise ValueError("bottles must contain 1..5000 items")

    profiles: dict[str, list[tuple[Fraction, Fraction, Fraction, str]]] = {}
    sample_ids: set[str] = set()
    for cast in casts:
        cast_id = cast.get("id") if isinstance(cast, dict) else None
        samples = cast.get("samples") if isinstance(cast, dict) else None
        if not isinstance(cast_id, str) or not cast_id or cast_id in profiles:
            raise ValueError("cast ids must be unique nonblank strings")
        if not isinstance(samples, list) or not 2 <= len(samples) <= 10000:
            raise ValueError("each cast needs 2..10000 samples")
        profile = []
        previous = None
        for sample in samples:
            sid = sample.get("id") if isinstance(sample, dict) else None
            if not isinstance(sid, str) or not sid or sid in sample_ids:
                raise ValueError("sample ids must be globally unique")
            depth = _fraction(sample.get("depth_m"), "depth_m")
            temperature = _fraction(sample.get("temperature_c"), "temperature_c")
            salinity = _fraction(sample.get("salinity_psu"), "salinity_psu")
            if previous is not None and depth <= previous:
                raise ValueError("sample depths must be strictly increasing")
            previous = depth
            sample_ids.add(sid)
            profile.append((depth, temperature, salinity, sid))
        profiles[cast_id] = profile

    bottle_ids: set[str] = set()
    results = []
    counts = {"matched": 0, "outside_profile": 0, "gap_too_wide": 0}
    for bottle in bottles:
        bottle_id = bottle.get("id") if isinstance(bottle, dict) else None
        cast_id = bottle.get("cast_id") if isinstance(bottle, dict) else None
        if not isinstance(bottle_id, str) or not bottle_id or bottle_id in bottle_ids:
            raise ValueError("bottle ids must be unique nonblank strings")
        if cast_id not in profiles:
            raise ValueError("unknown cast_id")
        bottle_ids.add(bottle_id)
        depth = _fraction(bottle.get("depth_m"), "depth_m")
        profile = profiles[cast_id]
        row = {"id": bottle_id, "cast_id": cast_id, "depth_m": _text(depth)}
        if depth < profile[0][0] or depth > profile[-1][0]:
            row["status"] = "outside_profile"
        else:
            lower = profile[0]
            upper = profile[-1]
            for sample in profile:
                if sample[0] <= depth:
                    lower = sample
                if sample[0] >= depth:
                    upper = sample
                    break
            gap = upper[0] - lower[0]
            if gap > max_gap:
                row.update({"status": "gap_too_wide", "source_sample_ids": [lower[3], upper[3]], "gap_m": _text(gap)})
            else:
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
        counts[row["status"]] += 1
        results.append(row)
    return {"bottles": sorted(results, key=lambda item: item["id"]), "counts": counts}

