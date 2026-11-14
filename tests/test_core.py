import tempfile
import unittest
from pathlib import Path

from studylock.core import BEGIN, Session, apply_hosts, is_near, strip_block


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.s = Session(self.d / "state.json")

    def test_needs_timer_and_tasks(self):
        self.s.start(60, ["a", "b"], now=1000)
        self.assertTrue(self.s.locked(now=1001))
        self.assertTrue(self.s.locked(now=1000 + 3601))  # timer done, tasks not
        self.s.complete_task(0); self.s.complete_task(1)
        self.assertTrue(self.s.locked(now=1001))          # tasks done, timer not
        self.assertFalse(self.s.locked(now=1000 + 3601))  # both done

    def test_cannot_restart_while_locked(self):
        self.s.start(60, [])
        with self.assertRaises(ValueError):
            self.s.start(1, [])

    def test_hosts_roundtrip(self):
        h = self.d / "hosts"; h.write_text("127.0.0.1 localhost\n")
        self.assertTrue(apply_hosts(True, ["instagram.com"], h))
        self.assertIn("0.0.0.0 www.instagram.com", h.read_text())
        self.assertFalse(apply_hosts(True, ["instagram.com"], h))  # idempotent
        apply_hosts(False, ["instagram.com"], h)
        self.assertEqual(h.read_text(), "127.0.0.1 localhost\n")
        self.assertNotIn(BEGIN, strip_block(h.read_text()))


class Near(unittest.TestCase):
    cfg = {"home": {"lat": 45.7489, "lon": 21.2087, "radius_m": 150}}

    def test_home_and_away(self):
        self.assertTrue(is_near(self.cfg, 45.7490, 21.2088))   # ~14 m away
        self.assertFalse(is_near(self.cfg, 45.7600, 21.2087))  # ~1.2 km away
        self.assertFalse(is_near(self.cfg, None, None))        # no location -> away
        self.assertTrue(is_near({}, None, None))               # no home set -> always near


if __name__ == "__main__":
    unittest.main()
