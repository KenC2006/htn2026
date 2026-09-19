use crate::{Record, WindowRow};
use std::collections::HashMap;

pub fn window_stats(records: &[Record], width_ms: i64) -> Vec<WindowRow> {
    let mut acc: HashMap<(&str, i64), (i64, i64, i64, i64)> = HashMap::new();
    for (sensor_id, timestamp_ms, _sequence, value_milli) in records {
        // div_euclid is the floor quotient for width_ms >= 1 and therefore
        // equals Python //: (-1).div_euclid(1000) * 1000 == -1000.
        let bucket = timestamp_ms.div_euclid(width_ms) * width_ms;
        acc.entry((sensor_id.as_str(), bucket))
            .and_modify(|row| {
                row.0 += 1;
                row.1 += *value_milli;
                row.2 = row.2.min(*value_milli);
                row.3 = row.3.max(*value_milli);
            })
            .or_insert((1, *value_milli, *value_milli, *value_milli));
    }
    let mut out: Vec<WindowRow> = acc
        .into_iter()
        .map(|((sensor_id, bucket), (count, sum, lo, hi))| {
            (sensor_id.to_string(), bucket, count, sum, lo, hi)
        })
        .collect();
    out.sort_by(|a, b| (a.0.as_str(), a.1).cmp(&(b.0.as_str(), b.1)));
    out
}
