use crate::{Record, WindowRow};

// Constant stub: the gate must reject this file until a worker replaces it.
pub fn window_stats(records: &[Record], width_ms: i64) -> Vec<WindowRow> {
    let _ = (records, width_ms);
    Vec::new()
}
