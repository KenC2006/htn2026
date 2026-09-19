"""P2: count, sum, min and max per (sensor_id, bucket_start_ms) over every record
including duplicates. bucket_start_ms = (timestamp_ms // width_ms) * width_ms."""

from __future__ import annotations


def window_stats(records, width_ms):
    acc = {}
    for sensor_id, timestamp_ms, _sequence, value_milli in records:
        key = (sensor_id, (timestamp_ms // width_ms) * width_ms)
        row = acc.get(key)
        if row is None:
            acc[key] = [1, value_milli, value_milli, value_milli]
        else:
            row[0] += 1
            row[1] += value_milli
            if value_milli < row[2]:
                row[2] = value_milli
            if value_milli > row[3]:
                row[3] = value_milli
    return [(s, b, c, t, lo, hi) for (s, b), (c, t, lo, hi) in sorted(acc.items())]
