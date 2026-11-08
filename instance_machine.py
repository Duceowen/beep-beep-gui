"""Per-instance announcement state.

A generation starts at each real instance change (drop, glide, low-wake into
supercruise, jump, respawn, relog). Within one generation a commander is
announced at most once. A CommanderHistory refresh is not a new arrival.

Strong journal evidence (ship lock, ship scan, local chat, interdiction)
confirms someone immediately. That path does not wait for the history file
or for a grace timer.

Worst-case plugin delay, after the triggering input is already in hand:

- Journal evidence: none. The beep is decided inside that journal call.
- Interdiction counterpart: none beyond the SupercruiseExit call that lands
  the drop. The beep is part of that call.
- History row that arrived during the jump (StartJump already seen): none
  beyond the SupercruiseEntry, SupercruiseExit, or FSDJump call. The staged
  row is announced there, not on a later poll.
- History-only new commander, no journal event in between: one history poll.
  The worker checks the file every HISTORY_POLL_SECONDS (50 ms). A journal
  call also checks the file before it returns, so a file that is already
  updated is applied in that call.

The game itself can wait many seconds before it writes CommanderHistory.
That wait is not a plugin timer. This machine adds no grace delay in front
of a commander who is new to the instance.
"""
import time

from commander_names import confusable_key, is_unknown_name, name_key

# How often the worker looks for a new CommanderHistory file.
# Also the worst-case wait for a history-only arrival when no journal line
# arrives to force an immediate read.
HISTORY_POLL_SECONDS = 0.05

# FSD cooldown after an interdiction. Success (the minigame was lost, or the
# outgoing interdiction reports Success) is 60 s. Submitting is 10 s.
# Outgoing Interdiction events do not say whether the target submitted, so
# Success uses 60 s. See the 1.1.6 notes for that ambiguity.
INTERDICTION_SUCCESS_COOLDOWN = 60.0
INTERDICTION_SUBMIT_COOLDOWN = 10.0


class InstanceMachine:
    def __init__(self):
        self.generation = 0
        self.pending = False
        self.staged = []
        self.seen_ids = set()
        self.departure_ids = set()
        self.announced = set()
        self.confirmed = set()
        self.killed = set()
        self.by_name = {}
        self.by_confusable = {}
        self.held = {}
        self.held_confusable = {}
        self.announced_names = set()
        self.cooldown_until = {}
        self.relog_open = False
        self.present_before_jump = set()

    def note_pending(self, present_ids=None) -> None:
        """StartJump: history that arrives before the jump completes is staged."""
        self.pending = True
        self.present_before_jump = set(self.confirmed) | set(self.announced)
        if present_ids:
            self.present_before_jump.update(str(cid) for cid in present_ids)

    def stage(self, cmdr_id) -> None:
        cid = str(cmdr_id)
        if cid not in self.staged:
            self.staged.append(cid)

    def commit(self, departure_ids, relog: bool = False):
        """Open a generation. Return ids staged during the jump that are new."""
        staged = [str(cid) for cid in self.staged]
        before = set() if relog else {str(cid) for cid in self.present_before_jump}
        self.staged = []
        self.pending = False
        self.generation += 1
        self.announced = set()
        self.confirmed = set()
        self.killed = set()
        self.announced_names = set()
        self.relog_open = bool(relog)
        if relog:
            self.departure_ids = set()
        else:
            # Someone touched only because their row arrived during the jump is
            # not a person you left behind. The visit list records that touch.
            self.departure_ids = {
                str(cid) for cid in departure_ids
                if str(cid) not in staged or str(cid) in before
            }
            self.seen_ids |= self.departure_ids
        known = set(self.seen_ids)
        fresh = []
        for cid in staged:
            self.seen_ids.add(cid)
            if cid in before or cid in self.departure_ids:
                continue
            self._mark_announced(cid)
            fresh.append(cid)
        return fresh

    def relog(self) -> None:
        self.commit(set(), relog=True)

    def suppresses(self, cmdr_id) -> bool:
        """True when a history row is a known contact, not a new arrival."""
        cid = str(cmdr_id)
        if cid in self.confirmed or cid in self.announced:
            return False
        if self.relog_open:
            return False
        return cid in self.departure_ids

    def note_announced(self, cmdr_id) -> None:
        cid = str(cmdr_id)
        self.seen_ids.add(cid)
        self._mark_announced(cid)

    def _mark_announced(self, cid: str) -> None:
        self.announced.add(cid)
        self.confirmed.add(cid)
        self.departure_ids.discard(cid)

    def confirm(self, cmdr_id, kind: str):
        """Strong evidence. Returns (beep, reason). Already-announced stays quiet."""
        cid = str(cmdr_id)
        if cid in self.killed:
            return False, "kill"
        self.seen_ids.add(cid)
        self.departure_ids.discard(cid)
        self.confirmed.add(cid)
        if cid in self.announced:
            return False, "refresh"
        self.announced.add(cid)
        return True, kind

    def note_killed(self, cmdr_id) -> None:
        """The kill row itself is not an arrival. A later return can still be announced."""
        cid = str(cmdr_id)
        self.killed.add(cid)
        self.confirmed.discard(cid)

    def index_name(self, cmdr_id, name) -> None:
        """Remember a display name for an id. Does not announce."""
        if not isinstance(name, str) or is_unknown_name(name):
            return
        cid = str(cmdr_id)
        key = name_key(name)
        conf = confusable_key(name)
        self.by_name.setdefault(key, set()).add(cid)
        self.by_confusable.setdefault(conf, set()).add(cid)

    def bind_name(self, cmdr_id, name):
        """Index a name and promote the id if journal evidence was waiting on it.

        Returns (beep, reason) or None.
        """
        self.index_name(cmdr_id, name)
        if not isinstance(name, str) or is_unknown_name(name):
            return None
        key = name_key(name)
        conf = confusable_key(name)
        held_kind = self.held.pop(key, None)
        if held_kind is None and self._single(self.by_confusable.get(conf)):
            held_kind = self.held_confusable.pop(conf, None)
        if held_kind is None:
            return None
        cid = str(cmdr_id)
        if cid in self.announced or self.name_was_announced(name):
            self.announced.add(cid)
            self.confirmed.add(cid)
            self.departure_ids.discard(cid)
            return False, "refresh"
        beep, reason = self.confirm(cid, held_kind)
        return beep, reason

    def hold_evidence(self, name, kind) -> None:
        if not isinstance(name, str) or is_unknown_name(name):
            return
        key = name_key(name)
        conf = confusable_key(name)
        self.held[key] = kind
        self.held_confusable[conf] = kind

    def name_was_announced(self, name) -> bool:
        if not isinstance(name, str):
            return False
        return name_key(name) in self.announced_names

    def mark_name_announced(self, name) -> None:
        if isinstance(name, str) and not is_unknown_name(name):
            self.announced_names.add(name_key(name))

    def resolve(self, name):
        """One history id for this name, or None when it is unknown or ambiguous."""
        if not isinstance(name, str) or is_unknown_name(name):
            return None
        exact = self._single(self.by_name.get(name_key(name)))
        if exact is not None:
            return exact
        if self.by_name.get(name_key(name)):
            return None
        return self._single(self.by_confusable.get(confusable_key(name)))

    def note_cooldown(self, cmdr_id, seconds: float, now: float | None = None) -> None:
        if cmdr_id is None:
            return
        moment = time.time() if now is None else now
        self.cooldown_until[str(cmdr_id)] = moment + float(seconds)

    def cooldown_remaining(self, cmdr_id, now: float | None = None):
        until = self.cooldown_until.get(str(cmdr_id))
        if until is None:
            return None
        moment = time.time() if now is None else now
        return max(0.0, until - moment)

    @staticmethod
    def _single(ids):
        if not ids or len(ids) != 1:
            return None
        return next(iter(ids))
