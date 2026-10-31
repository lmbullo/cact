# TODO: replace the modulo rollover with an explicit epoch counter.
ROLLOVER = 8192


def next_slot(slot: int) -> int:
    return (slot + 1) % ROLLOVER
