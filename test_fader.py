"""Test della dissolvenza (classe Fader di mixer.pyw).

Gira senza PySide6, pycaw e senza audio: prende il codice del Fader da mixer.pyw e lo prova
con sessioni audio finte.   Avvio:  python test_fader.py
"""
import ast
import json
import os
import tempfile
import time
import unittest

SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixer.pyw")
SRC = open(SRC_PATH, encoding="utf-8").read()
TREE = ast.parse(SRC)
NEEDED = {"Fader", "_set_channels", "load_fade_recovery", "save_fade_recovery", "session_key"}


class QObject:
    def __init__(self, parent=None):
        pass


class QTimer:
    def __init__(self, parent=None, interval=0, timeout=None):
        self.active = False

    def isActive(self):
        return self.active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class Vol:
    def __init__(self):
        self.v, self.m = 0.8, 0

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, ctx):
        self.v = v

    def GetMute(self):
        return self.m

    def SetMute(self, m, ctx):
        self.m = m


class Channels:
    def __init__(self):
        self.levels = [1.0, 1.0]

    def GetChannelCount(self):
        return 2

    def SetChannelVolume(self, i, level, ctx):
        self.levels[i] = level


class Session:
    def __init__(self, key):
        self.InstanceIdentifier = key
        self.SimpleAudioVolume = Vol()
        self.ch = Channels()


def make_fader(ms=200):
    code = "\n\n".join(ast.get_source_segment(SRC, n) for n in TREE.body
                       if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NEEDED)
    cfg = tempfile.mkdtemp()
    g = dict(QObject=QObject, QTimer=QTimer, time=time, json=json, os=os,
             CONFIG_DIR=cfg, FADE_FILE=os.path.join(cfg, "fade.json"), FADE_MAX_MS=3000,
             SYSTEM_NAME="Suoni di sistema", _channel_iface=lambda s: getattr(s, "ch", None),
             read_json=read_json, safe_write=lambda p: open(p, "w", encoding="utf-8"))
    exec(code, g)
    return g["Fader"](None, ms), g["FADE_FILE"]


def run(fader, seconds=1.0):
    end = time.monotonic() + seconds
    while fader.busy() and time.monotonic() < end:
        fader.step()
        time.sleep(0.015)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def half_step(fader, wait=0.05):
    fader.step()
    time.sleep(wait)
    fader.step()


class FaderTest(unittest.TestCase):
    def setUp(self):
        self.f, self.file = make_fader()
        self.s = Session("app-1")

    def test_mute_fades_out_then_mutes(self):
        self.f.mute([self.s], True, "app")
        half_step(self.f)
        self.assertEqual(self.s.SimpleAudioVolume.m, 0)
        self.assertLess(self.s.ch.levels[0], 1.0)
        self.assertTrue(self.f.mute_state([self.s], False))
        self.assertEqual(read_json(self.file), ["app"])
        run(self.f)
        self.assertEqual(self.s.SimpleAudioVolume.m, 1)
        self.assertEqual(self.s.ch.levels, [1.0, 1.0])
        self.assertEqual(self.s.SimpleAudioVolume.v, 0.8)
        self.assertEqual(read_json(self.file), [])

    def test_unmute_fades_in(self):
        self.s.SimpleAudioVolume.m = 1
        self.f.mute([self.s], False, "app")
        self.assertEqual(self.s.SimpleAudioVolume.m, 0)
        self.assertEqual(self.s.ch.levels, [0.0, 0.0])
        run(self.f)
        self.assertEqual(self.s.ch.levels, [1.0, 1.0])

    def test_reverse_mid_fade(self):
        self.f.mute([self.s], True, "app")
        half_step(self.f)
        self.f.mute([self.s], False, "app")
        run(self.f)
        self.assertEqual(self.s.SimpleAudioVolume.m, 0)
        self.assertEqual(self.s.ch.levels, [1.0, 1.0])

    def test_ramp_reaches_target(self):
        self.f.ramp([self.s], 0.3)
        half_step(self.f, 0.08)
        self.assertEqual(self.f.volume_state([self.s], 0), 0.3)
        run(self.f)
        self.assertAlmostEqual(self.s.SimpleAudioVolume.v, 0.3)

    def test_ramp_stops_when_someone_else_changes_volume(self):
        self.f.ramp([self.s], 0.9)
        half_step(self.f, 0.03)
        self.s.SimpleAudioVolume.v = 0.5
        run(self.f)
        self.assertEqual(self.s.SimpleAudioVolume.v, 0.5)

    def test_finish_all_completes_everything(self):
        self.f.mute([self.s], True, "app")
        self.f.ramp([self.s], 0.1)
        self.f.finish_all()
        self.assertEqual(self.s.SimpleAudioVolume.m, 1)
        self.assertEqual(self.s.ch.levels, [1.0, 1.0])
        self.assertEqual(self.s.SimpleAudioVolume.v, 0.1)
        self.assertFalse(self.f.busy())

    def test_zero_duration_is_instant(self):
        self.f.set_duration(0)
        self.f.mute([self.s], True)
        self.assertEqual(self.s.SimpleAudioVolume.m, 1)
        self.assertFalse(self.f.busy())

    def test_shutdown_finishes_fades_last(self):
        """restore_solo avvia fade: finish_all deve venire dopo, o le app restano a canali 0."""
        mixer = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Mixer")
        shutdown = next(n for n in mixer.body
                        if isinstance(n, ast.FunctionDef) and n.name == "shutdown")
        calls = [c.func.attr for c in ast.walk(shutdown)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        self.assertGreater(calls.index("finish_all"), calls.index("restore_solo"))


if __name__ == "__main__":
    unittest.main()
