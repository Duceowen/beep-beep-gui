"""Session recorder: JSONL shape, size cap, and no change to beep decisions."""
import datetime
import json
import os
import tempfile
import unittest

import test_scenarios  # stubs EDMC modules, then imports the plugin
from test_scenarios import add_row, feed, fresh_history, load_mod, location, present
from session_recorder import SessionRecorder, compact_journal


def _lines(folder):
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    found.append(json.loads(line))
    return found


def _folder_size(folder):
    total = 0
    for name in os.listdir(folder):
        total += os.path.getsize(os.path.join(folder, name))
    return total


class RecorderFile(unittest.TestCase):
    def test_lines_are_json_and_history_diff_is_compact(self):
        folder = tempfile.mkdtemp()
        rec = SessionRecorder(folder, max_total_bytes=50 * 1024 * 1024)
        rec.enabled_override = True
        rec.record_history([
            {"id": "1", "epoch": 10, "interactions": ["Met"], "name": "Ada"},
            {"id": "2", "epoch": 11, "interactions": ["Met", "WingMember"]},
        ], 100.0)
        rec.record_history([
            {"id": "2", "epoch": 12, "interactions": ["Met", "WingMember"], "name": "Bea"},
            {"id": "3", "epoch": 13, "interactions": ["Met"]},
        ], 200.5)
        rec.record("decision", {"beep": True, "reason": "new_arrival", "id": "3", "sound": "foe"})
        rec.record("presence", {"added": [{"id": "3", "name": "Cee"}], "removed": []})
        rec.record("mark", {"mark": "not_here", "id": "2", "name": "Bea"})
        rec.close()

        lines = _lines(rec.directory())
        self.assertGreaterEqual(len(lines), 5)
        self.assertEqual(lines[0]["kind"], "session")
        self.assertIn("wall", lines[0])
        history = [line for line in lines if line["kind"] == "history"]
        self.assertEqual(history[0]["diff"]["new"], ["1", "2"])
        self.assertEqual(history[0]["diff"]["disappeared"], [])
        self.assertEqual(history[0]["rows"][0]["name"], "Ada")
        self.assertEqual(history[0]["file_mtime"], 100.0)
        self.assertEqual(history[1]["diff"]["new"], ["3"])
        self.assertEqual(history[1]["diff"]["epoch_changed"], ["2"])
        self.assertEqual(history[1]["diff"]["disappeared"], ["1"])
        self.assertEqual(history[1]["file_mtime"], 200.5)
        kinds = {line["kind"] for line in lines}
        self.assertTrue({"decision", "presence", "mark"} <= kinds)
        # The recorder's own folder is the only thing it creates.
        self.assertEqual(os.listdir(folder), ["recordings"])
        self.assertFalse(os.path.exists(os.path.join(folder, "seen_commanders.json")))
        self.assertFalse(os.path.exists(os.path.join(folder, "beep_marks.jsonl")))

    def test_journal_keeps_chat_names_and_drops_the_message(self):
        local = compact_journal({
            "timestamp": "2026-11-07T22:05:47Z",
            "event": "ReceiveText",
            "Channel": "local",
            "From": "SIXART.",
            "Message": "secret words that must not be saved",
        })
        self.assertEqual(local["name"], "SIXART.")
        self.assertEqual(local["channel"], "local")
        self.assertNotIn("Message", local)
        self.assertNotIn("secret words that must not be saved", json.dumps(local))

        starsystem = compact_journal({
            "event": "ReceiveText",
            "Channel": "starsystem",
            "From": "Ada",
            "Message": "system chat stays out",
        })
        self.assertIsNone(starsystem)

        npc = compact_journal({
            "event": "ShipTargeted",
            "TargetLocked": True,
            "PilotName": "$npc_name_decorate:#name=Jefferson Clay;",
            "PilotName_Localised": "Jefferson Clay",
            "ScanStage": 3,
        })
        self.assertIsNone(npc)

        player = compact_journal({
            "timestamp": "2026-11-07T22:50:36Z",
            "event": "ShipTargeted",
            "TargetLocked": True,
            "Ship": "corsair",
            "ScanStage": 3,
            "PilotName": "$cmdr_decorate:#name=SIXART.;",
            "PilotName_Localised": "CMDR SIXART.",
        })
        self.assertEqual(player["name"], "SIXART.")
        self.assertEqual(player["ScanStage"], 3)
        self.assertNotIn("PilotName", player)

        music = compact_journal({"event": "Music", "MusicTrack": "MainMenu"})
        self.assertEqual(music["MusicTrack"], "MainMenu")

        drop = compact_journal({
            "timestamp": "2026-11-07T22:31:52Z",
            "event": "SupercruiseExit",
            "StarSystem": "Shinrarta Dezhra",
            "Body": "Shinrarta Dezhra B 1",
        })
        self.assertEqual(drop["StarSystem"], "Shinrarta Dezhra")

    def test_size_cap_drops_the_oldest_session_file(self):
        folder = tempfile.mkdtemp()
        rec = SessionRecorder(folder, max_total_bytes=500)
        rec.enabled_override = True
        os.makedirs(rec.directory())
        old = os.path.join(rec.directory(), "session-20200101-000000.jsonl")
        mid = os.path.join(rec.directory(), "session-20200102-000000.jsonl")
        with open(old, "w", encoding="utf-8") as handle:
            handle.write("x" * 300)
        with open(mid, "w", encoding="utf-8") as handle:
            handle.write("y" * 300)
        rec.record("note", {"text": "kept"})
        rec.close()
        names = set(os.listdir(rec.directory()))
        self.assertNotIn("session-20200101-000000.jsonl", names)
        self.assertIn("session-20200102-000000.jsonl", names)
        self.assertLessEqual(_folder_size(rec.directory()), 500)
        current = [name for name in names if name not in ("session-20200102-000000.jsonl",)]
        self.assertEqual(len(current), 1)
        with open(os.path.join(rec.directory(), current[0]), encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertTrue(any(line.get("kind") == "note" for line in lines))

    def test_size_cap_stops_when_the_current_file_is_full(self):
        folder = tempfile.mkdtemp()
        rec = SessionRecorder(folder, max_total_bytes=220)
        rec.enabled_override = True
        for index in range(30):
            rec.record("note", {"i": index, "pad": "z" * 30})
        rec.close()
        self.assertLessEqual(_folder_size(rec.directory()), 220)
        lines = _lines(rec.directory())
        self.assertGreaterEqual(len(lines), 1)
        self.assertTrue(all(isinstance(line, dict) and "kind" in line for line in lines))
        # A later write must not grow the folder past the cap.
        rec2 = SessionRecorder(folder, max_total_bytes=220)
        rec2.enabled_override = True
        # Reuse the existing file as the oldest data and try to add more.
        before = _folder_size(rec.directory())
        for index in range(10):
            rec2.record("note", {"i": index, "pad": "q" * 40})
        rec2.close()
        self.assertLessEqual(_folder_size(rec.directory()), 220)
        self.assertGreaterEqual(before, 1)

    def test_disabled_recorder_writes_nothing(self):
        folder = tempfile.mkdtemp()
        rec = SessionRecorder(folder)
        rec.enabled_override = False
        rec.record_history([{"id": "1", "epoch": 1, "interactions": ["Met"]}], 1)
        rec.record_journal({"event": "FSDJump", "StarSystem": "Sol", "timestamp": "2026-11-07T00:00:00Z"})
        self.assertFalse(os.path.isdir(rec.directory()))


class RecorderDoesNotChangeBeeps(unittest.TestCase):
    def _play(self, enabled):
        h = fresh_history()
        h.recorder.enabled_override = enabled
        add_row(h, "11", "NewPerson")
        add_row(h, "10", "Stayer")
        location.system, location.state = "Sol", 0
        location.add_instance("10", state=0, system="Sol")
        location.instance["10"]["here"] = True
        feed(h, ["11"], datetime.datetime(2026, 11, 8, 1, 5))
        feed(h, ["10", "11"], datetime.datetime(2026, 11, 8, 1, 6))
        h._log_presence_changes()
        return h

    def test_same_beeps_with_recording_on_or_off(self):
        on = self._play(True)
        off = self._play(False)
        self.assertEqual(on.sent, [["11"]])
        self.assertEqual(off.sent, [["11"]])
        self.assertEqual(on.sent, off.sent)
        self.assertTrue(present("11"))
        self.assertTrue(present("10"))
        recorded = _lines(on.recorder.directory())
        decisions = [line for line in recorded if line["kind"] == "decision"]
        self.assertTrue(any(line["beep"] and line["reason"] == "new_arrival" and line["sound"] == "neutral" and line["name"] == "NewPerson" for line in decisions))
        self.assertTrue(any((not line["beep"]) and line["reason"] == "refresh" and line["id"] == "10" for line in decisions))
        self.assertTrue(any(line["kind"] == "history" and "11" in line["diff"]["new"] for line in recorded))
        self.assertTrue(any(line["kind"] == "presence" and any(item["id"] == "11" for item in line["added"]) for line in recorded))
        self.assertFalse(os.path.isdir(off.recorder.directory()))

    def test_recorder_failure_still_beeps(self):
        h = fresh_history()
        h.recorder.enabled_override = True
        blocked = os.path.join(h.plugin_dir, "recordings")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("not a folder")

        def explode(*_args, **_kwargs):
            raise RuntimeError("disk full")

        h.recorder.record = explode
        h.recorder.record_history = explode
        add_row(h, "11", "NewPerson")
        location.system, location.state = "Sol", 0
        feed(h, ["11"], datetime.datetime(2026, 11, 8, 2, 0))
        self.assertEqual(h.sent, [["11"]])
        h.record_beep_mark("extra_beep", "11")
        self.assertTrue(os.path.isfile(h._marks_path))

    def test_journal_entry_records_a_drop_without_chat_text(self):
        h = fresh_history()
        h.recorder.enabled_override = True
        load_mod.history_inst = h
        location.system, location.state = "Shinrarta Dezhra", 1
        load_mod.journal_entry(
            "Tester", False, "Shinrarta Dezhra", "",
            {
                "timestamp": "2026-11-07T22:31:52Z",
                "event": "SupercruiseExit",
                "StarSystem": "Shinrarta Dezhra",
                "Body": "Shinrarta Dezhra B 1",
            },
            {},
        )
        load_mod.journal_entry(
            "Tester", False, "Shinrarta Dezhra", "",
            {
                "timestamp": "2026-11-07T22:32:00Z",
                "event": "ReceiveText",
                "Channel": "player",
                "From": "Sabbath91",
                "Message": "do not store this sentence",
            },
            {},
        )
        load_mod.journal_entry(
            "Tester", False, "Shinrarta Dezhra", "",
            {
                "timestamp": "2026-11-07T22:32:01Z",
                "event": "ReceiveText",
                "Channel": "npc",
                "From": "System Authority",
                "Message": "npc line stays out",
            },
            {},
        )
        timer = getattr(h, "_reset_timer", None)
        if timer is not None:
            timer.cancel()
        h.recorder.close()
        with open(os.path.join(h.recorder.directory(), os.listdir(h.recorder.directory())[0]), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("do not store this sentence", text)
        self.assertNotIn("npc line stays out", text)
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        journals = [line for line in lines if line["kind"] == "journal"]
        self.assertTrue(any(line["event"] == "SupercruiseExit" and line["StarSystem"] == "Shinrarta Dezhra" for line in journals))
        self.assertTrue(any(line["event"] == "ReceiveText" and line["name"] == "Sabbath91" and line["channel"] == "player" for line in journals))
        self.assertFalse(any(line.get("channel") == "npc" for line in journals))
