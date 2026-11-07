"""Seen commanders import, restore and load must ignore poisoned files.

Run from the repository root:  python -m unittest discover -s tests
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class _SilentLog:
    def __getattr__(self, name):
        return lambda *a, **k: None


if "beep_beep_config" not in sys.modules:
    _stub("beep_beep_config", config=types.SimpleNamespace(
        get_config=lambda *a, **k: None,
        set_config=lambda *a, **k: None,
        save_config=lambda: None,
    ))
if "logutil" not in sys.modules:
    _stub("logutil", log=_SilentLog())
if "config" not in sys.modules:
    _stub("config", appname="EDMarketConnector")

import commander_history as ch  # noqa: E402
from commander_history import location  # noqa: E402


class _RecordingLog:
    def __init__(self):
        self.messages = []

    def _note(self, level, msg, args):
        try:
            text = msg % args if args else msg
        except (TypeError, ValueError):
            text = "%s %s" % (msg, args)
        self.messages.append((level, text))

    def error(self, msg, *args, **kwargs):
        self._note("error", msg, args)

    def exception(self, msg, *args, **kwargs):
        self._note("exception", msg, args)

    def warning(self, msg, *args, **kwargs):
        self._note("warning", msg, args)

    def info(self, msg="", *args, **kwargs):
        self._note("info", msg, args)

    def debug(self, *args, **kwargs):
        return None


def _epoch(when):
    return int((when - datetime.datetime(1601, 1, 1)).total_seconds())


def _row(name, last_seen="2026-11-01T00:00:00", **extra):
    row = {
        "name": name,
        "sound": "neutral",
        "last_seen": last_seen,
        "seen_count": 1,
    }
    row.update(extra)
    return row


class ImportSafety(unittest.TestCase):
    def setUp(self):
        self._old_log = ch.log
        self.log = _RecordingLog()
        ch.log = self.log
        location.__init__()
        self.h = ch.CommanderHistoryManager()
        self.h.plugin_dir = tempfile.mkdtemp()
        self.h.json_file_path = os.path.join(self.h.plugin_dir, "seen_commanders.json")
        self.h.instance_seen_path = os.path.join(self.h.plugin_dir, "instance_seen.json")
        self.h.kill_tally_path = os.path.join(self.h.plugin_dir, "kill_tally.json")
        self.h._kill_tally = {}
        self.h.seen_data = {}
        self.h._batch_until = 0
        self.h.set_session_start(datetime.datetime(2026, 11, 7, 0, 0, 0))
        self.sent = []
        self.h._sound_listener = lambda infos: self.sent.append([i["commander_id"] for i in infos])

    def tearDown(self):
        ch.log = self._old_log
        location.__init__()
        shutil.rmtree(self.h.plugin_dir, ignore_errors=True)

    def write_json(self, name, data=None, raw=None):
        path = os.path.join(self.h.plugin_dir, name)
        self._write(path, data, raw)
        return path

    def write_backup(self, name, data=None, raw=None):
        os.makedirs(self.h.backup_dir, exist_ok=True)
        path = os.path.join(self.h.backup_dir, name)
        self._write(path, data, raw)
        return path

    def _write(self, path, data, raw):
        if raw is not None:
            mode = "wb" if isinstance(raw, (bytes, bytearray)) else "w"
            with open(path, mode, encoding=None if mode == "wb" else "utf-8") as handle:
                handle.write(raw)
            return
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def feed(self, cmdr_ids, when):
        entries = [
            {"CommanderID": int(cid), "Interactions": ["Met"], "Epoch": _epoch(when) + index}
            for index, cid in enumerate(cmdr_ids)
        ]
        self.h.aggregate_most_recent_commanders = lambda first_run=False: {"Interactions": entries}
        location.system, location.state = "Sol", 0
        self.h.aggregated_commanders()

    def assert_nothing_written(self):
        self.assertFalse(os.path.isdir(self.h.backup_dir))
        self.assertFalse(os.path.exists(self.h.json_file_path))

    def test_future_timestamp_is_ignored_and_does_not_silence(self):
        self.h.seen_data["7"] = {
            "commander_id": "7",
            "name": "unknown",
            "sound": "friend",
            "last_seen": "2024-05-01T00:00:00",
            "seen_count": 4,
            "kills": 2,
        }
        path = self.write_json("shared.json", {
            "555": {
                "commander_id": "555",
                "name": "Future",
                "sound": "foe",
                "last_seen": "2099-01-01T00:00:00",
                "first_seen": "2099-01-01T00:00:00",
                "seen_count": 40,
                "kills": 9,
                "encounters": [{"start": "2099-01-01T00:00:00", "end": "2099-06-01T00:00:00"}],
            },
            "7": {
                "commander_id": "7",
                "name": "Filled",
                "last_seen": "2099-12-31T23:59:59",
                "kills": 100,
                "sound": "foe",
            },
        })
        self.assertEqual(ch._import_last_seen("2099-01-01T00:00:00"), ch._IMPORT_EPOCH)

        plan = self.h.plan_commander_import(path)
        self.assertEqual(plan["added"], [{"id": "555", "name": "Future"}])
        self.assertEqual(plan["filled"], [{"id": "7", "name": "Filled"}])
        counts = self.h.apply_commander_import(plan)

        row = self.h.seen_data["555"]
        self.assertEqual(row["name"], "Future")
        self.assertEqual(row["last_seen"], ch._IMPORT_EPOCH)
        self.assertEqual(row["seen_count"], 0)
        self.assertEqual(row["sound"], "neutral")
        self.assertNotIn("kills", row)
        blob = json.dumps(row)
        self.assertNotIn("2099", blob)
        kept = self.h.seen_data["7"]
        self.assertEqual(kept["name"], "Filled")
        self.assertEqual(kept["last_seen"], "2024-05-01T00:00:00")
        self.assertEqual(kept["seen_count"], 4)
        self.assertEqual(kept["kills"], 2)
        self.assertEqual(kept["sound"], "friend")
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["filled"], 1)

        self.feed(["555"], datetime.datetime(2026, 11, 7, 1, 0, 0))
        self.assertEqual(self.sent, [["555"]])
        self.assertNotEqual(self.h.seen_data["555"]["last_seen"], ch._IMPORT_EPOCH)
        self.assertNotIn("2099", self.h.seen_data["555"]["last_seen"])

    def test_size_and_entry_caps(self):
        body = json.dumps({"1": {"commander_id": "1", "name": "Ann"}}).encode("utf-8")
        exact = body + b" " * (ch._IMPORT_MAX_BYTES - len(body))
        over = exact + b" "
        exact_path = self.write_json("exact.json", raw=exact)
        over_path = self.write_json("over.json", raw=over)
        self.assertEqual(os.path.getsize(exact_path), ch._IMPORT_MAX_BYTES)
        plan = self.h.plan_commander_import(exact_path)
        self.assertEqual(plan["added"], [{"id": "1", "name": "Ann"}])
        self.assert_nothing_written()

        with self.assertRaises(ValueError) as ctx:
            self.h.plan_commander_import(over_path)
        self.assertIn("5 MB", str(ctx.exception))
        self.assertNotIn("not valid JSON", str(ctx.exception))
        self.assertEqual(self.h.seen_data, {})
        self.assert_nothing_written()

        allowed = {str(i): {"name": "N"} for i in range(1, ch._IMPORT_MAX_ENTRIES + 1)}
        allowed_path = self.write_json("allowed.json", allowed)
        plan = self.h.plan_commander_import(allowed_path)
        self.assertEqual(len(plan["added"]), ch._IMPORT_MAX_ENTRIES)
        self.assert_nothing_written()

        allowed[str(ch._IMPORT_MAX_ENTRIES + 1)] = {"name": "N"}
        too_many = self.write_json("too-many.json", allowed)
        with self.assertRaises(ValueError) as ctx:
            self.h.plan_commander_import(too_many)
        self.assertIn("20,000", str(ctx.exception))
        self.assertEqual(self.h.seen_data, {})
        self.assert_nothing_written()

    def test_deep_nesting_is_a_clean_error(self):
        nested = "[" * 12000 + "0" + "]" * 12000
        path = self.write_json("nested.json", raw=nested)
        with self.assertRaises(ValueError) as ctx:
            self.h.plan_commander_import(path)
        self.assertIn("nested", str(ctx.exception).lower())
        self.assertNotIsInstance(ctx.exception, RecursionError)
        self.assertEqual(self.h.seen_data, {})
        self.assert_nothing_written()
        self.assertTrue(any("Nothing was written" in text for _level, text in self.log.messages))

    def test_bad_ids_and_names_are_rejected_and_good_rows_kept(self):
        long_ok = "A" * ch._NAME_MAX_CHARS
        long_bad = "A" * (ch._NAME_MAX_CHARS + 1)
        long_id = "1" * ch._ID_MAX_DIGITS
        data = {
            "10": {"commander_id": "10", "name": "GoodName", "last_seen": "2099-01-01T00:00:00", "kills": 4, "sound": "foe"},
            "bad": {"name": "Nope"},
            "11": {"commander_id": "11a", "name": "Nope2"},
            "12": {"commander_id": "-3", "name": "Nope3"},
            "13": {"commander_id": "13", "name": ""},
            "14": {"commander_id": "14", "name": "unknown"},
            "15": {"commander_id": "15", "name": "   "},
            "25": {"commander_id": "25", "name": "Unknown"},
            "16": {"commander_id": "16", "name": long_bad},
            "17": {"commander_id": "17", "name": long_ok},
            "18": {"commander_id": "18", "name": 12},
            "19": {"commander_id": "", "name": "Nope4"},
            "20": "not-an-object",
            "21": {"commander_id": " 21 ", "name": "  Spaced Name  "},
            "22": {"commander_id": "٢٢", "name": "ArabicDigits"},
            "26": {"commander_id": "１２", "name": "Fullwidth"},
            "23": {"name": "FromKey"},
            "24": {"commander_id": "10", "name": "Duplicate"},
            long_id: {"name": "LongId"},
            ("9" * (ch._ID_MAX_DIGITS + 1)): {"name": "TooLongId"},
            "30": {"commander_id": 30, "name": "Num"},
        }
        path = self.write_json("mixed.json", data)
        plan = self.h.plan_commander_import(path)
        self.assert_nothing_written()
        self.assertEqual(
            {item["name"] for item in plan["added"]},
            {"GoodName", long_ok, "Spaced Name", "FromKey", "LongId", "Num"},
        )
        self.assertEqual(plan["filled"], [])
        self.assertEqual(plan["kept"], 0)
        self.assertEqual(plan["skipped"], 4)
        self.assertEqual(
            plan["invalid"],
            plan["total"] - plan["skipped"] - len(plan["added"]),
        )
        self.assertGreater(plan["invalid"], 0)
        preview = ch.describe_import_plan(plan)
        self.assertIn("New names: %d" % len(plan["added"]), preview)
        self.assertIn("Invalid rows: %d" % plan["invalid"], preview)
        self.assertIn("Skipped (no name): 4", preview)
        shown = "\n".join(ch.import_plan_lines(plan))
        self.assertIn("GoodName", shown)
        self.assertNotIn("Nope", shown)
        self.assertNotIn(long_bad, shown)

        counts = self.h.apply_commander_import(plan)
        self.assertEqual(counts["added"], len(plan["added"]))
        stored = {row["name"] for row in self.h.seen_data.values()}
        self.assertEqual(stored, {"GoodName", long_ok, "Spaced Name", "FromKey", "LongId", "Num"})
        self.assertEqual(self.h.seen_data["10"]["last_seen"], ch._IMPORT_EPOCH)
        self.assertNotIn("kills", self.h.seen_data["10"])
        self.assertEqual(self.h.seen_data["21"]["name"], "Spaced Name")
        self.assertEqual(self.h.seen_data["30"]["commander_id"], "30")

    def test_control_and_bidi_characters_are_rejected(self):
        hidden = [
            "Bo\x00b",
            "Bo\nb",
            "Bo\tb",
            "Bo\x1bb",
            "Bo\x7fb",
            "Bo\u202eb",
            "\u202eBob",
            "Bo\u200bb",
            "Bo\u200eb",
            "Bo\u200fb",
            "Bo\ufeffb",
            "Bo\u202ab",
            "Bo\u202bb",
            "Bo\u202cb",
            "Bo\u202db",
            "Bo\u2066b",
            "Bo\u2067b",
            "Bo\u2068b",
            "Bo\u2069b",
            "\u0001",
        ]
        data = {
            "100": {"commander_id": "100", "name": "Safe"},
            # A trailing newline is whitespace. It is removed, and the control
            # character itself is not stored.
            "150": {"commander_id": "150", "name": "Trim\n"},
        }
        for index, name in enumerate(hidden, start=101):
            data[str(index)] = {"commander_id": str(index), "name": name}
        path = self.write_json("hidden.json", data)
        plan = self.h.plan_commander_import(path)
        self.assertEqual(plan["added"], [{"id": "100", "name": "Safe"}, {"id": "150", "name": "Trim"}])
        self.assertEqual(plan["invalid"], len(hidden))
        self.assertEqual(plan["skipped"], 0)
        self.h.apply_commander_import(plan)
        self.assertEqual(set(self.h.seen_data), {"100", "150"})
        self.assertEqual(self.h.seen_data["100"]["name"], "Safe")
        self.assertEqual(self.h.seen_data["150"]["name"], "Trim")
        for row in self.h.seen_data.values():
            self.assertFalse(ch._has_hidden_chars(row["name"]))

        self.h.seen_data["200"] = {"commander_id": "200", "name": "Ann\u202e"}
        self.h.seen_data["201"] = {"commander_id": "201", "name": "Real"}
        keys = self.h._known_name_keys()
        self.assertIn(ch.name_key("Real"), keys)
        self.assertIn(ch.name_key("Safe"), keys)
        self.assertNotIn(ch.name_key("Ann\u202e"), keys)
        self.assertNotIn(ch.name_key("Ann"), keys)

    def test_cancel_writes_nothing(self):
        self.h.seen_data["1"] = {
            "commander_id": "1",
            "name": "Bob",
            "sound": "neutral",
            "last_seen": "2026-11-01T00:00:00",
            "seen_count": 2,
        }
        original = json.loads(json.dumps(self.h.seen_data))
        with open(self.h.json_file_path, "w", encoding="utf-8") as handle:
            json.dump(original, handle)
        with open(self.h.json_file_path, "rb") as handle:
            before = handle.read()
        path = self.write_json("shared.json", {
            "2": {
                "commander_id": "2",
                "name": "B0b",
                "last_seen": "2099-01-01T00:00:00",
                "kills": 3,
            },
        })

        def refuse(*_args, **_kwargs):
            raise AssertionError("cancel must not write")

        self.h.backup_seen_commanders_now = refuse
        self.h.flush_seen_commanders = refuse
        self.h.save_seen_commanders = refuse

        plan = self.h.plan_commander_import(path)
        # Cancel stops here. The dialog does not call apply_commander_import.
        self.assertEqual(plan["added"], [{"id": "2", "name": "B0b"}])
        self.assertIn("B0b", "\n".join(ch.import_plan_lines(plan)))
        self.assertEqual(self.h.seen_data, original)
        self.assertEqual(self.h._known_name_keys(), {ch.name_key("Bob")})
        self.assertFalse(os.path.isdir(self.h.backup_dir))
        with open(self.h.json_file_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_before_import_backup_label(self):
        self.h.seen_data["1"] = _row("Bob")
        self.h.seen_data["1"]["commander_id"] = "1"
        path = self.write_json("shared.json", {"2": {"name": "New"}})
        counts = self.h.import_commander_names(path)
        self.assertIsNotNone(counts["backup"])
        names = self.h.list_backups()
        labeled = [name for name in names if ch.backup_list_kind(name) == "Before import"]
        self.assertEqual(len(labeled), 1)
        self.assertTrue(labeled[0].startswith("seen_commanders-manual-before-import-"))
        self.assertIn("Before import", "%s    %s" % ("2026-11-07 06:28", ch.backup_list_kind(labeled[0])))
        self.assertEqual(ch.backup_list_kind("seen_commanders-manual-20261107-010203.json"), "Manual")
        self.assertEqual(ch.backup_list_kind("seen_commanders-manual-before-restore-20261107-010203.json"), "Manual")
        self.assertEqual(ch.backup_list_kind("seen_commanders-20261107-010203.json"), "Startup")
        manual = self.h.backup_seen_commanders_now("")
        self.assertEqual(ch.backup_list_kind(os.path.basename(manual)), "Manual")

    def test_corrupt_restore_leaves_data_intact(self):
        self.h.seen_data["7"] = {
            "commander_id": "7",
            "name": "Kept",
            "sound": "neutral",
            "last_seen": "2026-11-01T00:00:00",
            "seen_count": 2,
        }
        original = json.loads(json.dumps(self.h.seen_data))

        class Watching(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.cleared = False

            def clear(self):
                self.cleared = True
                super().clear()

        cases = {
            "seen_commanders-manual-bad-json.json": b"{not json",
            "seen_commanders-manual-list.json": b"[1, 2, 3]",
            "seen_commanders-manual-nested.json": ("[" * 12000 + "0" + "]" * 12000).encode("ascii"),
            "seen_commanders-manual-empty-rows.json": {"bad": {"name": "Hi"}},
        }
        for name, payload in cases.items():
            self.log.messages.clear()
            watching = Watching(original)
            self.h.seen_data = watching
            if isinstance(payload, dict):
                self.write_backup(name, payload)
            else:
                self.write_backup(name, raw=payload)
            with self.assertRaises(ValueError):
                self.h.restore_backup(name)
            self.assertFalse(watching.cleared)
            self.assertEqual(self.h.seen_data, original)
            self.assertEqual(os.listdir(self.h.backup_dir), [name])
            self.assertTrue(any("left unchanged" in text for _level, text in self.log.messages))
            os.remove(os.path.join(self.h.backup_dir, name))

        good = {
            "8": {
                "commander_id": "8",
                "name": "Restored",
                "sound": "friend",
                "last_seen": "2026-08-01T00:00:00",
                "seen_count": 3,
                "kills": 1,
                "aliases": ["Old"],
            },
            "nope": "bad-row",
        }
        self.h.seen_data = dict(original)
        self.write_backup("seen_commanders-manual-good.json", good)
        count = self.h.restore_backup("seen_commanders-manual-good.json")
        self.assertEqual(count, 1)
        self.assertEqual(set(self.h.seen_data), {"8"})
        self.assertEqual(self.h.seen_data["8"]["name"], "Restored")
        self.assertEqual(self.h.seen_data["8"]["seen_count"], 3)
        self.assertEqual(self.h.seen_data["8"]["aliases"], ["Old"])
        self.assertTrue(any(ch.backup_list_kind(name) == "Manual" and "before-restore" in name for name in self.h.list_backups()))

    def test_corrupt_load_leaves_data_intact(self):
        original = {
            "7": {
                "commander_id": "7",
                "name": "Kept",
                "sound": "neutral",
                "last_seen": "2026-11-01T00:00:00",
                "seen_count": 2,
                "kills": 1,
            }
        }

        class Watching(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.cleared = False

            def clear(self):
                self.cleared = True
                super().clear()

        for raw in (b"{not json", b"[1, 2]", ("[" * 12000 + "0" + "]" * 12000).encode("ascii")):
            self.log.messages.clear()
            watching = Watching(original)
            self.h.seen_data = watching
            self.write_json("seen_commanders.json", raw=raw)
            self.h.load_seen_commanders()
            self.assertFalse(watching.cleared)
            self.assertEqual(dict(self.h.seen_data), original)
            self.assertTrue(any("left unchanged" in text for _level, text in self.log.messages))

        self.log.messages.clear()
        watching = Watching(original)
        self.h.seen_data = watching
        self.write_json("seen_commanders.json", {"bad": {"name": "Hi"}})
        self.h.load_seen_commanders()
        self.assertFalse(watching.cleared)
        self.assertEqual(dict(self.h.seen_data), original)
        self.assertTrue(any("left unchanged" in text for _level, text in self.log.messages))

    def test_load_keeps_a_normal_file_and_drops_bad_rows(self):
        normal = {
            "42": {
                "commander_id": "42",
                "name": "Ann",
                "sound": "friend",
                "last_seen": "2026-11-07T12:00:00",
                "seen_count": 0,
                "kills": 3,
                "first_seen": "2026-11-01T00:00:00",
                "encounters": [{"start": "2026-11-01T00:00:00", "end": "2026-11-07T12:00:00"}],
                "aliases": ["Annie"],
            }
        }
        self.h.seen_data = {"1": _row("Old")}
        self.h.seen_data["1"]["commander_id"] = "1"
        self.write_json("seen_commanders.json", normal)
        self.h.load_seen_commanders()
        self.assertEqual(set(self.h.seen_data), {"42"})
        row = self.h.seen_data["42"]
        self.assertEqual(row["name"], "Ann")
        self.assertEqual(row["sound"], "friend")
        self.assertEqual(row["last_seen"], "2026-11-07T12:00:00")
        self.assertEqual(row["seen_count"], 0)
        self.assertEqual(row["kills"], 3)
        self.assertEqual(row["first_seen"], "2026-11-01T00:00:00")
        self.assertEqual(row["aliases"], ["Annie"])
        self.assertEqual(row["encounters"], normal["42"]["encounters"])

        mixed = {
            "8": {
                "commander_id": "8",
                "name": "Okay",
                "sound": {"not": "a string"},
                "last_seen": {"nested": [1, 2, 3]},
                "seen_count": "nope",
                "kills": {"a": 1},
            },
            "9": "not-a-row",
            "10": {
                "commander_id": "10",
                "name": "Also\u202e",
                "sound": "neutral",
                "last_seen": "2026-01-01T00:00:00",
                "seen_count": 1,
            },
        }
        self.write_json("seen_commanders.json", mixed)
        self.h.seen_data = {"1": _row("Old")}
        self.h.load_seen_commanders()
        self.assertEqual(set(self.h.seen_data), {"8", "10"})
        self.assertEqual(self.h.seen_data["8"]["name"], "Okay")
        self.assertEqual(self.h.seen_data["8"]["sound"], "neutral")
        self.assertEqual(self.h.seen_data["8"]["last_seen"], ch._IMPORT_EPOCH)
        self.assertEqual(self.h.seen_data["8"]["seen_count"], 1)
        self.assertEqual(self.h.seen_data["8"]["kills"], 0)
        self.assertEqual(self.h.seen_data["10"]["name"], "Also")
        self.sent.clear()
        self.feed(["8"], datetime.datetime(2026, 11, 7, 2, 0, 0))
        self.assertEqual(self.sent, [["8"]])

    def test_bad_row_does_not_stall_aggregation(self):
        self.h.seen_data["1"] = {
            "commander_id": "1",
            "name": "Good",
            "sound": "neutral",
            "last_seen": "2020-01-01T00:00:00",
            "seen_count": 1,
        }
        self.h.seen_data["2"] = {
            "commander_id": "2",
            "name": "Bad",
            "sound": "neutral",
            "last_seen": {"nope": True},
            "seen_count": "many",
            "kills": {"x": 1},
        }
        self.feed(["2", "1"], datetime.datetime(2026, 11, 7, 3, 0, 0))
        self.assertEqual(self.sent, [["2", "1"]])

    def test_valid_backup_still_recovers_a_corrupt_main_file(self):
        self.h.seen_data["1"] = _row("Memory")
        self.h.seen_data["1"]["commander_id"] = "1"
        self.write_json("seen_commanders.json", raw=b"{broken")
        self.write_backup("seen_commanders-20200101-000000.json", {
            "9": {
                "commander_id": "9",
                "name": "FromBackup",
                "sound": "neutral",
                "last_seen": "2025-01-01T00:00:00",
                "seen_count": 1,
            }
        })
        self.h.load_seen_commanders()
        self.assertEqual(set(self.h.seen_data), {"9"})
        self.assertEqual(self.h.seen_data["9"]["name"], "FromBackup")
        self.assertTrue(any("From backup" in text or "backup" in text.lower() for _level, text in self.log.messages))
