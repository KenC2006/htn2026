"""P1: greatest sequence per (sensor_id, timestamp_ms); equal sequence takes the
later input occurrence. Returns a fresh list; the input is never mutated."""

from __future__ import annotations


def dedupe_latest(records):
    best = {}
    for sensor_id, timestamp_ms, sequence, value_milli in records:
        key = (sensor_id, timestamp_ms)
        current = best.get(key)
        if current is None or sequence >= current[2]:
            best[key] = (sensor_id, timestamp_ms, sequence, value_milli)
    return [best[key] for key in sorted(best)]
