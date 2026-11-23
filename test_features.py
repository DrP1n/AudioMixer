"""Test delle funzioni senza interfaccia: gruppi, MIDI, pubblicita', profili, versioni, lingue.

Servono le librerie di requirements.txt (non serve audio).   Avvio:  python test_features.py
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixer.pyw")
_loader = importlib.machinery.SourceFileLoader("mixer", _path)
mixer = importlib.util.module_from_spec(importlib.util.spec_from_loader("mixer", _loader))
_loader.exec_module(mixer)


class BusTest(unittest.TestCase):
    def test_keeps_ratios_and_survives_zero(self):
        bus = {"members": ["a", "b"], "gain": 1.0, "bases": {}}
        mixer.bus_capture(bus, {"a": 0.8, "b": 0.4})
        self.assertEqual(mixer.bus_targets(bus, 0.5, {}), {"a": 0.4, "b": 0.2})
        bus["gain"] = 0.0
        mixer.bus_capture(bus, {"a": 0.0, "b": 0.0})  # a zero le basi non si perdono
        self.assertEqual(mixer.bus_targets(bus, 1.0, {}), {"a": 0.8, "b": 0.4})

    def test_respects_limits(self):
        bus = {"members": ["a"], "gain": 1.0, "bases": {"a": 0.9}}
        self.assertEqual(mixer.bus_targets(bus, 1.0, {"a": 0.5}), {"a": 0.5})


class MidiTest(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(mixer.midi_key(0xB1, 7, 100), ("cc:1:7", 100))
        self.assertEqual(mixer.midi_key(0x90, 60, 64), ("note:0:60", 64))
        self.assertIsNone(mixer.midi_key(0x90, 60, 0))
        self.assertIsNone(mixer.midi_key(0x80, 60, 64))


class AdTest(unittest.TestCase):
    """check_ads con Spotify finto: due sessioni audio (due dispositivi) e titoli decisi qui."""

    def setUp(self):
        self.titles = []
        self.old = {k: getattr(mixer, k) for k in ("window_titles", "fade_mute", "shown_mute",
                                                  "save_ad_recovery", "save_solo_recovery")}
        self.old_proc = mixer.psutil.Process
        mixer.window_titles = lambda: {1: self.titles}
        mixer.psutil.Process = lambda pid: SimpleNamespace(name=lambda: "Spotify.exe")
        mixer.shown_mute = lambda sessions, actual: actual
        mixer.save_ad_recovery = mixer.save_solo_recovery = lambda names: None

        def fade_mute(sessions, muted, name=None):
            for s in sessions:
                s.muted = muted
        mixer.fade_mute = fade_mute
        self.a, self.b = self.session("a"), self.session("b")
        self.w = SimpleNamespace(settings={"ad_mute": True}, groups={"Spotify": [self.a, self.b]},
                                 ad_muted={}, _ad_seen={}, solo_prev={})
        for f in ("end_ad", "_mute_ad_sessions"):
            setattr(self.w, f, getattr(mixer.Mixer, f).__get__(self.w))

    def tearDown(self):
        for k, v in self.old.items():
            setattr(mixer, k, v)
        mixer.psutil.Process = self.old_proc

    @staticmethod
    def session(key, muted=False):
        s = SimpleNamespace(InstanceIdentifier=key, muted=muted)
        s.SimpleAudioVolume = SimpleNamespace(GetMute=lambda: s.muted)
        return s

    def step(self, title):
        self.titles = [title]
        mixer.Mixer.check_ads(self.w)
        return [s.muted for s in self.w.groups.get("Spotify", [])]

    def test_titles(self):
        for t in ("Advertisement", "Spotify", "Nome Sponsor"):
            self.assertIs(mixer.ad_state([t]), True)
        self.assertIs(mixer.ad_state(["Artist - Song", "Spotify"]), False)
        for t in ([], ["Spotify Free"], ["Spotify Premium"]):
            self.assertIsNone(mixer.ad_state(t))

    def test_both_sessions_and_pause(self):
        self.assertEqual(self.step("Artist - Song"), [False, False])
        self.assertEqual(self.step("Spotify Free"), [False, False])  # pausa, non pubblicita'
        self.assertEqual(self.step("Advertisement"), [True, True])
        self.assertEqual(self.step("Spotify Free"), [True, True])  # pausa durante la pubblicita'
        self.assertEqual(self.step("Artist - Song"), [False, False])

    def test_first_session_already_muted_by_user(self):
        self.a.muted = True
        self.assertEqual(self.step("Advertisement"), [True, True])
        self.assertEqual(self.step("Artist - Song"), [True, False])

    def test_session_opened_during_ad_and_user_unmute(self):
        self.step("Advertisement")
        self.b.muted = False  # l'utente smuta: resta cosi'
        self.w.groups["Spotify"].append(self.session("c"))
        self.assertEqual(self.step("Advertisement"), [True, False, True])
        self.assertEqual(self.step("Artist - Song"), [False, False, False])

    def test_sessions_vanish_and_crash_recovery(self):
        self.step("Advertisement")
        self.w.groups = {}
        self.step("Advertisement")
        self.w.groups = {"Spotify": [self.a, self.b]}
        self.assertEqual(self.step("Artist - Song"), [False, False])
        self.a.muted = self.b.muted = True
        self.w.ad_muted = {"Spotify": None}  # ripartito dopo una chiusura brusca
        self.assertEqual(self.step("Artist - Song"), [False, False])


class ProfileTest(unittest.TestCase):
    def test_round_trip_drops_hotkeys_and_bad_rows(self):
        presets = {"Gaming": {"apps": {}, "hotkey": "f1"}}
        auto = {"enabled": True, "rules": [
            {"id": "x", "type": "time", "time": "22:00", "preset": "Gaming"},
            {"type": "bogus", "preset": "Gaming"}]}
        duck = {"enabled": True, "groups": [{"id": "g", "triggers": ["Discord"], "targets": ["Spotify"]}]}
        p, rules, groups = mixer.read_profile(mixer.make_profile(presets, auto, duck))
        self.assertEqual(p, {"Gaming": {"apps": {}}})
        self.assertEqual(len(rules), 1)
        self.assertNotEqual(rules[0]["id"], "x")
        self.assertEqual(groups[0]["targets"], ["Spotify"])

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            mixer.read_profile({"presets": {}})


class MirrorTest(unittest.TestCase):
    def test_no_loops(self):
        pairs = [["A", "B"], ["B", "C"]]
        self.assertTrue(mixer.mirror_allowed(pairs, "A", "D"))
        self.assertFalse(mixer.mirror_allowed(pairs, "C", "A"))  # A->B->C->A
        self.assertFalse(mixer.mirror_allowed(pairs, "B", "A"))
        self.assertFalse(mixer.mirror_allowed(pairs, "A", "A"))


class SafeFilesTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "x.json")
        mixer.log_error = lambda text: None

    def save(self, data):
        with mixer.safe_write(self.path) as f:
            json.dump(data, f)

    def test_corrupt_file_falls_back_to_backup(self):
        self.save({"v": 1})
        self.save({"v": 2})
        open(self.path, "w").write('{"v":')  # spegnimento a meta' scrittura
        self.assertEqual(mixer.read_json(self.path), {"v": 1})
        self.assertTrue(os.path.exists(self.path + ".corrupt"))
        self.assertEqual(mixer.read_json(self.path), {"v": 1})

    def test_failed_write_keeps_old_file(self):
        self.save({"v": 1})
        with self.assertRaises(TypeError):
            self.save({"v": object()})
        self.assertEqual(mixer.read_json(self.path), {"v": 1})

    def test_removed_file_does_not_come_back_from_backup(self):
        self.save([1])
        self.save([2])
        os.remove(self.path)
        with self.assertRaises(FileNotFoundError):
            mixer.read_json(self.path)


class MiscTest(unittest.TestCase):
    def test_versions(self):
        self.assertGreater(mixer.version_tuple("v0.4.0"), mixer.version_tuple("0.3.9"))
        self.assertGreater(mixer.version_tuple("0.3.10"), mixer.version_tuple("0.3.9"))

    def test_language_files_are_complete(self):
        self.assertEqual(mixer.LANGUAGE_PROBLEMS, [])
        self.assertTrue({"en", "de", "es", "ru"} <= set(mixer.LANGUAGES))


if __name__ == "__main__":
    unittest.main()
