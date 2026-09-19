def bucket(ts: int, width: int) -> int:
    return ts // width * width


def offset(ts: int, width: int) -> int:
    return ts % width


def split(ts: int, width: int) -> list:
    return [bucket(ts, width), offset(ts, width)]
