"""Interpret EDMC Status.json flags for the hide-from-mouse auto rule."""

FLAG_IN_MAIN_SHIP = 1 << 24
FLAGS2_ON_FOOT = 1 << 0
FLAGS2_IN_TAXI = 1 << 1
FLAGS2_IN_MULTICREW = 1 << 2


def on_foot_from_status(flags, flags2):
    """True/False when Status.json decides, None to keep the journal-based value.

    EDMC's OnFoot state can stay set after boarding a shuttle because no Embark
    event is written. InTaxi and InMulticrew clear it immediately in Status.json.
    """
    try:
        flags = int(flags)
        flags2 = int(flags2)
    except (TypeError, ValueError):
        return None
    if flags2 & (FLAGS2_IN_TAXI | FLAGS2_IN_MULTICREW):
        return False
    if flags2 & FLAGS2_ON_FOOT:
        return True
    if flags & FLAG_IN_MAIN_SHIP:
        return False
    return None
