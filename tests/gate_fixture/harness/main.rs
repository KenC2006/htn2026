// Frozen harness. Workers only ever write target/bucket.rs.
#![forbid(unsafe_code)]
#[path = "../target/bucket.rs"]
mod bucket;

fn main() {
    let a: Vec<i64> = std::env::args().skip(1).map(|s| s.parse().expect("int")).collect();
    println!("{}", bucket::bucket(a[0], a[1]));
}
