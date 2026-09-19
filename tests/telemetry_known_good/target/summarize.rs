use crate::{Record, WindowRow};

pub fn summarize(records: &[Record], width_ms: i64) -> Vec<WindowRow> {
    crate::window_stats(&crate::dedupe_latest(records), width_ms)
}
