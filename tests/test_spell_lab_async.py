import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from ui.spell_lab_tab import SpellLabTabBuilder


class _App:
    def __init__(self):
        self.job = None

    def _run_bg(self, tag, fn, on_done=None):
        self.job = (tag, fn, on_done)
        return True


class _Button:
    def __init__(self):
        self.state = "normal"

    def configure(self, **kwargs):
        self.state = kwargs["state"]


class SpellLabAsyncTests(unittest.TestCase):
    def test_refresh_schedules_network_work_in_background(self):
        builder = SpellLabTabBuilder.__new__(SpellLabTabBuilder)
        builder.app = _App()
        builder.ops = object()
        builder._refreshing = False
        builder._refresh_btn = _Button()

        builder._refresh()

        self.assertEqual(builder.app.job[0], "spell_lab_refresh")
        self.assertIs(builder.app.job[1].__self__, builder)
        self.assertEqual(builder._refresh_btn.state, "disabled")
        self.assertTrue(builder._refreshing)


if __name__ == "__main__":
    unittest.main()
