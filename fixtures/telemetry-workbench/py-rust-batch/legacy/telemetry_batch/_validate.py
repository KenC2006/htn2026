"""Frozen domain validation (plan section 6, master architecture section 6.2).

Exception args are exactly (code, index); index is the record index, or -1 for a
container or width failure. Only type(e).__name__ and e.args are compared, never
interpreter wording. `type(x) is int` is what excludes bool from integer fields.
"""

from __future__ import annotations

import re

MAX_RECORDS = 10_000
SENSOR_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
TS_MIN, TS_MAX = -10**12, 10**12
SEQ_MIN, SEQ_MAX = 0, 2**31 - 1
VALUE_MIN, VALUE_MAX = -(10**9), 10**9
WIDTH_MIN, WIDTH_MAX = 1, 86_400_000


def validate_width(width_ms):
    if type(width_ms) is not int:
        raise TypeError("E_WIDTH_TYPE", -1)
    if not WIDTH_MIN <= width_ms <= WIDTH_MAX:
        raise ValueError("E_WIDTH_RANGE", -1)


def _container(records):
    if type(records) is not list:
        raise TypeError("E_RECORDS_TYPE", -1)
    if len(records) > MAX_RECORDS:
        raise ValueError("E_RECORDS_COUNT", -1)


def _items(records):
    for index, record in enumerate(records):
        if type(record) is not tuple:
            raise TypeError("E_RECORD_TYPE", index)
        if len(record) != 4:
            raise TypeError("E_RECORD_ARITY", index)
        sensor_id, timestamp_ms, sequence, value_milli = record
        if type(sensor_id) is not str:
            raise TypeError("E_SENSOR_TYPE", index)
        if SENSOR_RE.fullmatch(sensor_id) is None:
            raise ValueError("E_SENSOR_RANGE", index)
        if type(timestamp_ms) is not int:
            raise TypeError("E_TIMESTAMP_TYPE", index)
        if not TS_MIN <= timestamp_ms <= TS_MAX:
            raise ValueError("E_TIMESTAMP_RANGE", index)
        if type(sequence) is not int:
            raise TypeError("E_SEQUENCE_TYPE", index)
        if not SEQ_MIN <= sequence <= SEQ_MAX:
            raise ValueError("E_SEQUENCE_RANGE", index)
        if type(value_milli) is not int:
            raise TypeError("E_VALUE_TYPE", index)
        if not VALUE_MIN <= value_milli <= VALUE_MAX:
            raise ValueError("E_VALUE_RANGE", index)


def validate_records(records):
    _container(records)
    _items(records)


def validate_batch(records, width_ms):
    _container(records)
    validate_width(width_ms)
    _items(records)
