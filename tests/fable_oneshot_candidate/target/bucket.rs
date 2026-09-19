pub fn bucket(ts: i64, width: i64) -> i64 {
    // Python floor division for width > 0: ts // width * width == ts - (ts mod width)
    ts.wrapping_sub(ts.rem_euclid(width))
}
