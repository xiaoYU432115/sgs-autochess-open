import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.auto_battle import AutoBattler


class _DisconnectedOps:
    def connected(self):
        return False


class AutoBattleTests(unittest.TestCase):
    def test_choose_attempt_is_throttled(self):
        battler = AutoBattler.__new__(AutoBattler)
        battler.season = "s11"
        battler.cfg = {
            "auto_choose_lord": True,
            "banned_factions": [],
            "prefer_tags": [],
        }
        battler._choose_last_attempt_at = 0.0
        battler.log = lambda _message: None
        attempts = []
        battler._choose_general = lambda gid: attempts.append(gid) or True
        live = SimpleNamespace(me={}, banned_factions=set())

        recommendation = [{"id": 123, "name": "Test Lord", "score": 1, "why": "test"}]
        with patch("core.s11_enhance.lord_pick_advice", return_value=recommendation):
            battler._handle_choose(live)
            battler._handle_choose(live)

        self.assertEqual(len(attempts), 1)

    def test_immediate_restart_stops_previous_worker(self):
        battler = AutoBattler(_DisconnectedOps(), log_cb=lambda _message: None)
        battler.cfg["tick"] = 0.01

        battler.start()
        old_thread = battler._th
        try:
            time.sleep(0.03)
            battler.stop()
            battler.start()
            new_thread = battler._th

            time.sleep(0.05)
            self.assertFalse(old_thread.is_alive())
            self.assertTrue(new_thread.is_alive())
        finally:
            battler.stop()
            battler._th.join(timeout=1)
        self.assertFalse(battler._th.is_alive())


if __name__ == "__main__":
    unittest.main()
