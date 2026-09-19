"""P3: deduplicate, then summarize. The migrated path calls the Rust P1 and P2."""

from __future__ import annotations

from .dedupe_latest import dedupe_latest
from .window_stats import window_stats


def summarize(records, width_ms):
    return window_stats(dedupe_latest(records), width_ms)
