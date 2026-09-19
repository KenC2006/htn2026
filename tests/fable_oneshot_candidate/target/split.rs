pub fn split(ts: i64, width: i64) -> (i64, i64) {
    (
        crate::bucket::bucket(ts, width),
        crate::offset::offset(ts, width),
    )
}
