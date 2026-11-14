import tempfile
import unittest
from pathlib import Path

from studylock.core import BEGIN, Pass, Session, apply_hosts, is_near, strip_block


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


class PassTests(unittest.TestCase):
    def setUp(self):
        self.p = Pass({"pass": {"wait_s": 60, "minutes": 10, "per_day": 2}},
                      Path(tempfile.mkdtemp()) / "p.json")

    def test_wait_then_grant(self):
        t = 1_000_000
        self.assertEqual(self.p.request(t, False, True)["status"], "waiting")
        self.assertEqual(self.p.request(t + 30, False, True)["status"], "waiting")
        r = self.p.request(t + 61, False, True)
        self.assertEqual((r["status"], r["seconds_left"]), ("active", 600))
        self.assertFalse(self.p.blocks_social(t + 100, False, True))   # pass running
        self.assertTrue(self.p.blocks_social(t + 700, False, True))    # expired
        self.assertTrue(self.p.blocks_social(t + 100, True, True))     # session overrides

    def test_daily_limit_and_session_and_away(self):
        t = 2_000_000
        for i in range(2):
            self.p.request(t, False, True)
            self.assertEqual(self.p.request(t + 61, False, True)["status"], "active")
            t += 1000
        self.assertEqual(self.p.request(t, False, True)["status"], "refused")
        self.assertEqual(self.p.request(t, True, True)["status"], "refused")
        self.assertEqual(self.p.request(t, False, False)["status"], "free")
        self.assertFalse(self.p.blocks_social(t, False, False))  # away: never blocked


if __name__ == "__main__":
    unittest.main()
