// Frozen harness. Agents only ever write files under target/.
#![forbid(unsafe_code)]
#![allow(dead_code, unused_imports, unused_variables)]
mod json;
use json::{FromJ, ToJ};
use std::io::Read;

#[path = "../target/dedupe_latest.rs"]
pub mod dedupe_latest;
#[path = "../target/window_stats.rs"]
pub mod window_stats;
#[path = "../target/summarize.rs"]
pub mod summarize;

/// (sensor_id, timestamp_ms, sequence, value_milli)
pub type Record = (String, i64, i64, i64);
/// (sensor_id, bucket_start_ms, count, sum_milli, min_milli, max_milli)
pub type WindowRow = (String, i64, i64, i64, i64, i64);

pub use dedupe_latest::dedupe_latest;
pub use summarize::summarize;
pub use window_stats::window_stats;

fn main() {
    let mut text = String::new();
    std::io::stdin().read_to_string(&mut text).expect("stdin");
    let request = json::parse(&text);
    let export = String::from_j(request.get("export"));
    let input = request.get("input");
    let records = <Vec<Record>>::from_j(input.get("records"));
    let rows: String = match export.as_str() {
        "dedupe_latest" => dedupe_latest(&records).to_j(),
        "window_stats" => window_stats(&records, i64::from_j(input.get("width_ms"))).to_j(),
        "summarize" => summarize(&records, i64::from_j(input.get("width_ms"))).to_j(),
        other => panic!("unknown export {}", other),
    };
    println!("{{\"ok\": {}}}", rows);
}
