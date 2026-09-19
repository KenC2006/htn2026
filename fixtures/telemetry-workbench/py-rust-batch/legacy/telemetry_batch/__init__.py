"""Public API of the telemetry-workbench example: every call is validated, then handed to the function.

The three functions and `_validate.py` are Aidan's originals, unchanged. This file is the plain version of
his facade: the PyO3 dispatch is gone because Parity compares the Rust through its own harness.
"""

from __future__ import annotations

from ._validate import validate_batch, validate_records
from .dedupe_latest import dedupe_latest as _dedupe_latest
from .summarize import summarize as _summarize
from .window_stats import window_stats as _window_stats

__all__ = ["dedupe_latest", "summarize", "window_stats"]


def dedupe_latest(records):
    validate_records(records)
    return _dedupe_latest(records)


def window_stats(records, width_ms):
    validate_batch(records, width_ms)
    return _window_stats(records, width_ms)


def summarize(records, width_ms):
    validate_batch(records, width_ms)
    return _summarize(records, width_ms)
