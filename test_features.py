"""Test delle funzioni senza interfaccia: gruppi, MIDI, pubblicita', profili, versioni, lingue.

Servono le librerie di requirements.txt (non serve audio).   Avvio:  python test_features.py
"""
import importlib.machinery
import importlib.util
import os
import unittest

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
    def test_titles(self):
        self.assertTrue(mixer.is_ad_title(["Advertisement"]))
        self.assertTrue(mixer.is_ad_title(["Spotify Free"]))
        self.assertFalse(mixer.is_ad_title(["Artist - Song"]))
        self.assertFalse(mixer.is_ad_title([]))


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


class MiscTest(unittest.TestCase):
    def test_versions(self):
        self.assertGreater(mixer.version_tuple("v0.4.0"), mixer.version_tuple("0.3.9"))
        self.assertGreater(mixer.version_tuple("0.3.10"), mixer.version_tuple("0.3.9"))

    def test_language_files_are_complete(self):
        self.assertEqual(mixer.LANGUAGE_PROBLEMS, [])
        self.assertTrue({"en", "de", "es", "ru"} <= set(mixer.LANGUAGES))


if __name__ == "__main__":
    unittest.main()
