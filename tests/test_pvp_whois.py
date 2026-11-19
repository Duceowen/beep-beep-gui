import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if "logutil" not in sys.modules:
    mod = types.ModuleType("logutil")

    class _Log:
        def __getattr__(self, name):
            return lambda *a, **k: None

    mod.log = _Log()
    sys.modules["logutil"] = mod

import pvp_whois as w  # noqa: E402


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"cmdr_name":"Target","kills":36,"deaths":22,"is_combat_logger":false}'


def _http_error(code, headers=None):
    return w.urllib.error.HTTPError("url", code, "err", headers or {}, None)


class WhoisThrottling(unittest.TestCase):
    def setUp(self):
        w.reset_for_tests()

    def _call(self, name):
        done = threading.Event()
        result = []

        def cb(kind, data):
            result.append((kind, data))
            done.set()

        w.request(name, cb)
        done.wait(2)
        return result[0] if result else []

    def test_success_is_cached_per_commander(self):
        with mock.patch.object(w.urllib.request, "urlopen", return_value=_Response()) as open_mock:
            self.assertEqual(self._call("Target")[0], "ok")
            self.assertEqual(self._call("target")[0], "ok")
            self.assertEqual(open_mock.call_count, 1)

    def test_unknown_result_is_cached(self):
        with mock.patch.object(w.urllib.request, "urlopen", side_effect=_http_error(404)) as open_mock:
            self.assertEqual(self._call("Nobody")[0], "unknown")
            self.assertEqual(self._call("Nobody")[0], "unknown")
            self.assertEqual(open_mock.call_count, 1)

    def test_minimum_interval_blocks_rapid_lookups(self):
        with mock.patch.object(w.urllib.request, "urlopen", return_value=_Response()) as open_mock:
            self._call("One")
            result = self._call("Two")
            self.assertEqual(result, [])
            self.assertEqual(open_mock.call_count, 1)

    def test_call_budget_blocks_after_ten(self):
        with mock.patch.object(w.urllib.request, "urlopen", return_value=_Response()) as open_mock:
            w._calls[:] = [time.time()] * w.MAX_CALLS_PER_MINUTE
            self.assertEqual(self._call("Blocked"), [])
            open_mock.assert_not_called()

    def test_rate_limit_pauses_all_lookups(self):
        with mock.patch.object(w.urllib.request, "urlopen", side_effect=_http_error(429, {"Retry-After": "300"})) as open_mock:
            self._call("First")
            self.assertEqual(open_mock.call_count, 1)
        with mock.patch.object(w.urllib.request, "urlopen", return_value=_Response()) as open_mock:
            self.assertEqual(self._call("Second"), [])
            open_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
