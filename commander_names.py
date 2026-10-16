"""Match a journal commander name to one unknown ID."""

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

# History often writes the commander ID a few seconds after the journal
# names them. The short wait lets a second ID land before a name is saved.
_WINDOW_SECONDS = 120.0
_SETTLE_SECONDS = 10.0
# A later sighting is a new visit when the previous one ended more than
# this long ago. Refreshes while someone is still here update that visit.
_VISIT_GAP_SECONDS = 15 * 60
# Recent visits are enough to line a journal line up with an unknown ID.
# The first time seen stays on the row after older visits are dropped.
_MAX_ENCOUNTERS = 8
_OVERLAP_SLACK_SECONDS = 120.0

_CMDR_DECORATE = re.compile(r"\$cmdr_decorate:#name=([^;]*)", re.IGNORECASE)
_CMDR_PREFIX = re.compile(r"^cmdr\s+", re.IGNORECASE)
_CHAT_CHANNELS = {"local", "player", "voicechat"}
_SEND_SKIP = {"local", "wing", "squadron", "system", "starsystem", "voicechat", "friend", "npc", "player"}
_KIND_RANK = {
    "scan": 50,
    "wing": 40,
    "pvp": 40,
    "interdiction": 40,
    "chat": 10,
}


def is_unknown_name(name) -> bool:
    if not isinstance(name, str):
        return True
    text = name.strip()
    return text == "" or text.casefold() == "unknown"


def name_key(name: str) -> str:
    return name.strip().casefold()


def strip_cmdr_prefix(name: str) -> str:
    return _CMDR_PREFIX.sub("", name.strip(), count=1).strip()


def case_score(name: str) -> int:
    """How much capitalization a spelling carries beyond a single leading capital.

    All-lower and all-upper names score zero. A clearer spelling scores higher,
    including a capital after a space or punctuation mark.
    """
    if not isinstance(name, str):
        return 0
    letters = [char for char in name if char.isalpha()]
    if not any(char.isupper() for char in letters) or not any(char.islower() for char in letters):
        return 0
    score = 0
    seen_letter = False
    previous_upper = False
    for char in name:
        if not char.isalpha():
            continue
        if char.isupper() and seen_letter:
            score += 1 if previous_upper else 2
        seen_letter = True
        previous_upper = char.isupper()
    return score


_JOURNAL_NAME_TOKENS = (
    "ShipTargeted",
    "ReceiveText",
    "SendText",
    "PVPKill",
    "Interdicted",
    "EscapeInterdiction",
    "Interdiction",
    "WingAdd",
    "WingJoin",
)


def collect_journal_spellings(journal_dir: str) -> list[tuple[str, str]]:
    """Read player names from Elite journal logs in one folder."""
    if not isinstance(journal_dir, str) or not journal_dir or not os.path.isdir(journal_dir):
        return []
    found: list[tuple[str, str]] = []
    try:
        names = os.listdir(journal_dir)
    except OSError:
        return []
    for name in names:
        if not name.startswith("Journal.") or not name.endswith(".log"):
            continue
        path = os.path.join(journal_dir, name)
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if not any(token in line for token in _JOURNAL_NAME_TOKENS):
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                found.extend(journal_player_names(entry))
    return found


def plan_spelling_updates(records: dict, spellings: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """Choose clearer journal spellings for saved names.

    A row changes only when every saved copy of that name is spelled the same
    way and one journal spelling is a clearer form of those same letters.
    Unknown rows stay unknown. Two commanders who already use different
    capitalization of one name are left alone.
    """
    if not isinstance(records, dict):
        return []
    grouped: dict[str, list[tuple[str, str]]] = {}
    for cmdr_id, info in records.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name")
        if not isinstance(name, str) or is_unknown_name(name):
            continue
        cleaned = name.strip()
        if not cleaned:
            continue
        grouped.setdefault(name_key(cleaned), []).append((str(cmdr_id), cleaned))

    best: dict[str, dict[str, int]] = {}
    for kind, spelling in spellings or []:
        if not isinstance(spelling, str):
            continue
        cleaned = spelling.strip()
        if not cleaned or is_unknown_name(cleaned):
            continue
        rank = _KIND_RANK.get(kind, 0)
        current = best.setdefault(name_key(cleaned), {})
        if rank >= current.get(cleaned, -1):
            current[cleaned] = rank

    planned: list[tuple[str, str, str]] = []
    for key, rows in grouped.items():
        stored = {spelling for _cmdr_id, spelling in rows}
        if len(stored) != 1:
            continue
        current_name = next(iter(stored))
        current_score = case_score(current_name)
        improvements: dict[int, list[str]] = {}
        for spelling, rank in best.get(key, {}).items():
            # Chat can be typed in any case. A ship scan, wing name, kill,
            # or interdiction carries the capitalization the commander used.
            if rank <= _KIND_RANK["chat"]:
                continue
            if case_score(spelling) <= current_score or spelling == current_name:
                continue
            improvements.setdefault(rank, []).append(spelling)
        if not improvements:
            continue
        # A ship scan outranks chat. A louder chat spelling must not replace it.
        choices = improvements[max(improvements)]
        unique = list(dict.fromkeys(choices))
        if len(unique) != 1:
            continue
        new_name = unique[0]
        for cmdr_id, old_name in rows:
            planned.append((cmdr_id, old_name, new_name))
    planned.sort(key=lambda item: (item[1].casefold(), item[0]))
    return planned


def parse_seen_time(value) -> datetime | None:
    """Read a stored or journal timestamp as naive UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def format_seen_time(value) -> str | None:
    parsed = parse_seen_time(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def visit_is_new(previous_last_seen, seen_at) -> bool:
    previous = parse_seen_time(previous_last_seen)
    current = parse_seen_time(seen_at)
    if previous is None or current is None:
        return True
    return (current - previous).total_seconds() > _VISIT_GAP_SECONDS


def _valid_encounters(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        if parse_seen_time(start) is None:
            continue
        if parse_seen_time(end) is None:
            end = start
        cleaned.append({"start": start, "end": end})
    return cleaned


def backfill_seen_record(record: dict) -> bool:
    """Fill first_seen and one visit from last_seen. Leave a complete row alone.

    Older rows only know the latest sighting. That becomes a single visit
    at that time, not a span from the first day the file existed.
    """
    if not isinstance(record, dict):
        return False
    last = parse_seen_time(record.get("last_seen"))
    if last is None:
        return False
    changed = False
    if parse_seen_time(record.get("first_seen")) is None:
        record["first_seen"] = format_seen_time(last)
        changed = True
    encounters = _valid_encounters(record.get("encounters"))
    if len(encounters) > _MAX_ENCOUNTERS:
        encounters = encounters[-_MAX_ENCOUNTERS:]
    if not encounters:
        seen = format_seen_time(last)
        encounters = [{"start": record.get("first_seen") or seen, "end": record.get("last_seen") or seen}]
    if record.get("encounters") != encounters:
        record["encounters"] = encounters
        changed = True
    return changed


def note_seen_time(record: dict, seen_at, previous: dict | None, new_visit: bool) -> None:
    """Record this sighting on a row, keeping at most eight visits."""
    if not isinstance(record, dict):
        return
    when = parse_seen_time(seen_at)
    text = format_seen_time(when)
    if text is None:
        return
    record["last_seen"] = text

    first = None
    encounters: list[dict] = []
    if isinstance(previous, dict):
        first = parse_seen_time(previous.get("first_seen"))
        if first is None:
            first = parse_seen_time(previous.get("last_seen"))
        encounters = _valid_encounters(previous.get("encounters"))
    if first is None or when < first:
        first = when
    record["first_seen"] = format_seen_time(first)

    if new_visit or not encounters:
        encounters.append({"start": text, "end": text})
    else:
        end = parse_seen_time(encounters[-1].get("end"))
        if end is None or when >= end:
            encounters[-1]["end"] = text
        if parse_seen_time(encounters[-1].get("start")) is None:
            encounters[-1]["start"] = text
    if len(encounters) > _MAX_ENCOUNTERS:
        encounters = encounters[-_MAX_ENCOUNTERS:]
    record["encounters"] = encounters


def close_seen_time(record: dict, closed_at) -> bool:
    """Extend the open visit when someone leaves, if the leave is still that visit."""
    if not isinstance(record, dict):
        return False
    when = parse_seen_time(closed_at)
    text = format_seen_time(when)
    encounters = record.get("encounters")
    if text is None or not isinstance(encounters, list) or not encounters:
        return False
    last = encounters[-1]
    if not isinstance(last, dict):
        return False
    end = parse_seen_time(last.get("end"))
    if end is None:
        return False
    gap = (when - end).total_seconds()
    if gap < 0 or gap > _VISIT_GAP_SECONDS:
        return False
    if last.get("end") == text:
        return False
    last["end"] = text
    return True


def encounter_overlaps(record: dict, when, slack_seconds: float = _OVERLAP_SLACK_SECONDS) -> bool:
    """True when `when` falls inside one visit, plus a short margin.

    The margin is around each visit. It is not the span from first_seen to
    last_seen, so an old unknown does not match every later journal line.
    """
    moment = parse_seen_time(when)
    if moment is None or not isinstance(record, dict):
        return False
    windows = []
    for item in _valid_encounters(record.get("encounters")):
        start = parse_seen_time(item.get("start"))
        end = parse_seen_time(item.get("end")) or start
        if start is None:
            continue
        if end < start:
            start, end = end, start
        windows.append((start, end))
    if not windows:
        point = parse_seen_time(record.get("last_seen"))
        if point is None:
            return False
        windows.append((point, point))
    margin = timedelta(seconds=slack_seconds)
    for start, end in windows:
        if start - margin <= moment <= end + margin:
            return True
    return False


def overlapping_unknown_ids(records: dict, when, slack_seconds: float = _OVERLAP_SLACK_SECONDS) -> list[str]:
    if not isinstance(records, dict) or parse_seen_time(when) is None:
        return []
    found = []
    for cmdr_id, info in records.items():
        if not isinstance(info, dict) or not is_unknown_name(info.get("name")):
            continue
        if encounter_overlaps(info, when, slack_seconds):
            found.append(str(cmdr_id))
    return found


def candidate_unknown_ids(live_ids, records: dict, link_when) -> tuple[list[str], bool]:
    """Prefer commanders who are here. Otherwise use visits that overlap the journal time.

    The second value is True when the ids came from saved visits. Callers
    must not treat that set like a live instance.
    """
    live = [str(cid) for cid in (live_ids or [])]
    if live:
        return live, False
    if link_when is None:
        return [], False
    found = overlapping_unknown_ids(records, link_when)
    if not found:
        return [], False
    return found, True


def journal_player_names(entry: dict) -> list[tuple[str, str]]:
    """Return (kind, spelling) pairs for one journal event.

    NPC scans, the friends list, and squadron chat are left out. Those
    records are not the commander in the current instance.
    """
    if not isinstance(entry, dict):
        return []
    event = entry.get("event")
    found: list[tuple[str, str]] = []

    if event == "ShipTargeted":
        name = _ship_targeted_cmdr(entry)
        if name:
            found.append(("scan", name))
    elif event == "ReceiveText":
        channel = str(entry.get("Channel") or "").strip().lower()
        if channel in _CHAT_CHANNELS:
            _append_name(found, "chat", entry.get("From_Localised") or entry.get("From"))
    elif event == "SendText":
        target = entry.get("To_Localised") or entry.get("To")
        if isinstance(target, str) and target.strip().lower() not in _SEND_SKIP:
            _append_name(found, "chat", target)
    elif event == "PVPKill":
        _append_name(found, "pvp", entry.get("Victim_Localised") or entry.get("Victim"))
    elif event in ("Interdicted", "EscapeInterdiction"):
        if entry.get("IsPlayer") is True:
            _append_name(found, "interdiction", entry.get("Interdictor"))
    elif event == "Interdiction":
        if entry.get("IsPlayer") is True:
            _append_name(found, "interdiction", entry.get("Interdicted") or entry.get("Interdictor"))
    elif event in ("WingAdd", "WingJoin"):
        _append_name(found, "wing", entry.get("Name"))
    return found


def _append_name(found: list[tuple[str, str]], kind: str, raw) -> None:
    if not isinstance(raw, str):
        return
    name = strip_cmdr_prefix(raw)
    if name and not is_unknown_name(name):
        found.append((kind, name))


def _ship_targeted_cmdr(entry: dict) -> str | None:
    raw = str(entry.get("PilotName") or "")
    match = _CMDR_DECORATE.search(raw)
    if match:
        name = strip_cmdr_prefix(match.group(1))
        return name or None
    if "$npc_" in raw.lower():
        return None
    localised = entry.get("PilotName_Localised")
    if isinstance(localised, str) and _CMDR_PREFIX.match(localised.strip()):
        name = strip_cmdr_prefix(localised)
        return name or None
    return None


class _PendingName:
    def __init__(self, name: str, noted_at: float, origin_ids: frozenset[str], rank: int, journal_at: datetime | None, kind: str):
        self.name = name
        self.noted_at = noted_at
        self.origin_ids = origin_ids
        self.rank = rank
        self.journal_at = journal_at
        self.kinds = {kind} if kind else set()
        self.ambiguous = len(origin_ids) > 1
        self.waiting_id: str | None = None


class UnknownNameResolver:
    def __init__(self, window_seconds: float = _WINDOW_SECONDS, settle_seconds: float = _SETTLE_SECONDS):
        self.window_seconds = window_seconds
        self.settle_seconds = settle_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingName] = {}
        self._unknown_since: dict[str, float] = {}
        self._pvp_victim: tuple[str, float] | None = None
        self._pvp_ambiguous = False
        self._wing_name: tuple[str, float] | None = None
        self._wing_ambiguous = False

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self._unknown_since.clear()
            self._pvp_victim = None
            self._pvp_ambiguous = False
            self._wing_name = None
            self._wing_ambiguous = False

    def note_journal(self, entry: dict, now: float, unknown_ids: set[str], known_names: set[str], allow_kill: bool = True) -> None:
        journal_at = parse_seen_time(entry.get("timestamp")) if isinstance(entry, dict) else None
        with self._lock:
            if not allow_kill:
                self._pvp_victim = None
                self._pvp_ambiguous = False
            for kind, name in journal_player_names(entry):
                if kind == "pvp" and not allow_kill:
                    continue
                self._note(kind, name, now, set(unknown_ids), known_names, journal_at)

    def link_time(self) -> datetime | None:
        """Journal time for the one pending name that can be matched to a saved visit."""
        with self._lock:
            stamped = [
                note.journal_at
                for note in self._pending.values()
                if not note.ambiguous and note.journal_at is not None
            ]
            if len(stamped) != 1:
                return None
            return stamped[0]

    def resolve(
        self,
        unknown_ids,
        now: float,
        known_names: set[str],
        killed_unknown_ids=None,
        new_wing_unknown_ids=None,
        allow_kill: bool = True,
        historical: bool = False,
    ) -> list[tuple[str, str]]:
        with self._lock:
            if not allow_kill:
                self._pvp_victim = None
                self._pvp_ambiguous = False
                killed_unknown_ids = None
            return self._resolve(
                {str(cid) for cid in unknown_ids},
                now,
                known_names,
                [str(cid) for cid in (killed_unknown_ids or [])],
                [str(cid) for cid in (new_wing_unknown_ids or [])],
                allow_kill,
                historical,
            )

    def _note(self, kind: str, name: str, now: float, unknown_ids: set[str], known_names: set[str], journal_at: datetime | None) -> None:
        cleaned = name.strip()
        if not cleaned or is_unknown_name(cleaned):
            return
        key = name_key(cleaned)
        if key in known_names:
            return
        rank = _KIND_RANK.get(kind, 0)
        if kind == "pvp":
            self._remember_role("_pvp_victim", "_pvp_ambiguous", cleaned, now)
        elif kind == "wing":
            self._remember_role("_wing_name", "_wing_ambiguous", cleaned, now)

        existing = self._pending.get(key)
        if existing is not None:
            # Keep the first time we heard the name. Later scan lines of the
            # same target must not push the settle window back.
            existing.kinds.add(kind)
            if existing.journal_at is None and journal_at is not None:
                existing.journal_at = journal_at
            if rank >= existing.rank:
                existing.name = cleaned
                existing.rank = rank
        else:
            self._pending[key] = _PendingName(
                cleaned,
                now,
                frozenset(str(cid) for cid in unknown_ids),
                rank,
                journal_at,
                kind,
            )
        self._mark_competing_empty_origins()

    def _remember_role(self, value_attr: str, ambiguous_attr: str, name: str, now: float) -> None:
        current = getattr(self, value_attr)
        if current is not None and name_key(current[0]) != name_key(name):
            setattr(self, ambiguous_attr, True)
        setattr(self, value_attr, (name, now))

    def _mark_competing_empty_origins(self) -> None:
        empty = [note for note in self._pending.values() if not note.origin_ids and not note.ambiguous]
        if len(empty) > 1:
            for note in empty:
                note.ambiguous = True

    def _resolve(
        self,
        unknown_ids: set[str],
        now: float,
        known_names: set[str],
        killed_ids: list[str],
        wing_ids: list[str],
        allow_kill: bool,
        historical: bool,
    ) -> list[tuple[str, str]]:
        self._expire(now, known_names)
        # Saved visits are a fallback for a journal line. They must not mark
        # a name ambiguous or start the live settle clock.
        if not historical:
            for cid in list(self._unknown_since):
                if cid not in unknown_ids:
                    self._unknown_since.pop(cid, None)
            for cid in unknown_ids:
                self._unknown_since.setdefault(cid, now)

        assignments: list[tuple[str, str]] = []
        used_names: set[str] = set()
        used_ids: set[str] = set()

        pvp_name = self._role_name(self._pvp_victim, self._pvp_ambiguous, now, known_names)
        if pvp_name:
            killed = self._unique(killed_ids)
            if len(killed) == 1:
                assignments.append((killed[0], pvp_name))
                used_names.add(name_key(pvp_name))
                used_ids.add(killed[0])
                self._consume(pvp_name)
                self._pvp_victim = None
                self._pvp_ambiguous = False

        wing_name = self._role_name(self._wing_name, self._wing_ambiguous, now, known_names)
        if wing_name and name_key(wing_name) not in used_names:
            winged = [cid for cid in self._unique(wing_ids) if cid not in used_ids]
            if len(winged) == 1:
                assignments.append((winged[0], wing_name))
                used_names.add(name_key(wing_name))
                used_ids.add(winged[0])
                self._consume(wing_name)
                self._wing_name = None
                self._wing_ambiguous = False

        if not historical:
            self._mark_newcomers(unknown_ids)
            self._mark_waiting(unknown_ids)
        remaining = [cid for cid in unknown_ids if cid not in used_ids]
        if len(remaining) != 1:
            return assignments
        target = remaining[0]
        compatible = [
            note for note in self._pending.values()
            if self._compatible(note, target, now, known_names, used_names, allow_kill)
        ]
        if len(compatible) > 1:
            if not historical:
                for note in compatible:
                    note.ambiguous = True
            return assignments
        if len(compatible) != 1:
            return assignments
        note = compatible[0]
        if note.origin_ids or historical:
            ready = now - note.noted_at >= self.settle_seconds
        else:
            appeared = self._unknown_since.get(target, now)
            ready = appeared >= note.noted_at and now - appeared >= self.settle_seconds
        if ready:
            assignments.append((target, note.name))
            self._consume(note.name)
        return assignments

    def _mark_newcomers(self, unknown_ids: set[str]) -> None:
        for note in self._pending.values():
            if note.ambiguous or not note.origin_ids:
                continue
            if unknown_ids - note.origin_ids:
                note.ambiguous = True

    def _compatible(self, note: _PendingName, target: str, now: float, known_names: set[str], used_names: set[str], allow_kill: bool) -> bool:
        if note.ambiguous or name_key(note.name) in known_names or name_key(note.name) in used_names:
            return False
        if not allow_kill and note.kinds and note.kinds <= {"pvp"}:
            return False
        if now - note.noted_at > self.window_seconds:
            return False
        if note.origin_ids:
            return note.origin_ids == frozenset({target})
        return note.waiting_id in (None, target)

    def _mark_waiting(self, unknown_ids: set[str]) -> None:
        for note in self._pending.values():
            if note.ambiguous or note.origin_ids:
                continue
            if len(unknown_ids) > 1:
                note.ambiguous = True
            elif len(unknown_ids) == 1:
                sole = next(iter(unknown_ids))
                if note.waiting_id is None:
                    note.waiting_id = sole
                elif note.waiting_id != sole:
                    note.ambiguous = True
            elif note.waiting_id and note.waiting_id not in unknown_ids:
                note.ambiguous = True

    def _role_name(self, role: tuple[str, float] | None, ambiguous: bool, now: float, known_names: set[str]) -> str | None:
        if role is None or ambiguous:
            return None
        name, noted_at = role
        if now - noted_at > self.window_seconds or name_key(name) in known_names:
            return None
        return name

    def _unique(self, ids: list[str]) -> list[str]:
        seen: list[str] = []
        for cid in ids:
            if cid not in seen:
                seen.append(cid)
        return seen

    def _consume(self, name: str) -> None:
        self._pending.pop(name_key(name), None)

    def _expire(self, now: float, known_names: set[str]) -> None:
        for key, note in list(self._pending.items()):
            if now - note.noted_at > self.window_seconds or name_key(note.name) in known_names:
                self._pending.pop(key, None)
        if self._pvp_victim and self._role_name(self._pvp_victim, False, now, known_names) is None:
            self._pvp_victim = None
            self._pvp_ambiguous = False
        if self._wing_name and self._role_name(self._wing_name, False, now, known_names) is None:
            self._wing_name = None
            self._wing_ambiguous = False
