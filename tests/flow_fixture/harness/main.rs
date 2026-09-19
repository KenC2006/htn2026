// Frozen harness. Workers only ever write files under target/.
#![forbid(unsafe_code)]
#![allow(dead_code)]
#[path = "../target/bucket.rs"]
mod bucket;
#[path = "../target/offset.rs"]
mod offset;
#[path = "../target/split.rs"]
mod split;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let ts: i64 = args[1].parse().expect("ts");
    let width: i64 = args[2].parse().expect("width");
    match args[0].as_str() {
        "bucket" => println!("{}", bucket::bucket(ts, width)),
        "offset" => println!("{}", offset::offset(ts, width)),
        "split" => {
            let (b, o) = split::split(ts, width);
            println!("[{}, {}]", b, o)
        }
        other => panic!("unknown export {other}"),
    }
}
