use crate::Record;
use std::collections::HashMap;

pub fn dedupe_latest(records: &[Record]) -> Vec<Record> {
    let mut best: HashMap<(&str, i64), &Record> = HashMap::new();
    for rec in records {
        // and_modify replaces on >=, so an equal sequence takes the later
        // input occurrence, matching the Python oracle.
        best.entry((rec.0.as_str(), rec.1))
            .and_modify(|prev| {
                if rec.2 >= prev.2 {
                    *prev = rec;
                }
            })
            .or_insert(rec);
    }
    let mut out: Vec<Record> = best.into_values().cloned().collect();
    out.sort_by(|a, b| (a.0.as_str(), a.1).cmp(&(b.0.as_str(), b.1)));
    out
}
