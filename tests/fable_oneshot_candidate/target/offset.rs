pub fn offset(ts: i64, width: i64) -> i64 {
    // For width > 0, Python % yields a result in [0, width), same as rem_euclid.
    ts.rem_euclid(width)
}
