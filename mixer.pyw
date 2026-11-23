"""
Mixer audio per app (stile OBS "Audio Monitor") - Windows 10/11
Installazione:  pip install -r requirements.txt
Avvio:          python mixer.pyw
"""
import sys
import os
import json
import math
import re
import time
import threading
import contextlib
import subprocess
import tempfile
from collections import defaultdict
from string import Template
from ctypes import c_float, c_uint32, POINTER, HRESULT

import keyboard
import psutil
from PySide6.QtCore import (Qt, QTimer, QRectF, QFileInfo, QObject, Signal, QByteArray, QPointF,
                            QPoint, QMimeData, QLocale, QTranslator, QLibraryInfo, QTime)
from PySide6.QtGui import (QPainter, QColor, QPen, QIcon, QGuiApplication,
                           QDrag, QCursor, QPixmap, QImage, QPolygonF)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton, QScrollArea, QFileIconProvider,
    QAbstractButton, QSizePolicy, QMenu, QInputDialog, QDialog, QMessageBox,
    QSystemTrayIcon, QCheckBox, QTabWidget, QSplitter, QComboBox, QColorDialog, QSpacerItem,
    QSpinBox, QGridLayout, QStyle, QStyleOptionSlider, QTimeEdit,
)
from comtypes import CLSCTX_ALL, GUID, COMMETHOD, IUnknown
from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
from pycaw.utils import AudioSession
try:
    from pycaw.api.audiopolicy import IAudioSessionControl2
except Exception:
    try:
        from pycaw.pycaw import IAudioSessionControl2
    except Exception:
        IAudioSessionControl2 = None

LANGUAGES = {"it": "Italiano"}
LANG = "it"
TRANSLATIONS = {}
QUOTES = {"it": "\u00ab\u00bb"}
LANGUAGE_DIRS = []
LANGUAGE_PROBLEMS = []
LANG_CODE_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?")
PLACEHOLDER_RE = re.compile(r"\{\w+\}")


def app_dir():
    """Cartella del programma: quella dell'.exe, oppure quella di mixer.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


def _valid_translation(key, value):
    """Una traduzione e' valida se ha testo e gli stessi segnaposto {nome} dell'italiano, e nessuna
    altra graffa (altrimenti formattarla potrebbe fallire)."""
    if not isinstance(value, str) or not value.strip():
        return False
    if _placeholders(key) != _placeholders(value):
        return False
    rest = PLACEHOLDER_RE.sub("", value)
    return "{" not in rest and "}" not in rest


def load_languages():
    """Legge i file lingue/<codice>.json (accanto al programma; per l'.exe anche la copia inclusa
    al suo interno). Ogni file: {"language": nome, "quotes": virgolette, "strings": {italiano: testo}}.
    Voci o file non validi vengono scartati senza fermare il programma e annotati in
    LANGUAGE_PROBLEMS. L'italiano e' sempre disponibile perche' e' nel codice."""
    dirs = [os.path.join(app_dir(), "lingue")]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(getattr(sys, "_MEIPASS", app_dir()), "lingue"))
    LANGUAGE_DIRS[:] = dirs
    LANGUAGE_PROBLEMS.clear()
    for table in list(TRANSLATIONS):
        del TRANSLATIONS[table]
    found = {}
    for folder in dirs:
        try:
            files = sorted(os.listdir(folder))
        except OSError:
            if folder == dirs[0]:
                LANGUAGE_PROBLEMS.append(f"Cartella delle lingue non trovata: {folder}")
            continue
        for fname in files:
            stem, ext = os.path.splitext(fname)
            code = stem.lower()
            if ext.lower() != ".json" or code in found or code == "it":
                continue
            if not LANG_CODE_RE.fullmatch(code):
                LANGUAGE_PROBLEMS.append(f"Nome di file lingua non valido: {fname}")
                continue
            try:
                with open(os.path.join(folder, fname), encoding="utf-8-sig") as f:
                    data = json.load(f)
                strings = data["strings"]
                if not isinstance(strings, dict):
                    raise ValueError('"strings" non e\' un elenco di voci')
            except Exception as e:
                LANGUAGE_PROBLEMS.append(f"File di lingua non valido ({fname}): {e}")
                continue
            good = {k: v for k, v in strings.items() if isinstance(k, str) and _valid_translation(k, v)}
            if len(good) != len(strings):
                LANGUAGE_PROBLEMS.append(
                    f"{fname}: {len(strings) - len(good)} voci scartate (testo vuoto o segnaposto diversi)")
            label = data.get("language")
            found[code] = label.strip()[:40] if isinstance(label, str) and label.strip() else code.upper()
            pair = data.get("quotes")
            QUOTES[code] = pair if isinstance(pair, str) and len(pair) == 2 else "\u201c\u201d"
            TRANSLATIONS[code] = good
    LANGUAGES.clear()
    LANGUAGES["it"] = "Italiano"
    for code, label in sorted(found.items(), key=lambda item: item[1].casefold()):
        LANGUAGES[code] = label


load_languages()


def system_language():
    """Lingua del sistema operativo tra quelle disponibili (la prima in ordine di preferenza);
    se nessuna e' disponibile, l'inglese."""
    try:
        for name in QLocale.system().uiLanguages():
            code = name.replace("_", "-").split("-")[0].lower()
            if code in LANGUAGES:
                return code
    except Exception:
        pass
    return "en"


def resolve_language(setting):
    """Dal valore salvato ("auto" o un codice di LANGUAGES) alla lingua da usare."""
    return setting if setting in LANGUAGES else system_language()


def set_language(code):
    global LANG
    LANG = code if code in LANGUAGES else "en"


def tr(text, **values):
    """Testo nella lingua attuale. Il testo italiano e' anche la chiave: se manca la traduzione
    si vede l'italiano. I segnaposto {nome} si riempiono con values."""
    original = text
    if LANG != "it":
        text = (TRANSLATIONS.get(LANG) or {}).get(text) or (TRANSLATIONS.get("en") or {}).get(text, text)
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return original.format(**values)


def quoted(text):
    """Il testo tra virgolette nella forma della lingua attuale."""
    pair = QUOTES.get(LANG, "\u201c\u201d")
    return pair[0] + text + pair[1]


_qt_translator = None


def install_qt_translator(code):
    """Testi di Qt stesso (finestra dei colori, pulsanti standard) nella lingua scelta."""
    global _qt_translator
    app = QApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if code == "en":
        return
    try:
        folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator = QTranslator(app)
        if translator.load(f"qtbase_{code}", folder):
            app.installTranslator(translator)
            _qt_translator = translator
    except Exception:
        pass


THEMES = {
    "dark": {
        "label": "Scuro",
        "bg": "#2b2e36", "text": "#dfe3ea", "strong": "#ffffff", "panel": "#31343d",
        "border": "#23252b", "line": "#4a4f5c", "hover": "#3b3f4a", "raised": "#3a3e49",
        "raised_hover": "#454a58", "row_border": "#262930", "row_hover": "#343844",
        "toggle_bg": "#2f323b", "muted": "#9aa0ad", "input": "#1b1d23",
        "check_border": "#5a5f6e", "danger": "#e5484d", "solo": "#e0b422",
        "solo_text": "#1b1d23", "handle": "#ffffff", "handle_border": "#ffffff",
        "eye_on": "#ffffff", "eye_off": "#6b7080", "row_shown": "#ffffff",
        "row_hidden": "#8a90a0", "row_idle": "#5d6272", "hint": "#6b7080",
        "meter_text": "#9aa0ad", "dim_g": "#1d5e2b", "dim_y": "#6e5d12", "dim_r": "#6e1f1c",
        "duck": "#f0883e", "duck_bg": "#3d3328",
    },
    "light": {
        "label": "Chiaro",
        "bg": "#eceef2", "text": "#1f2330", "strong": "#000000", "panel": "#ffffff",
        "border": "#c5c9d3", "line": "#aab0bd", "hover": "#e3e6ee", "raised": "#dde1ea",
        "raised_hover": "#d2d7e2", "row_border": "#d5d9e2", "row_hover": "#e4e8f0",
        "toggle_bg": "#e2e5ec", "muted": "#5a6072", "input": "#ffffff",
        "check_border": "#8a90a0", "danger": "#d12f35", "solo": "#e0b422",
        "solo_text": "#1b1d23", "handle": "#ffffff", "handle_border": "#8a90a0",
        "eye_on": "#1f2330", "eye_off": "#9aa0ad", "row_shown": "#1f2330",
        "row_hidden": "#5a6072", "row_idle": "#9aa0ad", "hint": "#6b7285",
        "meter_text": "#555b6b", "dim_g": "#b9dfc1", "dim_y": "#ecdfa0", "dim_r": "#eab8b5",
        "duck": "#d9730d", "duck_bg": "#fff1e0",
    },
    "contrast": {
        "label": "Alto contrasto",
        "bg": "#000000", "text": "#ffffff", "strong": "#ffffff", "panel": "#000000",
        "border": "#ffffff", "line": "#ffffff", "hover": "#262626", "raised": "#1a1a1a",
        "raised_hover": "#333333", "row_border": "#808080", "row_hover": "#262626",
        "toggle_bg": "#000000", "muted": "#ffffff", "input": "#000000",
        "check_border": "#ffffff", "danger": "#ff6b6b", "solo": "#ffd400",
        "solo_text": "#000000", "handle": "#ffffff", "handle_border": "#ffffff",
        "eye_on": "#ffffff", "eye_off": "#bdbdbd", "row_shown": "#ffffff",
        "row_hidden": "#d0d0d0", "row_idle": "#8c8c8c", "hint": "#d0d0d0",
        "meter_text": "#ffffff", "dim_g": "#1d5e2b", "dim_y": "#6e5d12", "dim_r": "#6e1f1c",
        "duck": "#ff9f1a", "duck_bg": "#332000",
    },
}
DEFAULT_ACCENT = "#3b6fe0"
ACCENT_CHOICES = ["#3b6fe0", "#17a2b8", "#1fa971", "#e0b422", "#e0562b", "#c04be0"]
SCALE_MIN, SCALE_MAX = 80, 150
UI = {"scale": 1.0}
PALETTE = {}


def sc(n):
    """Misura in pixel adattata alla scala dell'interfaccia."""
    return max(1, int(round(n * UI["scale"])))


def _on_color(hex_color):
    """Colore del testo leggibile sopra uno sfondo (nero su chiaro, bianco su scuro)."""
    c = QColor(hex_color)
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
    return "#000000" if lum > 0.6 else "#ffffff"


def valid_accent(value):
    return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None


def configure_appearance(theme, accent, scale_pct):
    """Imposta tema, accento e scala (valori non validi tornano a quelli di default)."""
    if theme not in THEMES:
        theme = "dark"
    if not valid_accent(accent):
        accent = DEFAULT_ACCENT
    try:
        scale_pct = max(SCALE_MIN, min(SCALE_MAX, int(scale_pct)))
    except (TypeError, ValueError):
        scale_pct = 100
    UI["scale"] = scale_pct / 100
    PALETTE.clear()
    PALETTE.update(THEMES[theme])
    PALETTE["theme"] = theme
    PALETTE["accent"] = accent.lower()
    PALETTE["on_accent"] = _on_color(accent)


_STYLE_TEMPLATE = """
QWidget { background: ${bg}; color: ${text}; font-family: 'Segoe UI'; ${base_font} }
QToolTip { background: ${panel}; color: ${text}; border: 1px solid ${line}; }
QFrame#strip { background: ${panel}; border: 1px solid ${border}; }
QFrame#strip QLabel { background: transparent; border: none; }
QFrame#master { background: ${panel}; border: 1px solid ${accent}; }
QFrame#master QLabel { background: transparent; border: none; }
QLabel#name { font-weight: 600; font-size: ${p11}; }
QSlider::groove:vertical { background: ${accent}; width: ${p4}; border-radius: ${p2}; }
QSlider::handle:vertical { background: ${handle}; border: 1px solid ${handle_border};
                           height: ${p22}; width: ${p14}; margin: 0 -${p6}; border-radius: ${p6}; }
QSlider::groove:horizontal { background: ${accent}; height: ${p4}; border-radius: ${p2}; }
QSlider::handle:horizontal { background: ${handle}; border: 1px solid ${handle_border};
                             width: ${p14}; height: ${p14}; margin: -${p6} 0; border-radius: ${p6}; }
QPushButton { background: transparent; border: none; font-size: ${p16}; }
QPushButton:checked { color: ${danger}; }
QPushButton#key { font-size: ${p10}; border: 1px solid ${line};
                  border-radius: ${p3}; padding: ${p3}; }
QPushButton#key:hover { background: ${hover}; }
QLabel#panelTitle { background: ${raised}; font-weight: 600; padding: ${p5} ${p8}; }
QPushButton#panelTab { background: transparent; color: ${muted}; font-size: ${p12}; font-weight: 600;
                       border: none; border-radius: 0; padding: ${p5} ${p8}; }
QPushButton#panelTab:hover { background: ${row_hover}; color: ${strong}; }
QPushButton#panelTab:checked { background: ${raised}; color: ${strong}; }
QFrame#row { background: ${bg}; border-bottom: 1px solid ${row_border}; }
QFrame#row:hover { background: ${row_hover}; }
QFrame#row QLabel { background: transparent; }
QFrame#strip:disabled { background: ${bg}; }
QFrame#strip[ducked="true"] { background: ${duck_bg}; border: 1px solid ${duck}; }
QPushButton#toggleFonti { background: ${toggle_bg}; color: ${muted}; font-size: ${p10};
                          border-left: 1px solid ${border}; border-right: 1px solid ${border}; }
QPushButton#toggleFonti:hover { background: ${raised}; color: ${strong}; }
QSplitter::handle { background: ${border}; }
QSplitter::handle:hover { background: ${accent}; }
QMenu { background: ${panel}; border: 1px solid ${border}; padding: ${p3}; }
QMenu::item { padding: ${p5} ${p26}; }
QMenu::item:selected { background: ${accent}; color: ${on_accent}; }
QDialog QPushButton { background: ${raised}; border: 1px solid ${line};
                      border-radius: ${p3}; padding: ${p4} ${p16}; font-size: ${p12}; }
QDialog QPushButton:hover { background: ${raised_hover}; }
QLineEdit { background: ${input}; border: 1px solid ${line}; padding: ${p4}; }
QComboBox { background: ${input}; border: 1px solid ${line}; padding: ${p3} ${p8}; }
QSpinBox { background: ${input}; border: 1px solid ${line}; padding: ${p3}; padding-right: ${p20}; }
QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: ${p16};
                      border-left: 1px solid ${line}; background: ${raised}; }
QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: ${p16};
                        border-left: 1px solid ${line}; background: ${raised}; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: ${raised_hover}; }
QSpinBox::up-arrow { image: url("${arrow_up}"); width: ${p9}; height: ${p6}; }
QSpinBox::down-arrow { image: url("${arrow_down}"); width: ${p9}; height: ${p6}; }
QComboBox QAbstractItemView { background: ${panel}; border: 1px solid ${line};
                              selection-background-color: ${accent}; selection-color: ${on_accent}; }
QPushButton#solo { font-size: ${p11}; font-weight: 700; border: 1px solid ${line};
                   border-radius: ${p3}; min-width: ${p22}; padding: ${p2} ${p4}; }
QPushButton#solo:checked { background: ${solo}; color: ${solo_text}; border-color: ${solo}; }
QPushButton#bar { font-size: ${p14}; border: 1px solid transparent;
                  border-radius: ${p3}; padding: ${p2} ${p7}; }
QPushButton#bar:hover { background: ${raised}; }
QPushButton#bar:checked { background: ${accent}; color: ${on_accent}; }
QCheckBox::indicator { width: ${p14}; height: ${p14}; border: 1px solid ${check_border};
                       background: ${input}; border-radius: ${p2}; }
QCheckBox::indicator:checked { background: ${accent}; border-color: ${accent}; }
QTabWidget::pane { border: 1px solid ${border}; }
QTabBar::tab { padding: ${p6} ${p14}; background: ${panel}; }
QTabBar::tab:selected { background: ${accent}; color: ${on_accent}; }
QFrame#dropLine { background: ${accent}; border: none; }
"""


def _arrow_png(direction, color):
    """Piccola freccia (triangolo) in un PNG nella cartella temporanea: serve alle frecce dei
    campi numerici, che il foglio di stile non sa disegnare da solo. Torna il percorso."""
    w, h = sc(9), sc(6)
    folder = os.path.join(tempfile.gettempdir(), "AudioMixerUI")
    path = os.path.join(folder, f"arrow_{direction}_{color.lstrip('#')}_{w}.png")
    if not os.path.exists(path):
        try:
            os.makedirs(folder, exist_ok=True)
            img = QImage(w, h, QImage.Format_ARGB32)
            img.fill(0)
            pt = QPainter(img)
            pt.setRenderHint(QPainter.Antialiasing)
            pt.setPen(Qt.NoPen)
            pt.setBrush(QColor(color))
            if direction == "up":
                tri = [QPointF(0, h), QPointF(w, h), QPointF(w / 2, 0)]
            else:
                tri = [QPointF(0, 0), QPointF(w, 0), QPointF(w / 2, h)]
            pt.drawPolygon(QPolygonF(tri))
            pt.end()
            img.save(path)
        except Exception:
            pass
    return path.replace("\\", "/")


def build_style():
    """Foglio di stile per il tema, l'accento e la scala attuali."""
    values = dict(PALETTE)
    values["arrow_up"] = _arrow_png("up", PALETTE["text"])
    values["arrow_down"] = _arrow_png("down", PALETTE["text"])
    values.update({f"p{n}": f"{sc(n)}px" for n in range(0, 41)})
    values["base_font"] = "" if UI["scale"] == 1.0 else f"font-size: {sc(12)}px;"
    return Template(_STYLE_TEMPLATE).substitute(values)


configure_appearance("dark", DEFAULT_ACCENT, 100)


CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AudioMixer")
CONFIG = os.path.join(CONFIG_DIR, "hotkeys.json")
WINDOW_FILE = os.path.join(CONFIG_DIR, "window.json")


def resource_path(name):
    """Percorso di un file incluso (funziona sia da script sia da .exe)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


SOURCES_FILE = os.path.join(CONFIG_DIR, "sources.json")


def load_sources():
    """{nome app: {"visible": bool, "exe": percorso}}"""
    try:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_sources(sources):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(sources, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


PENDING_FILE = os.path.join(CONFIG_DIR, "reset_pending.json")


def load_pending():
    """Nomi delle fonti eliminate che vanno reimpostate alla prossima apparizione."""
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_pending(names):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(names), f, ensure_ascii=False)
    except Exception:
        pass


SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "close_to_tray": False,
    "start_minimized": False,
    "always_on_top": False,
    "order_mode": "custom",
    "theme": "dark",
    "accent": DEFAULT_ACCENT,
    "ui_scale": 100,
    "language": "auto",
    "fade_ms": 250,
}


def load_settings():
    """Impostazioni salvate, con i valori di default se il file manca o e' rovinato."""
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in DEFAULT_SETTINGS and type(v) is type(DEFAULT_SETTINGS[k]):
                    data[k] = v
    except Exception:
        pass
    if data["theme"] not in THEMES:
        data["theme"] = "dark"
    if not valid_accent(data["accent"]):
        data["accent"] = DEFAULT_ACCENT
    data["ui_scale"] = max(SCALE_MIN, min(SCALE_MAX, data["ui_scale"]))
    data["fade_ms"] = max(0, min(3000, data["fade_ms"]))
    if data["language"] != "auto" and not (isinstance(data["language"], str)
                                           and LANG_CODE_RE.fullmatch(data["language"])):
        data["language"] = "auto"
    return data


def save_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


LOG_FILE = os.path.join(CONFIG_DIR, "mixer.log")


def log_error(text):
    """Scrive un errore in mixer.log (cartella AudioMixer in %APPDATA%). Da pythonw/.exe non c'e' una console:
    senza questo file un errore all'avvio di Windows passerebbe inosservato."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text.rstrip() + "\n")
    except Exception:
        pass


def _log_excepthook(exc_type, exc, tb):
    import traceback
    log_error("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.__excepthook__(exc_type, exc, tb)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_NAME = "AudioMixer"


def autostart_command():
    """Comando da lanciare all'avvio di Windows (da .exe o da script)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def autostart_disabled_by_windows():
    """True se l'avvio e' stato disattivato da Gestione attivita' > Avvio: la voce "Run" resta
    nel registro ma Windows non la esegue piu'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as k:
            data, _ = winreg.QueryValueEx(k, RUN_NAME)
        return bool(data) and (data[0] & 1) == 1
    except Exception:
        return False


def autostart_enabled():
    """True se l'avvio con Windows e' davvero attivo (voce presente e non disattivata)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
    except Exception:
        return False
    return not autostart_disabled_by_windows()


def refresh_autostart_entry():
    """Se la voce di avvio c'e' ma punta a un percorso vecchio (script spostato, Python
    aggiornato, .exe rinominato) la riscrive con quello attuale. Non la riattiva se l'utente
    l'ha disattivata da Gestione attivita'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            current, _ = winreg.QueryValueEx(k, RUN_NAME)
            if current != autostart_command():
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
    except Exception:
        pass


def _clear_startup_disabled():
    """Toglie il blocco messo da Gestione attivita' (se c'e'), cosi' la voce riparte."""
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0,
                                winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, RUN_NAME)
            except FileNotFoundError:
                return
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_BINARY, bytes([2]) + bytes(11))
    except Exception:
        pass


def set_autostart(on):
    """Scrive o toglie la voce di avvio automatico. True se e' riuscito."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
                _clear_startup_disabled()
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


SOLO_FILE = os.path.join(CONFIG_DIR, "solo_recovery.json")


def load_solo_recovery():
    """Mute messi dal solo da rimettere a posto: {nome app: mute di prima}."""
    try:
        with open(SOLO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_solo_recovery(prev):
    try:
        if prev:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(SOLO_FILE, "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False)
        elif os.path.exists(SOLO_FILE):
            os.remove(SOLO_FILE)
    except Exception:
        pass


DUCK_FILE = os.path.join(CONFIG_DIR, "duck_recovery.json")
DUCKING_FILE = os.path.join(CONFIG_DIR, "ducking.json")


def new_duck_id():
    return os.urandom(4).hex()


def clean_duck_group(g):
    """Rimette in ordine i valori di un gruppo di ducking (file modificato a mano o vecchio)."""
    g["triggers"] = [n for n in g.get("triggers") or [] if isinstance(n, str)]
    g["targets"] = [n for n in g.get("targets") or []
                    if isinstance(n, str) and n not in g["triggers"]]
    if g.get("unit") not in ("%", "dB"):
        g["unit"] = "%"
    lo, hi = (5, 95) if g["unit"] == "%" else (1, 40)
    amount = g.get("amount")
    g["amount"] = max(lo, min(hi, amount if isinstance(amount, (int, float)) else 70))
    threshold = g.get("threshold")
    g["threshold"] = max(-60, min(0, threshold if isinstance(threshold, (int, float)) else -40))
    attack = g.get("attack")
    g["attack"] = max(5, min(2000, attack if isinstance(attack, (int, float)) else 80))
    release = g.get("release")
    g["release"] = max(20, min(5000, release if isinstance(release, (int, float)) else 800))
    hold = g.get("hold")
    g["hold"] = max(0, min(5000, hold if isinstance(hold, (int, float)) else 500))
    g["enabled"] = bool(g.get("enabled", True))
    return g


def _legacy_duck_group():
    """Il ducking a un gruppo solo, fino alla 0.3.5: se era configurato, diventa il primo gruppo
    la prima volta che si passa ai gruppi multipli."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        triggers, targets = raw.get("duck_triggers"), raw.get("duck_targets")
        if not (isinstance(triggers, list) and isinstance(targets, list) and triggers and targets):
            return None
        g = clean_duck_group({
            "triggers": triggers, "targets": targets,
            "enabled": bool(raw.get("duck_enabled", False)),
            "threshold": raw.get("duck_threshold", -40), "amount": raw.get("duck_amount", 70),
            "unit": raw.get("duck_unit", "%"), "attack": raw.get("duck_attack", 80),
            "release": raw.get("duck_release", 800), "hold": raw.get("duck_hold", 500)})
        g["id"] = new_duck_id()
        return g
    except Exception:
        return None


def load_ducking():
    """Gruppi di ducking: {"enabled": bool, "groups": [gruppo, ...]}. Un gruppo ha i suoi trigger,
    le sue app da abbassare, soglia, riduzione e tempi, e puo' essere disattivato singolarmente.
    Gruppi diversi possono avere la stessa app come bersaglio. Righe non valide vengono scartate
    senza fermare il programma."""
    data = {"enabled": True, "groups": []}
    migrating = not os.path.exists(DUCKING_FILE)
    try:
        with open(DUCKING_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data["enabled"] = bool(raw.get("enabled", True))
            seen = set()
            for g in raw.get("groups") or []:
                if not isinstance(g, dict):
                    continue
                gid = g.get("id")
                if not isinstance(gid, str) or not gid or gid in seen:
                    gid = new_duck_id()
                seen.add(gid)
                clean = clean_duck_group(dict(g))
                clean["id"] = gid
                data["groups"].append(clean)
    except Exception:
        pass
    if migrating and not data["groups"]:
        legacy = _legacy_duck_group()
        if legacy:
            data["groups"].append(legacy)
            save_ducking(data)
    return data


def save_ducking(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(DUCKING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


QUICK_FILE = os.path.join(CONFIG_DIR, "quick_devices.json")


def load_quick_devices():
    """Dispositivi per il cambio rapido dell'uscita predefinita:
    {"devices": [id, ...], "hotkey": str, "volumes": {id: 0..1}}. "devices" e' l'ordine del ciclo
    (tasto rapido e menu della tray); "volumes" ricorda l'ultimo volume master di ogni
    dispositivo, cosi' tornandoci si ritrova quello che aveva."""
    data = {"devices": [], "hotkey": "", "volumes": {}}
    try:
        with open(QUICK_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            seen = set()
            for d in raw.get("devices") or []:
                if isinstance(d, str) and d.startswith(DEV_PREFIX) and d not in seen:
                    seen.add(d)
                    data["devices"].append(d)
            hotkey = raw.get("hotkey")
            if isinstance(hotkey, str):
                data["hotkey"] = hotkey
            for k, v in (raw.get("volumes") or {}).items():
                if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool):
                    data["volumes"][k] = max(0.0, min(1.0, float(v)))
    except Exception:
        pass
    return data


def save_quick_devices(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(QUICK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def load_duck_recovery():
    """Volumi che il ducking aveva abbassato e non e' riuscito a rimettere: {app: volume voluto}."""
    try:
        with open(DUCK_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): max(0.0, min(1.0, float(v))) for k, v in data.items()}
    except Exception:
        return {}


def save_duck_recovery(vols):
    try:
        if vols:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(DUCK_FILE, "w", encoding="utf-8") as f:
                json.dump({k: round(v, 4) for k, v in vols.items()}, f, ensure_ascii=False)
        elif os.path.exists(DUCK_FILE):
            os.remove(DUCK_FILE)
    except Exception:
        pass


PRESETS_FILE = os.path.join(CONFIG_DIR, "presets.json")


def load_presets():
    """Preset salvati e valori in attesa: ({nome preset: dati}, {nome app: valori da
    applicare quando l'app compare})."""
    presets, pending = {}, {}
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw = data.get("presets")
            if isinstance(raw, dict):
                presets = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            raw = data.get("pending")
            if isinstance(raw, dict):
                pending = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    except Exception:
        pass
    return presets, pending


def save_presets(presets, pending):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump({"presets": presets, "pending": pending}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


AUTOMATIONS_FILE = os.path.join(CONFIG_DIR, "automations.json")
AUTOMATION_TYPES = ("app_open", "app_close", "output_device", "time")


def new_rule_id():
    return os.urandom(4).hex()


def _valid_rule(r):
    """Una regola valida ha un tipo noto, un preset (nome non vuoto) e il campo che le serve."""
    if not isinstance(r, dict) or r.get("type") not in AUTOMATION_TYPES:
        return False
    if not isinstance(r.get("preset"), str) or not r["preset"]:
        return False
    if r["type"] in ("app_open", "app_close"):
        return isinstance(r.get("app"), str) and bool(r["app"])
    if r["type"] == "output_device":
        return isinstance(r.get("device"), str) and bool(r["device"])
    return isinstance(r.get("time"), str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", r.get("time", ""))


def load_automations():
    """Preset automatici: {"enabled": bool, "rules": [regola, ...]}. Ogni regola ha un id
    stabile (serve a ricordare se e' gia' scattata oggi), puo' essere disattivata singolarmente,
    e ha: type ("app_open" / "app_close" / "output_device" / "time"), preset (nome), e il campo
    del suo tipo (app / device / time). Righe non valide vengono scartate senza fermare il
    programma."""
    data = {"enabled": True, "rules": []}
    try:
        with open(AUTOMATIONS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data["enabled"] = bool(raw.get("enabled", True))
            seen = set()
            for r in raw.get("rules") or []:
                if not _valid_rule(r):
                    continue
                rid = r.get("id")
                if not isinstance(rid, str) or not rid or rid in seen:
                    rid = new_rule_id()
                seen.add(rid)
                rule = {"id": rid, "enabled": bool(r.get("enabled", True)),
                        "type": r["type"], "preset": r["preset"]}
                if r["type"] in ("app_open", "app_close"):
                    rule["app"] = r["app"]
                elif r["type"] == "output_device":
                    rule["device"] = r["device"]
                else:
                    rule["time"] = r["time"]
                data["rules"].append(rule)
    except Exception:
        pass
    return data


def save_automations(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(AUTOMATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class Bridge(QObject):
    """Porta gli eventi dei tasti (thread esterno) nel thread dell'interfaccia."""
    pressed = Signal(str)
    recorded = Signal(str, str)
    call_main = Signal(object)
    preset_pressed = Signal(str)
    preset_recorded = Signal(str, str)
    quick_pressed = Signal()
    quick_recorded = Signal(str)


APP_ID = "audiomixer-single-instance"
APP_VERSION = "0.3.9"
DEV_PREFIX = "dev:"
DEVICE_ICONS = {"in": "\U0001F3A4", "out": "\U0001F3A7"}
SYSTEM_NAME = "Suoni di sistema"
_mutex = None


def already_running():
    """True se un'altra copia del mixer e' gia' in esecuzione (mutex di Windows,
    controllo atomico: funziona anche con molti clic ravvicinati)."""
    global _mutex
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _mutex = k32.CreateMutexW(None, False, "AudioMixerSingleInstance")
        return ctypes.get_last_error() == 183
    except Exception:
        return False


def app_name(session):
    """Nome leggibile dell'app che possiede la sessione audio."""
    p = session.Process
    if p is None:
        return SYSTEM_NAME
    try:
        n = p.name()
    except psutil.Error:
        return "?"
    return n[:-4] if n.lower().endswith(".exe") else n


def ask_text(parent, title, label, text=""):
    """Finestrella per scrivere un testo. Restituisce il testo, o None se annulli."""
    dlg = QInputDialog(parent)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setOkButtonText(tr("Ok"))
    dlg.setCancelButtonText(tr("Annulla"))
    if dlg.exec() == QDialog.Accepted:
        return dlg.textValue()
    return None


def ask_int(parent, title, label, value, lo, hi):
    """Finestrella per scegliere un numero intero. None se annulli."""
    dlg = QInputDialog(parent)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dlg.setInputMode(QInputDialog.IntInput)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setIntRange(lo, hi)
    dlg.setIntValue(max(lo, min(hi, value)))
    dlg.setOkButtonText(tr("Ok"))
    dlg.setCancelButtonText(tr("Annulla"))
    if dlg.exec() == QDialog.Accepted:
        return dlg.intValue()
    return None


def confirm(parent, title, text, ok_text=None):
    """Domanda con due pulsanti. True se confermi."""
    box = QMessageBox(parent)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(ok_text or tr("Ok"), QMessageBox.AcceptRole)
    box.addButton(tr("Annulla"), QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() == yes


def wrap_name(name):
    """Maiuscolo, con a capo possibile anche dentro i nomi molto lunghi."""
    return " ".join("\u200b".join(w) if len(w) > 9 else w
                    for w in name.upper().split(" "))


def app_exe(sessions):
    """Percorso di un eseguibile tra le sessioni del gruppo (serve per l'icona). Le app a piu'
    processi (Chrome, Discord, Microsoft Edge WebView2...) a volte hanno la sessione audio su un
    processo "figlio" protetto, a cui l'accesso e' negato: si prova ogni sessione del gruppo e,
    se nessuna riesce, anche il processo che le ha avviate."""
    procs = [s.Process for s in sessions if s.Process is not None]
    tried = set()
    for p in procs:
        try:
            path = p.exe()
            if path:
                return path
        except psutil.Error:
            pass
        tried.add(p.pid)
    for p in procs:
        try:
            parent = p.parent()
        except psutil.Error:
            continue
        if parent is None or parent.pid in tried:
            continue
        tried.add(parent.pid)
        try:
            path = parent.exe()
            if path:
                return path
        except psutil.Error:
            pass
    return None


def session_key(s):
    """Identificativo stabile di una sessione audio (gli oggetti si ricreano a ogni giro)."""
    try:
        return s.InstanceIdentifier or id(s)
    except Exception:
        return id(s)


def reset_audio(sessions):
    """Volume al 100% e audio non mutato."""
    for s in sessions:
        try:
            vol = s.SimpleAudioVolume
            vol.SetMasterVolume(1.0, None)
            vol.SetMute(0, None)
        except Exception:
            pass


def channel_peaks(meter):
    """Picco (0..1) del canale sinistro e destro di una sessione audio."""
    try:
        n = meter.GetMeteringChannelCount()
        if n >= 2:
            arr = (c_float * n)()
            meter.GetChannelsPeakValues(n, arr)
            return arr[0], arr[1]
    except Exception:
        pass
    p = meter.GetPeakValue()
    return p, p


class Meter(QWidget):
    """Meter stereo in stile OBS: due barre (sinistra e destra) con zone colorate
    attenuate sullo sfondo, livello acceso sopra, peak hold e scala in dB."""

    BAR_W = 5
    GAP = 1
    ZONES = [(-60, -20, "#1d5e2b", "#2fbf4a"),
             (-20, -9, "#6e5d12", "#e0b422"),
             (-9, 0, "#6e1f1c", "#d8342f")]

    def __init__(self):
        super().__init__()
        self.apply_scale()
        self.db = [-60.0, -60.0]
        self.hold = [-60.0, -60.0]
        self.hold_ticks = [0, 0]

    def apply_scale(self):
        self.BAR_W = sc(5)
        self.setFixedWidth(sc(38))
        self.setMinimumHeight(sc(80))
        self.update()

    def set_peaks(self, left, right):
        for i, peak in enumerate((left, right)):
            db = 20 * math.log10(peak) if peak > 1e-4 else -60.0
            self.db[i] = db if db > self.db[i] else max(db, self.db[i] - 1.5)
            if self.db[i] >= self.hold[i]:
                self.hold[i], self.hold_ticks[i] = self.db[i], 20
            elif self.hold_ticks[i] > 0:
                self.hold_ticks[i] -= 1
            else:
                self.hold[i] = max(self.db[i], self.hold[i] - 1.0)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        m = sc(7)
        dims = (PALETTE["dim_g"], PALETTE["dim_y"], PALETTE["dim_r"])
        span = max(1, h - 2 * m)
        bars_w = 2 * self.BAR_W + self.GAP

        def y(db):
            return m + (-max(-60.0, min(0.0, db))) / 60 * span

        for i in range(2):
            x = i * (self.BAR_W + self.GAP)
            for zi, (lo, hi, _dim, bright) in enumerate(self.ZONES):
                y_lo, y_hi = y(lo), y(hi)
                p.fillRect(QRectF(x, y_hi, self.BAR_W, y_lo - y_hi), QColor(dims[zi]))
                t = max(y(self.db[i]), y_hi)
                if t < y_lo:
                    p.fillRect(QRectF(x, t, self.BAR_W, y_lo - t), QColor(bright))
            if self.hold[i] > -60:
                col = next(b for lo, hi, d, b in self.ZONES if self.hold[i] <= hi)
                p.setPen(QPen(QColor(col), 2))
                yh = int(y(self.hold[i]))
                p.drawLine(x, yh, x + self.BAR_W, yh)

        step = 5 if span / 12 >= sc(13) else 10
        f = p.font()
        f.setPixelSize(sc(9))
        p.setFont(f)
        p.setPen(QColor(PALETTE["meter_text"]))
        for db in range(0, -61, -step):
            yy = y(db)
            p.drawLine(bars_w, int(yy), bars_w + sc(3), int(yy))
            p.drawText(QRectF(bars_w + sc(5), yy - sc(6), w - bars_w - sc(5), sc(12)),
                       Qt.AlignVCenter | Qt.AlignLeft, str(db))


class _EndpointVolume:
    """Volume e mute di un dispositivo con la stessa interfaccia di SimpleAudioVolume."""

    def __init__(self, endpoint):
        self.ep = endpoint

    def GetMasterVolume(self):
        return self.ep.GetMasterVolumeLevelScalar()

    def SetMasterVolume(self, value, ctx):
        self.ep.SetMasterVolumeLevelScalar(value, ctx)

    def GetMute(self):
        return self.ep.GetMute()

    def SetMute(self, muted, ctx):
        self.ep.SetMute(muted, ctx)


class _MeterHolder:
    """Fa trovare ai canali il meter del dispositivo, come per le sessioni delle app."""

    def __init__(self, meter):
        self.meter = meter

    def QueryInterface(self, _iface):
        if self.meter is None:
            raise RuntimeError("meter non disponibile")
        return self.meter


class DeviceSession:
    """Un dispositivo audio (IAudioEndpointVolume + IAudioMeterInformation) che si comporta
    come una sessione di app: cosi' canali, mute, preset e tasti rapidi lo trattano allo
    stesso modo delle app, senza codice a parte."""
    ProcessId = 0
    Process = None

    def __init__(self, dev_id, endpoint, meter):
        self.InstanceIdentifier = dev_id
        self.SimpleAudioVolume = _EndpointVolume(endpoint)
        self._ctl = _MeterHolder(meter)


def make_device_session(device):
    """Collega un AudioDevice di pycaw a volume e meter. None se il dispositivo non li ha."""
    try:
        endpoint = device.EndpointVolume
    except Exception:
        return None
    meter = None
    try:
        raw = device._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        meter = raw.QueryInterface(IAudioMeterInformation)
    except Exception:
        pass
    return DeviceSession(device.id, endpoint, meter)


class IChannelAudioVolumeMx(IUnknown):
    """Volume per canale di una sessione audio. Windows lo moltiplica per il volume principale
    (SimpleAudioVolume): la dissolvenza del mute agisce qui, cosi' lo slider, il ducking e i preset
    continuano a vedere il volume vero dell'app."""
    _iid_ = GUID("{1C158861-B533-4B30-B1CF-E853E51C59B8}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetChannelCount",
                  (["out"], POINTER(c_uint32), "pdwCount")),
        COMMETHOD([], HRESULT, "SetChannelVolume",
                  (["in"], c_uint32, "dwIndex"), (["in"], c_float, "fLevel"),
                  (["in"], POINTER(GUID), "EventContext")),
    )


FADE_FILE = os.path.join(CONFIG_DIR, "fade_recovery.json")
FADE_MAX_MS = 3000


def load_fade_recovery():
    """App rimaste a meta' dissolvenza (mixer chiuso di colpo): i loro canali vanno rimessi al 100%."""
    try:
        with open(FADE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {n for n in data if isinstance(n, str)} if isinstance(data, list) else set()
    except Exception:
        return set()


def save_fade_recovery(names):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(FADE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(names), f, ensure_ascii=False)
    except Exception:
        pass


def _channel_iface(session):
    """IChannelAudioVolume di una sessione di app, None se non c'e' (dispositivi compresi)."""
    if isinstance(session, DeviceSession):
        return None
    try:
        return session._ctl.QueryInterface(IChannelAudioVolumeMx)
    except Exception:
        return None


def _set_channels(ch, level):
    level = max(0.0, min(1.0, float(level)))
    for i in range(int(ch.GetChannelCount())):
        ch.SetChannelVolume(i, level, None)


class Fader(QObject):
    """Dissolvenze in entrata e in uscita (punto 11).

    Mute / smute: il volume dei canali della sessione scende a 0 e poi l'app viene mutata, oppure
    l'app viene smutata coi canali a 0 e poi risale. Il volume principale non si tocca mai.
    Preset: il volume principale scivola dal valore attuale a quello del preset. Se nel frattempo
    qualcun altro lo cambia (slider, ducking, l'app stessa), la dissolvenza si ferma e vince lui.
    Con durata 0, o dove l'interfaccia non c'e' (dispositivi), tutto resta istantaneo."""

    def __init__(self, parent=None, ms=0):
        super().__init__(parent)
        self.ms = 0
        self.set_duration(ms)
        self.mutes = {}
        self.ramps = {}
        self.recover = load_fade_recovery()
        self._saved = set(self.recover)
        self._last = time.monotonic()
        self._timer = QTimer(self, interval=15, timeout=self.step)

    def set_duration(self, ms):
        self.ms = max(0, min(FADE_MAX_MS, int(ms)))

    def busy(self):
        return bool(self.mutes or self.ramps)

    def _kick(self):
        if self.busy() and not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        self._save()

    def _save(self):
        names = {j["name"] for j in self.mutes.values() if j["name"]} | self.recover
        if names != self._saved:
            self._saved = names
            save_fade_recovery(names)

    def mute(self, sessions, muted, name=None):
        muted = bool(muted)
        target = 0.0 if muted else 1.0
        for s in sessions:
            key = session_key(s)
            job = self.mutes.get(key)
            if job is not None:
                job["target"] = target
                continue
            try:
                vol = s.SimpleAudioVolume
                current = bool(vol.GetMute())
            except Exception:
                continue
            ch = _channel_iface(s) if self.ms > 0 and current != muted else None
            if ch is None:
                try:
                    vol.SetMute(int(muted), None)
                except Exception:
                    pass
                continue
            try:
                if not muted:
                    _set_channels(ch, 0.0)
                    vol.SetMute(0, None)
            except Exception:
                self._restore(vol, ch, muted)
                continue
            self.mutes[key] = {"vol": vol, "ch": ch, "gain": 1.0 if muted else 0.0,
                               "target": target, "name": name}
        self._kick()

    def ramp(self, sessions, value):
        value = max(0.0, min(1.0, float(value)))
        for s in sessions:
            key = session_key(s)
            self.ramps.pop(key, None)
            try:
                vol = s.SimpleAudioVolume
                cur = float(vol.GetMasterVolume())
                if self.ms <= 0 or abs(cur - value) < 0.005:
                    vol.SetMasterVolume(value, None)
                    continue
            except Exception:
                continue
            self.ramps[key] = {"vol": vol, "from": cur, "to": value, "t": 0.0, "last": cur}
        self._kick()

    def cancel_ramp(self, sessions):
        """L'utente ha mosso lo slider: niente piu' dissolvenza di volume su queste sessioni."""
        for s in sessions:
            self.ramps.pop(session_key(s), None)

    def mute_state(self, sessions, actual):
        """Mute da mostrare: durante una dissolvenza, quello verso cui sta andando."""
        job = self.mutes.get(session_key(sessions[0])) if sessions else None
        return actual if job is None else job["target"] == 0.0

    def volume_state(self, sessions, actual):
        """Volume da salvare nei preset: durante una dissolvenza, quello di arrivo."""
        job = self.ramps.get(session_key(sessions[0])) if sessions else None
        return actual if job is None else job["to"]

    @staticmethod
    def _restore(vol, ch, muted):
        try:
            vol.SetMute(int(muted), None)
        except Exception:
            pass
        try:
            _set_channels(ch, 1.0)
        except Exception:
            pass

    def step(self):
        now = time.monotonic()
        dt = max(0.0, min(0.1, now - self._last))
        self._last = now
        rate = dt * 1000 / self.ms if self.ms > 0 else 1.0
        for key, job in list(self.mutes.items()):
            g, t = job["gain"], job["target"]
            g = min(t, g + rate) if g < t else max(t, g - rate)
            job["gain"] = g
            if g == t:
                del self.mutes[key]
                self._restore(job["vol"], job["ch"], t == 0.0)
                continue
            try:
                _set_channels(job["ch"], g * g)
            except Exception:
                del self.mutes[key]
                self._restore(job["vol"], job["ch"], t == 0.0)
        for key, job in list(self.ramps.items()):
            try:
                cur = float(job["vol"].GetMasterVolume())
                if abs(cur - job["last"]) > 0.01:
                    del self.ramps[key]
                    continue
                t = job["t"] = min(1.0, job["t"] + rate)
                v = job["from"] + (job["to"] - job["from"]) * t * t * (3 - 2 * t)
                job["vol"].SetMasterVolume(v, None)
                job["last"] = v
                if t >= 1.0:
                    del self.ramps[key]
            except Exception:
                self.ramps.pop(key, None)
        if not self.busy():
            self._timer.stop()
        self._save()

    def finish_all(self):
        """Alla chiusura: ogni dissolvenza arriva subito alla fine."""
        for job in self.mutes.values():
            self._restore(job["vol"], job["ch"], job["target"] == 0.0)
        for job in self.ramps.values():
            try:
                job["vol"].SetMasterVolume(job["to"], None)
            except Exception:
                pass
        self.mutes.clear()
        self.ramps.clear()
        self._timer.stop()
        self._save()

    def recover_sessions(self, groups, system):
        """Rimette al 100% i canali delle app rimaste a meta' dissolvenza l'ultima volta."""
        if not self.recover:
            return
        for name in list(self.recover):
            sessions = system if name == SYSTEM_NAME else groups.get(name)
            if not sessions:
                continue
            for s in sessions:
                if session_key(s) in self.mutes:
                    continue
                ch = _channel_iface(s)
                if ch is not None:
                    try:
                        _set_channels(ch, 1.0)
                    except Exception:
                        pass
            self.recover.discard(name)
        self._save()


FADER = None  # creato in Mixer.__init__, prima di strisce e preset


def fade_mute(sessions, muted, name=None):
    FADER.mute(sessions, muted, name)


def fade_volume(sessions, value):
    FADER.ramp(sessions, value)


def shown_mute(sessions, actual):
    return FADER.mute_state(sessions, actual)


def cancel_fade(sessions):
    FADER.cancel_ramp(sessions)


def limit_pct(info):
    """Limite massimo di volume (1-99 %) di una fonte; 100 = nessun limite."""
    v = info.get("max") if isinstance(info, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v < 100:
        return int(v)
    return 100


class LimitSlider(QSlider):
    """Slider del volume con il segno del limite massimo: una linea sul valore limite."""

    def __init__(self, orientation):
        super().__init__(orientation)
        self.limit = 100

    def set_limit(self, pct):
        pct = max(1, min(100, int(pct)))
        if pct != self.limit:
            self.limit = pct
            self.setToolTip(tr("Limite massimo: {pct}%", pct=pct) if pct < 100 else "")
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.limit >= 100:
            return
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        opt.sliderPosition = self.limit
        opt.sliderValue = self.limit
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        p = QPainter(self)
        p.setPen(QPen(QColor(PALETTE["danger"]), max(2, sc(2))))
        if self.orientation() == Qt.Vertical:
            y = handle.center().y()
            p.drawLine(0, y, self.width(), y)
        else:
            x = handle.center().x()
            p.drawLine(x, 0, x, self.height())


class Strip(QFrame):
    """Un canale: nome app, meter, slider volume, mute."""

    def __init__(self, name, exe=None, key="", on_bind=None, on_solo=None, on_menu=None):
        super().__init__()
        self.setObjectName("strip")
        self.name = name
        self.on_menu = on_menu
        self.sessions, self.meters = [], []

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignCenter)
        self._icon = QFileIconProvider().icon(QFileInfo(exe)) if exe else None
        if not exe:
            self.icon_label.setText("🔈")

        self.label = QLabel(wrap_name(name))
        self.label.setObjectName("name")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setToolTip(name)
        self.icon_label.setCursor(Qt.OpenHandCursor)
        self.label.setCursor(Qt.OpenHandCursor)
        self.meter = Meter()
        self.slider = LimitSlider(Qt.Vertical)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self.on_volume)
        self.mute = QPushButton("🔊")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self.on_mute)
        self.solo = QPushButton("S")
        self.solo.setObjectName("solo")
        self.solo.setCheckable(True)
        self.solo.setToolTip(tr("Solo: muta tutte le altre app"))
        self.solo.toggled.connect(lambda c: on_solo and on_solo(self.name, c))
        self.key_btn = QPushButton()
        self.key_btn.setObjectName("key")
        self.key_btn.setToolTip(tr("Clic per assegnare un tasto (Esc = rimuovi)"))
        self.key_btn.clicked.connect(lambda: on_bind and on_bind(self.name))
        self.set_key(key)

        body = QHBoxLayout()
        body.addWidget(self.meter)
        body.addWidget(self.slider)
        lay = QVBoxLayout(self)
        lay.addWidget(self.icon_label)
        lay.addWidget(self.label)
        lay.addLayout(body, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.mute)
        buttons.addWidget(self.solo)
        lay.addLayout(buttons)
        lay.addWidget(self.key_btn)
        self.apply_scale()

    def apply_scale(self):
        """Misure che dipendono dalla scala dell'interfaccia."""
        self.setFixedWidth(sc(104))
        self.icon_label.setFixedHeight(sc(36))
        self.label.setFixedHeight(sc(32))
        if self._icon is not None:
            self.icon_label.setPixmap(self._icon.pixmap(sc(32), sc(32)))
        self.meter.apply_scale()

    def contextMenuEvent(self, e):
        if self.on_menu:
            self.on_menu(self.name, e.globalPos())

    def grip_rect(self):
        """Zona da afferrare per trascinare il canale: icona e nome."""
        return self.icon_label.geometry().united(self.label.geometry())

    def set_output_hint(self, device_name):
        self.icon_label.setToolTip(tr("Uscita: {device}", device=device_name) if device_name else "")

    def set_limit(self, pct):
        """Limite massimo di volume (100 = nessuno): segno sullo slider."""
        self.slider.set_limit(pct)

    def set_ducked(self, flag):
        """Bordo colorato mentre il ducking sta abbassando questa app."""
        flag = bool(flag)
        if self.property("ducked") == flag:
            return
        self.setProperty("ducked", flag)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_title(self, text):
        self.label.setText(wrap_name(text))

    def set_key(self, key):
        self.key_btn.setText(f"⌨ {key.upper()}" if key else "⌨ " + tr("Assegna"))

    def set_sessions(self, sessions):
        self.sessions = sessions
        self.meters = []
        for s in sessions:
            try:
                self.meters.append(s._ctl.QueryInterface(IAudioMeterInformation))
            except Exception:
                pass

    def tick_meter(self):
        left = right = 0.0
        for m in self.meters:
            try:
                l, r = channel_peaks(m)
                left, right = max(left, l), max(right, r)
            except Exception:
                pass
        self.meter.set_peaks(left, right)

    def sync(self):
        """Allinea slider e mute con lo stato reale di Windows."""
        if not self.sessions:
            return
        try:
            vol = self.sessions[0].SimpleAudioVolume
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.setValue(round(vol.GetMasterVolume() * 100))
                self.slider.blockSignals(False)
            self.mute.blockSignals(True)
            self.mute.setChecked(shown_mute(self.sessions, bool(vol.GetMute())))
            self.mute.setText("🔇" if self.mute.isChecked() else "🔊")
            self.mute.blockSignals(False)
        except Exception:
            pass

    def on_volume(self, v):
        if v > self.slider.limit:
            v = self.slider.limit
            self.slider.blockSignals(True)
            self.slider.setValue(v)
            self.slider.blockSignals(False)
        cancel_fade(self.sessions)
        for s in self.sessions:
            try:
                s.SimpleAudioVolume.SetMasterVolume(v / 100, None)
            except Exception:
                pass

    def on_mute(self, checked):
        self.mute.setText("🔇" if checked else "🔊")
        fade_mute(self.sessions, checked, self.name)


class DeviceStrip(Strip):
    """Canale di un dispositivo: microfono (ingresso) oppure casse/cuffie (uscita).
    Volume, mute, meter e tasto di mute; niente solo e niente scelta dell'uscita."""

    def __init__(self, name, flow, key="", on_bind=None):
        super().__init__(name, None, key, on_bind, None, None)
        self.flow = flow
        self.default = False
        self.solo.hide()
        self.key_btn.setToolTip(tr("Clic per assegnare un tasto che muta / smuta questo dispositivo "
                                   "(Esc = rimuovi)"))
        self.show_icon()

    def show_icon(self):
        glyph = DEVICE_ICONS.get(self.flow, "\U0001F50A")
        badge = ""
        if self.default:
            badge = (f"<br><span style='font-size:{sc(8)}px; color:{PALETTE['accent']};'>"
                     f"{tr('PREDEFINITO')}</span>")
        self.icon_label.setText(f"<div align='center'>{glyph}{badge}</div>")

    def apply_scale(self):
        super().apply_scale()
        if hasattr(self, "flow"):
            self.show_icon()

    def set_default(self, default):
        """Segnala se e' il dispositivo predefinito di Windows (per le uscite e' lo stesso
        che comanda AUDIO PC)."""
        if default == self.default and self.icon_label.toolTip():
            return
        self.default = default
        self.show_icon()
        if not default:
            self.icon_label.setToolTip(tr("Microfono") if self.flow == "in" else tr("Casse / cuffie"))
        elif self.flow == "in":
            self.icon_label.setToolTip(tr("Microfono predefinito di Windows"))
        else:
            self.icon_label.setToolTip(tr("Uscita predefinita di Windows: è la stessa di AUDIO PC, "
                                          "volume e mute sono condivisi"))

    def set_output_hint(self, device_name):
        pass

    def set_title(self, text):
        super().set_title(text)
        self.label.setToolTip(text)


class RoutingError(Exception):
    pass


class OutputRouter:
    """Sceglie su quale dispositivo di uscita suona una singola app.

    Usa l'interfaccia NON documentata di Windows IAudioPolicyConfigFactory (la stessa che usano
    EarTrumpet e SoundVolumeView), raggiunta con RoGetActivationFactory
    ("Windows.Media.Internal.AudioPolicyConfig"). Servono due varianti dell'interfaccia:
      - Windows 10 21H2 e Windows 11 (build >= 21390):  IID {ab3d4648-...}
      - Windows 10 1803 ... 21H1:                        IID {2a59116d-...}
    Il metodo SetPersistedDefaultAudioEndpoint e' alla posizione 25 della vtable (dopo i 6 metodi
    di IInspectable e 19 metodi interni che non servono).

    Limiti noti:
      - e' un'API non ufficiale: Microsoft puo' cambiarla con un aggiornamento di Windows;
      - alcune app non cambiano uscita finche' non riaprono l'audio (o vanno riavviate);
      - non agisce su chi usa l'uscita in modo esclusivo o ha scelto un dispositivo da solo;
      - Windows ricorda la scelta anche quando il mixer e' chiuso.
    La chiamata gira in un PROCESSO SEPARATO (vedi apply): se l'API dovesse andare in crash su
    qualche versione di Windows, il mixer resta in piedi e mostra un errore (RoutingError).
    Dopo ogni scelta Windows viene riletto (GetPersistedDefaultAudioEndpoint) per verificarla.
    """

    CLASS_ID = "Windows.Media.Internal.AudioPolicyConfig"
    IID_21H2 = "{ab3d4648-e242-459f-b02f-541c70306324}"
    IID_DOWNLEVEL = "{2a59116d-6c4f-45e0-a74f-707e3fef9258}"
    IDX_SET = 25
    IDX_GET = 26
    BUILD_1803, BUILD_21H2 = 17134, 21390
    MMDEVAPI_TOKEN = r"\\?\SWD#MMDEVAPI#"
    RENDER_INTERFACE = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"

    def __init__(self):
        self._ptr = None
        self._api = None
        self._raw = None

    @classmethod
    def pack_device_id(cls, device_id):
        return f"{cls.MMDEVAPI_TOKEN}{device_id}{cls.RENDER_INTERFACE}" if device_id else None

    def _load(self):
        if self._api is not None:
            return self._api
        import ctypes
        if not sys.platform.startswith("win"):
            raise RoutingError(tr("Disponibile solo su Windows."))
        try:
            combase = ctypes.WinDLL("combase")
            create = combase.WindowsCreateString
            create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
            create.restype = ctypes.c_long
            delete = combase.WindowsDeleteString
            delete.argtypes = [ctypes.c_void_p]
            delete.restype = ctypes.c_long
            get_factory = combase.RoGetActivationFactory
            get_factory.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID),
                                    ctypes.POINTER(ctypes.c_void_p)]
            get_factory.restype = ctypes.c_long
            raw = combase.WindowsGetStringRawBuffer
            raw.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            raw.restype = ctypes.c_wchar_p
            self._raw = raw
        except Exception as e:
            raise RoutingError(tr("Libreria di Windows non disponibile ({err}).", err=e))
        self._api = (create, delete, get_factory)
        return self._api

    @contextlib.contextmanager
    def _hstring(self, value):
        import ctypes
        create, delete, _ = self._load()
        handle = ctypes.c_void_p()
        if value is not None:
            hr = create(value, len(value), ctypes.byref(handle))
            if hr < 0:
                raise RoutingError(tr("WindowsCreateString non riuscita (0x{code}).",
                                      code=f"{hr & 0xFFFFFFFF:08X}"))
        try:
            yield handle
        finally:
            if handle:
                delete(handle)

    def _open(self):
        if self._ptr:
            return
        import ctypes
        _, _, get_factory = self._load()
        build = sys.getwindowsversion().build
        if build < self.BUILD_1803:
            raise RoutingError(tr("Serve Windows 10 aprile 2018 (1803) o successivo."))
        order = ([self.IID_21H2, self.IID_DOWNLEVEL] if build >= self.BUILD_21H2
                 else [self.IID_DOWNLEVEL, self.IID_21H2])
        last_hr = 0
        for iid in order:
            ptr = ctypes.c_void_p()
            with self._hstring(self.CLASS_ID) as cls:
                last_hr = get_factory(cls, ctypes.byref(GUID(iid)), ctypes.byref(ptr))
            if last_hr >= 0 and ptr:
                self._ptr = ptr
                return
        raise RoutingError(tr("Questa versione di Windows non offre l'interfaccia (errore 0x{code}).",
                              code=f"{last_hr & 0xFFFFFFFF:08X}"))

    def apply(self, pids, device_id):
        """Manda l'audio dei processi `pids` al dispositivo `device_id` (None = predefinito).
        Gira in un processo separato (puo' impiegare qualche secondo: chiamalo da un thread)."""
        pids = sorted({p for p in pids if p})
        if not pids:
            return
        if not sys.platform.startswith("win"):
            raise RoutingError(tr("Disponibile solo su Windows."))
        fd, path = tempfile.mkstemp(prefix="audiomixer_", suffix=".txt")
        os.close(fd)
        cmd = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, os.path.abspath(__file__)])
        cmd += ["--set-output", path, device_id or "-", ",".join(str(p) for p in pids)]
        try:
            proc = subprocess.run(cmd, timeout=60,
                                  env={**os.environ, "AUDIOMIXER_LANG": LANG},
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except subprocess.TimeoutExpired:
            raise RoutingError(tr("Il processo di servizio non ha risposto in tempo."))
        except Exception as e:
            raise RoutingError(tr("Non sono riuscito ad avviare il processo di servizio ({err}).", err=e))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if text == "OK":
            return
        if text.startswith("ERR "):
            raise RoutingError(text[4:])
        raise RoutingError(tr("Il processo di servizio si è chiuso in modo anomalo (codice {code}): "
                              "l'interfaccia di Windows non è compatibile con questa versione.",
                              code=proc.returncode))

    def apply_direct(self, pids, device_id):
        """La chiamata vera all'API di Windows (eseguita dal processo di servizio)."""
        import ctypes
        pids = [p for p in pids if p]
        if not pids:
            return
        self._open()
        packed = self.pack_device_id(device_id)
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_func = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_void_p)(vtable[self.IDX_SET])
        get_func = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(ctypes.c_void_p))(vtable[self.IDX_GET])
        errors = []
        for pid in pids:
            try:
                for role in (0, 1):
                    with self._hstring(packed) as handle:
                        hr = int(set_func(self._ptr, pid, 0, role, handle))
                    if hr < 0:
                        raise RoutingError(tr("errore 0x{code}", code=f"{hr & 0xFFFFFFFF:08X}"))
                    self._verify(get_func, pid, role, packed)
            except RoutingError as e:
                errors.append(str(e))
        if errors and len(errors) == len(pids):
            raise RoutingError(errors[0])

    def _verify(self, get_func, pid, role, packed):
        """Rilegge da Windows la scelta appena fatta: se non coincide, non ha avuto effetto."""
        import ctypes
        out = ctypes.c_void_p()
        hr = int(get_func(self._ptr, pid, 0, role, ctypes.byref(out)))
        if hr < 0:
            return
        try:
            value = self._raw(out, ctypes.byref(ctypes.c_uint32())) if out else ""
        finally:
            if out:
                self._load()[1](out)
        if (value or "").lower() != (packed or "").lower():
            raise RoutingError(tr("Windows non ha applicato la scelta (la verifica non coincide)."))


def cli_set_output(result_path, device_id, pids_csv):
    """Modalita' 'processo di servizio': python mixer.py --set-output <file> <dispositivo|-> <pid,pid>
    Scrive 'OK' oppure 'ERR <messaggio>' nel file indicato."""
    set_language(os.environ.get("AUDIOMIXER_LANG", "it"))
    try:
        pids = [int(p) for p in pids_csv.split(",") if p.strip()]
        OutputRouter().apply_direct(pids, None if device_id == "-" else device_id)
        msg = "OK"
    except Exception as e:
        msg = "ERR " + (str(e) or e.__class__.__name__)
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass
    return 0


class DefaultDeviceSwitcher:
    """Cambia il dispositivo di uscita PREDEFINITO di Windows (quello della pagina Audio delle
    Impostazioni), non l'uscita di una singola app (quella la fa OutputRouter).

    Usa l'interfaccia non documentata IPolicyConfig (CLSID {870af99c-171d-4f9e-af0d-e63df40c2bc9}),
    la stessa usata da molti programmi di terze parti per cambiare il dispositivo predefinito da
    riga di comando, stabile da Windows Vista a Windows 11. Il metodo usato, SetDefaultEndpoint,
    e' in posizione 13 della vtable (dopo i 3 di IUnknown e 10 metodi che non servono). Viene
    chiamato tre volte, una per ruolo (Console, Multimedia, Comunicazioni), come fa la pagina
    Audio di Windows quando si sceglie un dispositivo dall'elenco.

    Limiti noti:
      - e' un'interfaccia non ufficiale: Microsoft potrebbe cambiarla con un aggiornamento;
      - alcune app leggono il dispositivo predefinito solo all'avvio e non lo riaggiornano.
    La chiamata gira in un PROCESSO SEPARATO (vedi apply), come per l'uscita delle singole app:
    se l'interfaccia dovesse andare in crash su qualche versione di Windows, il mixer resta in
    piedi e mostra un errore. Dopo ogni scelta Windows viene riletto per verificarla.
    """

    CLSID = "{870af99c-171d-4f9e-af0d-e63df40c2bc9}"
    IID = "{f8679f50-850a-41cf-9c72-430f290290c8}"
    IDX_SET_DEFAULT = 13
    ROLES = (0, 1, 2)

    def __init__(self):
        self._ptr = None

    def _open(self):
        if self._ptr:
            return
        import ctypes
        if not sys.platform.startswith("win"):
            raise RoutingError(tr("Disponibile solo su Windows."))
        try:
            ole32 = ctypes.WinDLL("ole32")
            create = ole32.CoCreateInstance
            create.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32,
                               ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
            create.restype = ctypes.c_long
        except Exception as e:
            raise RoutingError(tr("Libreria di Windows non disponibile ({err}).", err=e))
        ptr = ctypes.c_void_p()
        hr = create(ctypes.byref(GUID(self.CLSID)), None, CLSCTX_ALL,
                   ctypes.byref(GUID(self.IID)), ctypes.byref(ptr))
        if hr < 0 or not ptr:
            raise RoutingError(tr("Questa versione di Windows non offre l'interfaccia (errore 0x{code}).",
                                  code=f"{hr & 0xFFFFFFFF:08X}"))
        self._ptr = ptr

    def apply(self, device_id):
        """Rende `device_id` il dispositivo di uscita predefinito di Windows.
        Gira in un processo separato (chiamalo da un thread)."""
        if not device_id:
            return
        if not sys.platform.startswith("win"):
            raise RoutingError(tr("Disponibile solo su Windows."))
        fd, path = tempfile.mkstemp(prefix="audiomixer_", suffix=".txt")
        os.close(fd)
        cmd = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, os.path.abspath(__file__)])
        cmd += ["--set-default-device", path, device_id]
        try:
            proc = subprocess.run(cmd, timeout=60,
                                  env={**os.environ, "AUDIOMIXER_LANG": LANG},
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except subprocess.TimeoutExpired:
            raise RoutingError(tr("Il processo di servizio non ha risposto in tempo."))
        except Exception as e:
            raise RoutingError(tr("Non sono riuscito ad avviare il processo di servizio ({err}).", err=e))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if text == "OK":
            return
        if text.startswith("ERR "):
            raise RoutingError(text[4:])
        raise RoutingError(tr("Il processo di servizio si è chiuso in modo anomalo (codice {code}): "
                              "l'interfaccia di Windows non è compatibile con questa versione.",
                              code=proc.returncode))

    def apply_direct(self, device_id):
        """La chiamata vera all'API di Windows (eseguita dal processo di servizio)."""
        import ctypes
        self._open()
        vtable = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        set_func = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_int)(vtable[self.IDX_SET_DEFAULT])
        errors = []
        for role in self.ROLES:
            hr = int(set_func(self._ptr, device_id, role))
            if hr < 0:
                errors.append(tr("errore 0x{code}", code=f"{hr & 0xFFFFFFFF:08X}"))
        if len(errors) == len(self.ROLES):
            raise RoutingError(errors[0])
        self._verify(device_id)

    @staticmethod
    def _verify(device_id):
        """Rilegge da Windows la scelta appena fatta: se non coincide, non ha avuto effetto."""
        try:
            enumerator = AudioUtilities.GetDeviceEnumerator()
            current = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, 1).GetId()
        except Exception:
            return
        if (current or "").lower() != (device_id or "").lower():
            raise RoutingError(tr("Windows non ha applicato la scelta (la verifica non coincide)."))


def cli_set_default_device(result_path, device_id):
    """Modalita' 'processo di servizio': python mixer.py --set-default-device <file> <dispositivo>
    Scrive 'OK' oppure 'ERR <messaggio>' nel file indicato."""
    set_language(os.environ.get("AUDIOMIXER_LANG", "it"))
    try:
        DefaultDeviceSwitcher().apply_direct(device_id)
        msg = "OK"
    except Exception as e:
        msg = "ERR " + (str(e) or e.__class__.__name__)
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass
    return 0


def _scale_fixed_strip(w):
    """Misure delle strip fisse (AUDIO PC e Suoni di sistema) secondo la scala."""
    w.setFixedWidth(sc(104))
    w.icon.setFixedHeight(sc(36))
    w.label.setFixedHeight(sc(32))
    w._spacer.changeSize(0, sc(30), QSizePolicy.Minimum, QSizePolicy.Fixed)
    w.meter.apply_scale()
    w.layout().invalidate()


class SystemStrip(QFrame):
    """Suoni di sistema (notifiche e avvisi di Windows): canale fisso, accanto ad AUDIO PC.
    Windows crea questa sessione audio solo dopo il primo suono di sistema riprodotto:
    finche' non esiste il canale resta disabilitato, come quello di un'app chiusa."""

    def __init__(self):
        super().__init__()
        self.setObjectName("master")
        self.sessions, self.meters = [], []

        self.icon = QLabel("\U0001F514")
        self.icon.setAlignment(Qt.AlignCenter)
        self.label = QLabel(wrap_name(tr("Suoni di sistema")))
        self.label.setObjectName("name")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setToolTip(tr("Notifiche e avvisi di Windows.\n"
                                 "Il canale si attiva dopo il primo suono di sistema."))
        self.meter = Meter()
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self.on_volume)
        self.mute = QPushButton("\U0001F50A")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self.on_mute)

        body = QHBoxLayout()
        body.addWidget(self.meter)
        body.addWidget(self.slider)
        lay = QVBoxLayout(self)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addLayout(body, 1)
        lay.addWidget(self.mute)
        self._spacer = QSpacerItem(0, 30, QSizePolicy.Minimum, QSizePolicy.Fixed)
        lay.addItem(self._spacer)
        self.apply_scale()
        self.setEnabled(False)

    def apply_scale(self):
        _scale_fixed_strip(self)

    def retranslate(self):
        self.label.setText(wrap_name(tr("Suoni di sistema")))
        self.label.setToolTip(tr("Notifiche e avvisi di Windows.\n"
                                 "Il canale si attiva dopo il primo suono di sistema."))

    def set_sessions(self, sessions):
        """Le sessioni dei suoni di sistema (ce n'e' una per ogni dispositivo di uscita)."""
        self.sessions = sessions
        self.meters = []
        for s in sessions:
            try:
                self.meters.append(s._ctl.QueryInterface(IAudioMeterInformation))
            except Exception:
                pass
        self.setEnabled(bool(sessions))

    def sync(self):
        """Allinea slider e mute con lo stato reale di Windows."""
        if not self.sessions:
            return
        try:
            vol = self.sessions[0].SimpleAudioVolume
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.setValue(round(vol.GetMasterVolume() * 100))
                self.slider.blockSignals(False)
            muted = shown_mute(self.sessions, bool(vol.GetMute()))
            self.mute.blockSignals(True)
            self.mute.setChecked(muted)
            self.mute.setText("\U0001F507" if muted else "\U0001F50A")
            self.mute.blockSignals(False)
        except Exception:
            pass

    def tick_meter(self):
        left = right = 0.0
        for m in self.meters:
            try:
                l, r = channel_peaks(m)
                left, right = max(left, l), max(right, r)
            except Exception:
                pass
        self.meter.set_peaks(left, right)

    def on_volume(self, v):
        cancel_fade(self.sessions)
        for s in self.sessions:
            try:
                s.SimpleAudioVolume.SetMasterVolume(v / 100, None)
            except Exception:
                pass

    def on_mute(self, checked):
        self.mute.setText("\U0001F507" if checked else "\U0001F50A")
        fade_mute(self.sessions, checked, SYSTEM_NAME)


class MasterStrip(QFrame):
    """Volume generale del dispositivo di uscita predefinito di Windows."""

    def __init__(self):
        super().__init__()
        self.setObjectName("master")
        self.dev_id = None
        self.endpoint = None
        self.meter_if = None

        self.icon = QLabel("\U0001F50A")
        self.icon.setAlignment(Qt.AlignCenter)
        self.label = QLabel(tr("AUDIO PC"))
        self.label.setObjectName("name")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.meter = Meter()
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self.on_volume)
        self.mute = QPushButton("\U0001F50A")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self.on_mute)

        body = QHBoxLayout()
        body.addWidget(self.meter)
        body.addWidget(self.slider)
        lay = QVBoxLayout(self)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addLayout(body, 1)
        lay.addWidget(self.mute)
        self._spacer = QSpacerItem(0, 30, QSizePolicy.Minimum, QSizePolicy.Fixed)
        lay.addItem(self._spacer)
        self.apply_scale()
        self.setEnabled(False)

    def apply_scale(self):
        _scale_fixed_strip(self)

    def retranslate(self):
        self.label.setText(tr("AUDIO PC"))

    def bind(self, device):
        """Collega la strip a un dispositivo (AudioDevice di pycaw); None = nessun dispositivo."""
        self.endpoint = self.meter_if = None
        self.dev_id = None
        if device is not None:
            self.dev_id = device.id
            self.label.setToolTip(device.FriendlyName or "")
            try:
                self.endpoint = device.EndpointVolume
            except Exception:
                pass
            try:
                raw = device._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
                self.meter_if = raw.QueryInterface(IAudioMeterInformation)
            except Exception:
                pass
        self.setEnabled(self.endpoint is not None)
        self.sync()

    def sync(self):
        """Allinea slider e mute allo stato reale di Windows (anche se cambia dai tasti)."""
        if self.endpoint is None:
            return
        try:
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.setValue(round(self.endpoint.GetMasterVolumeLevelScalar() * 100))
                self.slider.blockSignals(False)
            muted = bool(self.endpoint.GetMute())
            self.mute.blockSignals(True)
            self.mute.setChecked(muted)
            self.mute.setText("\U0001F507" if muted else "\U0001F50A")
            self.mute.blockSignals(False)
        except Exception:
            pass

    def tick_meter(self):
        left = right = 0.0
        if self.meter_if is not None:
            try:
                left, right = channel_peaks(self.meter_if)
            except Exception:
                pass
        self.meter.set_peaks(left, right)

    def on_volume(self, v):
        try:
            self.endpoint.SetMasterVolumeLevelScalar(v / 100, None)
        except Exception:
            pass

    def on_mute(self, checked):
        self.mute.setText("\U0001F507" if checked else "\U0001F50A")
        try:
            self.endpoint.SetMute(int(checked), None)
        except Exception:
            pass


class EyeToggle(QAbstractButton):
    """Pulsante occhio disegnato: acceso = fonte mostrata nel mixer."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.apply_scale()
        self.setCursor(Qt.PointingHandCursor)

    def apply_scale(self):
        self.setFixedSize(sc(24), sc(24))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        col = QColor(PALETTE["eye_on"] if on else PALETTE["eye_off"])
        k = self.width() / 24
        p.scale(k, k)
        p.translate(1, 1)
        p.setPen(QPen(col, 1.7))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(2.5, 6.5, 17, 9))
        p.setBrush(col)
        p.drawEllipse(QPointF(11, 11), 2.6, 2.6)
        if not on:
            p.setPen(QPen(col, 2))
            p.drawLine(4, 18, 18, 4)


FONTI_MIN, FONTI_MAX, FONTI_DEFAULT = 210, 420, 220
TOGGLE_W = 16


class ElidedLabel(QLabel):
    """Etichetta che accorcia il testo con "..." se non ci sta, senza allargare nulla."""

    def __init__(self, text=""):
        super().__init__()
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self._elide()

    def setFullText(self, text):
        self._full = text
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        w = self.width()
        t = self.fontMetrics().elidedText(self._full, Qt.ElideRight, w) if w > 1 else self._full
        if t != self.text():
            QLabel.setText(self, t)


class SourceRow(QFrame):
    """Una riga dell'elenco Fonti: icona, nome e occhio."""

    def __init__(self, name, exe, visible, on_visible, on_rename, on_delete, title=None, kind=None):
        super().__init__()
        self.setObjectName("row")
        self.name = name
        self.kind = kind
        self.on_rename = on_rename
        self.on_delete = on_delete
        self.active = True
        self.default = False
        self._title = (title or name).upper()

        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self._icon = QFileIconProvider().icon(QFileInfo(exe)) if exe else None
        if not exe:
            self.icon.setText(DEVICE_ICONS.get(kind, "🔈"))
        self.label = ElidedLabel(self._title)
        self.label.setToolTip(name if not kind else (title or name))

        self.eye = EyeToggle()
        self.eye.setChecked(visible)
        self.eye.setToolTip(tr("Mostra / nascondi nel mixer"))
        self.eye.toggled.connect(lambda v: (on_visible(name, v), self.refresh_style()))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 6, 3)
        lay.setSpacing(8)
        lay.addWidget(self.icon)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.eye)
        self.apply_scale()
        self.refresh_style()

    def apply_scale(self):
        self.icon.setFixedSize(sc(20), sc(20))
        if self._icon is not None:
            self.icon.setPixmap(self._icon.pixmap(sc(20), sc(20)))
        self.eye.apply_scale()
        self.label._elide()

    def set_title(self, text):
        self._title = text.upper()
        self.label.setFullText(self._title + ("  \u2605" if self.default else ""))

    def set_default(self, default):
        """Stella accanto al dispositivo predefinito di Windows."""
        if default != self.default:
            self.default = default
            self.set_title(self._title)

    def grip_rect(self):
        """Zona da afferrare per trascinare la riga (l'occhio riceve da solo i suoi clic)."""
        return self.rect()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        rename = menu.addAction(tr("Rinomina…"))
        busy = tr("Elimina (dispositivo collegato)") if self.kind else tr("Elimina (app in uso)")
        delete = menu.addAction(tr("Elimina") if not self.active else busy)
        delete.setEnabled(not self.active)
        chosen = menu.exec(e.globalPos())
        if chosen == rename:
            self.on_rename(self.name)
        elif chosen == delete:
            self.on_delete(self.name)

    def refresh_style(self):
        if not self.active:
            color = PALETTE["row_idle"]
        elif self.eye.isChecked():
            color = PALETTE["row_shown"]
        else:
            color = PALETTE["row_hidden"]
        self.label.setStyleSheet(f"color: {color}; background: transparent;")

    def set_active(self, active):
        if active != self.active:
            self.active = active
            idle = tr("Dispositivo non collegato") if self.kind else tr("Al momento non riproduce audio")
            self.label.setToolTip("" if active else idle)
            self.refresh_style()


class SettingsDialog(QDialog):
    """Finestra Impostazioni (le modifiche si applicano subito)."""

    def __init__(self, mixer, tab=0):
        super().__init__(mixer)
        self.setWindowTitle(tr("Impostazioni"))
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        general = QWidget()
        gl = QVBoxLayout(general)
        gl.setAlignment(Qt.AlignTop)
        gl.setSpacing(10)
        for key, text in [("close_to_tray", tr("Chiudi con la X = nascondi nella tray")),
                          ("start_minimized", tr("Avvia ridotto nella tray")),
                          ("always_on_top", tr("Sempre in primo piano"))]:
            cb = QCheckBox(text)
            cb.setChecked(bool(mixer.settings.get(key)))
            cb.toggled.connect(lambda v, k=key: mixer.set_option(k, v))
            gl.addWidget(cb)
        self.autostart = QCheckBox(tr("Avvia con Windows"))
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self.on_autostart)
        gl.addWidget(self.autostart)

        fade_row = QHBoxLayout()
        fade_label = QLabel(tr("Dissolvenza"))
        fade_tip = tr("Durata del fade quando muti, smuti, usi il solo o cambi preset.\n"
                      "0 = cambi istantanei.")
        fade_label.setToolTip(fade_tip)
        fade_row.addWidget(fade_label)
        fade_row.addStretch(1)
        self.fade_box = QSpinBox()
        self.fade_box.setRange(0, FADE_MAX_MS)
        self.fade_box.setSingleStep(50)
        self.fade_box.setSuffix(" ms")
        self.fade_box.setSpecialValueText(tr("Disattivata"))
        self.fade_box.setToolTip(fade_tip)
        self.fade_box.setValue(int(mixer.settings.get("fade_ms", 0)))
        self.fade_box.valueChanged.connect(lambda v: mixer.set_option("fade_ms", int(v)))
        fade_row.addWidget(self.fade_box)
        gl.addLayout(fade_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(tr("Lingua")))
        lang_row.addStretch(1)
        self.lang_box = QComboBox()
        self.lang_box.addItem(tr("Automatica (lingua del sistema)"), "auto")
        for code, label in LANGUAGES.items():
            self.lang_box.addItem(label, code)
        self.lang_box.setCurrentIndex(
            max(0, self.lang_box.findData(mixer.settings.get("language", "auto"))))
        self.lang_box.setToolTip(tr("Cartella delle lingue: {path}\nFile di lingua trovati: {count}",
                                    path=LANGUAGE_DIRS[0] if LANGUAGE_DIRS else "",
                                    count=len(LANGUAGES) - 1))
        self.lang_box.currentIndexChanged.connect(self.on_language)
        lang_row.addWidget(self.lang_box)
        gl.addLayout(lang_row)

        self.mixer = mixer

        gl.addWidget(QLabel(tr("Cambio rapido dispositivo")))
        quick_hint = QLabel(tr("Spunta le uscite tra cui passare: un tasto o il menu della tray "
                               "passano alla successiva. Ogni dispositivo ricorda il proprio "
                               "volume."))
        quick_hint.setWordWrap(True)
        quick_hint.setStyleSheet(f"color: {PALETTE['hint']};")
        gl.addWidget(quick_hint)
        outs = [n for n, i in mixer.sources.items() if i.get("device") == "out"]
        chosen = set(mixer.quick.get("devices", []))
        for n in outs:
            cb = QCheckBox(mixer.display_name(n))
            cb.setChecked(n in chosen)
            cb.toggled.connect(lambda v, n=n: self.on_quick_toggle(n, v))
            gl.addWidget(cb)
        if not outs:
            gl.addWidget(QLabel(tr("(nessuna uscita conosciuta)")))
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel(tr("Tasto rapido")))
        key_row.addStretch(1)
        self.quick_key_btn = QPushButton()
        self.quick_key_btn.setObjectName("key")
        self.quick_key_btn.setToolTip(tr("Clic per assegnare un tasto globale (Esc = rimuovi)"))
        self.quick_key_btn.clicked.connect(self.on_quick_key)
        self._update_quick_key_btn()
        key_row.addWidget(self.quick_key_btn)
        gl.addLayout(key_row)
        look = QWidget()
        al = QVBoxLayout(look)
        al.setAlignment(Qt.AlignTop)
        al.setSpacing(12)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel(tr("Tema")))
        theme_row.addStretch(1)
        self.theme_box = QComboBox()
        for key, th in THEMES.items():
            self.theme_box.addItem(tr(th["label"]), key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(mixer.settings.get("theme"))))
        self.theme_box.currentIndexChanged.connect(
            lambda i: mixer.change_appearance(theme=self.theme_box.itemData(i)))
        theme_row.addWidget(self.theme_box)
        al.addLayout(theme_row)

        al.addWidget(QLabel(tr("Colore d'accento")))
        swatch_row = QHBoxLayout()
        self.swatches = {}
        for color in ACCENT_CHOICES:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(color)
            btn.clicked.connect(lambda _=False, c=color: self.pick_accent(c))
            self.swatches[color] = btn
            swatch_row.addWidget(btn)
        custom = QPushButton(tr("Scegli…"))
        custom.clicked.connect(self.choose_accent)
        swatch_row.addWidget(custom)
        swatch_row.addStretch(1)
        al.addLayout(swatch_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel(tr("Scala dell'interfaccia")))
        scale_row.addStretch(1)
        self.scale_label = QLabel()
        scale_row.addWidget(self.scale_label)
        al.addLayout(scale_row)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(SCALE_MIN, SCALE_MAX)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(10)
        self.scale_slider.setValue(int(mixer.settings.get("ui_scale", 100)))
        self.scale_slider.valueChanged.connect(self.on_scale)
        self._scale_timer = QTimer(self, singleShot=True, interval=150,
                                   timeout=lambda: mixer.change_appearance(
                                       scale=self.scale_slider.value()))
        al.addWidget(self.scale_slider)
        reset = QPushButton(tr("Ripristina aspetto"))
        reset.clicked.connect(self.reset_look)
        al.addWidget(reset, 0, Qt.AlignLeft)
        self.refresh_look()

        tabs = QTabWidget()
        self.tabs = tabs
        tabs.addTab(general, tr("Generale"))
        tabs.addTab(look, tr("Aspetto"))
        tabs.addTab(self._ducking_tab(mixer), tr("Ducking"))
        tabs.addTab(self._limits_tab(mixer), tr("Limiti"))
        tabs.addTab(self._automation_tab(mixer), tr("Automazione"))
        tabs.setCurrentIndex(tab)
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addLayout(buttons)

    def _ducking_tab(self, mixer):
        self.mixer = mixer
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self.duck_on = QCheckBox(tr("Attiva il ducking"))
        self.duck_on.setChecked(bool(mixer.ducking.get("enabled", True)))
        self.duck_on.toggled.connect(mixer.guard.set_ducking_enabled)
        lay.addWidget(self.duck_on)
        hint = QLabel(tr("Quando un trigger di un gruppo supera la soglia, le sue app da "
                         "abbassare scendono di volume; quando smette tornano al valore che "
                         "avevi scelto. Puoi creare pi\u00f9 gruppi indipendenti."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['hint']};")
        lay.addWidget(hint)

        self.duck_rows_layout = QVBoxLayout()
        self.duck_rows_layout.setAlignment(Qt.AlignTop)
        self.duck_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.duck_rows_layout.setSpacing(0)
        inner = QWidget()
        inner.setLayout(self.duck_rows_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(sc(160))
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        add_btn = QPushButton(tr("Aggiungi gruppo di ducking\u2026"))
        add_btn.clicked.connect(lambda: self.edit_duck_group(None))
        lay.addWidget(add_btn, 0, Qt.AlignLeft)

        self._rebuild_duck_groups()
        return page

    def _rebuild_duck_groups(self):
        while self.duck_rows_layout.count():
            item = self.duck_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        groups = self.mixer.ducking.get("groups") or []
        if not groups:
            hint = QLabel(tr("Nessun gruppo di ducking creato.\n"
                             "Usa \u00abAggiungi gruppo di ducking\u2026\u00bb per crearne uno."))
            hint.setStyleSheet(f"color: {PALETTE['hint']}; padding: 14px;")
            self.duck_rows_layout.addWidget(hint)
            return
        for g in groups:
            self.duck_rows_layout.addWidget(self._make_duck_row(g))

    def _duck_summary(self, g):
        m = self.mixer
        triggers = ", ".join(m.display_name(n) for n in g["triggers"]) or tr("(nessuna fonte)")
        targets = ", ".join(m.display_name(n) for n in g["targets"]) or tr("(nessuna fonte)")
        amount = f"{g['amount']}%" if g["unit"] == "%" else f"{g['amount']} dB"
        return tr("{triggers} \u2192 {targets} (\u2212{amount}, soglia {threshold} dB)",
                  triggers=triggers, targets=targets, amount=amount, threshold=g["threshold"])

    def _make_duck_row(self, g):
        row = QFrame()
        row.setObjectName("row")
        cb = QCheckBox()
        cb.setChecked(bool(g.get("enabled", True)))
        cb.toggled.connect(lambda v, gid=g["id"]: self.mixer.guard.set_group_enabled(gid, v))
        text = self._duck_summary(g)
        label = ElidedLabel(text)
        label.setToolTip(text)
        more = QPushButton("\u22ef")
        more.setToolTip(tr("Altre azioni"))
        more.clicked.connect(lambda _=False, grp=g, b=more: self._duck_menu(grp, b))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(cb)
        lay.addWidget(label, 1)
        lay.addWidget(more)
        return row

    def _duck_menu(self, g, btn):
        menu = QMenu(self)
        edit = menu.addAction(tr("Modifica\u2026"))
        menu.addSeparator()
        delete = menu.addAction(tr("Elimina"))
        chosen = menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        if chosen == edit:
            self.edit_duck_group(g)
        elif chosen == delete:
            self.delete_duck_group(g["id"])

    def edit_duck_group(self, group):
        dlg = DuckingGroupDialog(self.mixer, group)
        if dlg.exec() == QDialog.Accepted:
            self.mixer.guard.save_group(dlg.result_group())
            self._rebuild_duck_groups()

    def delete_duck_group(self, gid):
        if not confirm(self, tr("Elimina gruppo di ducking"),
                       tr("Eliminare questo gruppo di ducking?"), tr("Elimina")):
            return
        self.mixer.guard.delete_group(gid)
        self._rebuild_duck_groups()


    def _limits_tab(self, mixer):
        page = QWidget()
        lay = QVBoxLayout(page)
        hint = QLabel(tr("Volume massimo per app. Lo slider non lo supera e, se l’app lo supera da sola, "
                         "viene riportata al limite. Vale anche con preset, ducking e solo. "
                         "Si imposta anche dal tasto destro sul canale."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['hint']};")
        lay.addWidget(hint)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setAlignment(Qt.AlignTop)
        il.setSpacing(6)
        apps = [n for n, i in mixer.sources.items() if not i.get("device")]
        for name in apps:
            current = limit_pct(mixer.sources[name])
            row = QHBoxLayout()
            cb = QCheckBox(mixer.display_name(name))
            cb.setChecked(current < 100)
            box = QSpinBox()
            box.setRange(1, 99)
            box.setSuffix(" %")
            box.setValue(current if current < 100 else 60)
            box.setEnabled(current < 100)

            def changed(_=None, n=name, c=cb, b=box):
                b.setEnabled(c.isChecked())
                mixer.set_limit(n, b.value() if c.isChecked() else None)

            cb.toggled.connect(changed)
            box.valueChanged.connect(changed)
            row.addWidget(cb, 1)
            row.addWidget(box)
            il.addLayout(row)
        if not apps:
            il.addWidget(QLabel(tr("(nessuna app conosciuta)")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(sc(200))
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        return page

    def _automation_tab(self, mixer):
        self.mixer = mixer
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)
        self.auto_on = QCheckBox(tr("Attiva i preset automatici"))
        self.auto_on.setChecked(bool(mixer.automations.get("enabled", True)))
        self.auto_on.toggled.connect(mixer.set_automation_enabled)
        lay.addWidget(self.auto_on)
        hint = QLabel(tr("Applica da solo un preset quando apri o chiudi un'app, quando cambia "
                         "il dispositivo di uscita predefinito, o a un orario preciso."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {PALETTE['hint']};")
        lay.addWidget(hint)

        self.rules_layout = QVBoxLayout()
        self.rules_layout.setAlignment(Qt.AlignTop)
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_layout.setSpacing(0)
        inner = QWidget()
        inner.setLayout(self.rules_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(sc(160))
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        add_btn = QPushButton(tr("Aggiungi regola…"))
        add_btn.clicked.connect(lambda: self.edit_rule(None))
        lay.addWidget(add_btn, 0, Qt.AlignLeft)

        self._rebuild_rules()
        return page

    def _rebuild_rules(self):
        while self.rules_layout.count():
            item = self.rules_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        rules = self.mixer.automations.get("rules") or []
        if not rules:
            hint = QLabel(tr("Nessuna regola creata.\nUsa «Aggiungi regola…» per crearne una."))
            hint.setStyleSheet(f"color: {PALETTE['hint']}; padding: 14px;")
            self.rules_layout.addWidget(hint)
            return
        for rule in rules:
            self.rules_layout.addWidget(self._make_rule_row(rule))

    def _rule_summary(self, rule):
        m = self.mixer
        preset = rule.get("preset", "")
        t = rule["type"]
        if t == "app_open":
            return tr("Quando apri «{app}» → applica «{preset}»",
                      app=m.display_name(rule.get("app", "")), preset=preset)
        if t == "app_close":
            return tr("Quando chiudi «{app}» → applica «{preset}»",
                      app=m.display_name(rule.get("app", "")), preset=preset)
        if t == "output_device":
            return tr("Quando l'uscita predefinita diventa «{device}» → applica «{preset}»",
                      device=m.display_name(rule.get("device", "")), preset=preset)
        return tr("Alle {time} → applica «{preset}»", time=rule.get("time", ""), preset=preset)

    def _make_rule_row(self, rule):
        row = QFrame()
        row.setObjectName("row")
        cb = QCheckBox()
        cb.setChecked(bool(rule.get("enabled", True)))
        cb.toggled.connect(lambda v, rid=rule["id"]: self.mixer.set_rule_enabled(rid, v))
        text = self._rule_summary(rule)
        label = ElidedLabel(text)
        label.setToolTip(text)
        more = QPushButton("\u22ef")
        more.setToolTip(tr("Altre azioni"))
        more.clicked.connect(lambda _=False, r=rule, b=more: self._rule_menu(r, b))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(cb)
        lay.addWidget(label, 1)
        lay.addWidget(more)
        return row

    def _rule_menu(self, rule, btn):
        menu = QMenu(self)
        edit = menu.addAction(tr("Modifica…"))
        menu.addSeparator()
        delete = menu.addAction(tr("Elimina"))
        chosen = menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        if chosen == edit:
            self.edit_rule(rule)
        elif chosen == delete:
            self.delete_rule(rule["id"])

    def edit_rule(self, rule):
        dlg = AutomationRuleDialog(self.mixer, rule)
        if dlg.exec() == QDialog.Accepted:
            self.mixer.save_rule(dlg.result_rule())
            self._rebuild_rules()

    def delete_rule(self, rule_id):
        if not confirm(self, tr("Elimina regola"), tr("Eliminare questa regola?"), tr("Elimina")):
            return
        self.mixer.delete_rule(rule_id)
        self._rebuild_rules()

    def refresh_look(self):
        """Segna il colore d'accento attuale e il valore della scala."""
        current = str(self.mixer.settings.get("accent", "")).lower()
        for color, btn in self.swatches.items():
            ring = PALETTE["text"] if color == current else "transparent"
            css = (f"background: {color}; border: 2px solid {ring}; "
                   "border-radius: 4px; padding: 0px; min-width: 0px;")
            btn.setStyleSheet("QPushButton { " + css + " } QPushButton:hover { " + css + " }")
        self.scale_label.setText(f"{self.scale_slider.value()}%")

    def pick_accent(self, color):
        self.mixer.change_appearance(accent=color)
        self.refresh_look()

    def choose_accent(self):
        chosen = QColorDialog.getColor(QColor(self.mixer.settings.get("accent", DEFAULT_ACCENT)),
                                       self, tr("Colore d'accento"))
        if chosen.isValid():
            self.pick_accent(chosen.name())

    def on_scale(self, value):
        self.scale_label.setText(f"{value}%")
        self._scale_timer.start()

    def reset_look(self):
        self.theme_box.blockSignals(True)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData("dark")))
        self.theme_box.blockSignals(False)
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(100)
        self.scale_slider.blockSignals(False)
        self._scale_timer.stop()
        self.mixer.change_appearance(theme="dark", accent=DEFAULT_ACCENT, scale=100)
        self.refresh_look()

    def on_language(self, _index):
        """Cambia lingua subito; la finestra si chiude e si riapre (stessa scheda) nella nuova lingua."""
        self.mixer.reopen_settings_tab = self.tabs.currentIndex()
        self.mixer.change_language(self.lang_box.currentData())
        self.accept()

    def on_autostart(self, on):
        if not set_autostart(on):
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not on)
            self.autostart.blockSignals(False)
            QMessageBox.warning(self, tr("Avvio con Windows"),
                                tr("Non sono riuscito a modificare l'avvio automatico."))

    def on_quick_toggle(self, name, on):
        devices = [d for d in self.mixer.quick.get("devices", []) if isinstance(d, str)]
        if on and name not in devices:
            devices.append(name)
        elif not on and name in devices:
            devices.remove(name)
        self.mixer.quick["devices"] = devices
        save_quick_devices(self.mixer.quick)
        self.mixer.rebuild_tray_menu()

    def _update_quick_key_btn(self):
        key = self.mixer.quick.get("hotkey")
        self.quick_key_btn.setText(f"\u2328 {key.upper()}" if key else "\u2328 " + tr("Assegna"))

    def on_quick_key(self):
        if self.mixer.start_quick_bind():
            self.quick_key_btn.setText(tr("Premi un tasto\u2026"))


class AutomationRuleDialog(QDialog):
    """Crea o modifica una regola di preset automatico."""

    def __init__(self, mixer, rule=None):
        super().__init__(mixer)
        self.mixer = mixer
        self.rule = rule
        self.setWindowTitle(tr("Modifica regola") if rule else tr("Nuova regola"))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(sc(360))

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        lay.addWidget(QLabel(tr("Quando")))
        self.type_box = QComboBox()
        self.type_box.addItem(tr("Si apre un'app"), "app_open")
        self.type_box.addItem(tr("Si chiude un'app"), "app_close")
        self.type_box.addItem(tr("Cambia il dispositivo di uscita predefinito"), "output_device")
        self.type_box.addItem(tr("È una certa ora"), "time")
        lay.addWidget(self.type_box)

        apps = [n for n, i in mixer.sources.items() if not i.get("device")]
        outs = [n for n, i in mixer.sources.items() if i.get("device") == "out"]

        self.app_label = QLabel(tr("Applicazione"))
        self.app_box = QComboBox()
        for n in apps:
            self.app_box.addItem(mixer.display_name(n), n)
        if not apps:
            self.app_box.addItem(tr("(nessuna app conosciuta)"), None)
            self.app_box.setEnabled(False)

        self.device_label = QLabel(tr("Dispositivo di uscita"))
        self.device_box = QComboBox()
        for n in outs:
            self.device_box.addItem(mixer.display_name(n), n)
        if not outs:
            self.device_box.addItem(tr("(nessuna uscita conosciuta)"), None)
            self.device_box.setEnabled(False)

        self.time_label = QLabel(tr("Orario"))
        self.time_box = QTimeEdit()
        self.time_box.setDisplayFormat("HH:mm")
        self.time_box.setTime(QTime(21, 0))

        for w in (self.app_label, self.app_box, self.device_label, self.device_box,
                  self.time_label, self.time_box):
            lay.addWidget(w)

        lay.addWidget(QLabel(tr("Preset da applicare")))
        self.preset_box = QComboBox()
        for name in mixer.presets:
            self.preset_box.addItem(name, name)
        if not mixer.presets:
            no_presets = QLabel(tr("Crea prima almeno un preset dalla finestra Preset."))
            no_presets.setWordWrap(True)
            no_presets.setStyleSheet(f"color: {PALETTE['hint']};")
            lay.addWidget(no_presets)
        lay.addWidget(self.preset_box)

        self.ok_btn = QPushButton(tr("Ok"))
        self.ok_btn.clicked.connect(self.accept)
        cancel = QPushButton(tr("Annulla"))
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_btn)
        lay.addLayout(buttons)

        self.type_box.currentIndexChanged.connect(self._update_fields)
        self.app_box.currentIndexChanged.connect(self._update_ok)
        self.device_box.currentIndexChanged.connect(self._update_ok)
        self.preset_box.currentIndexChanged.connect(self._update_ok)

        if rule:
            self.type_box.setCurrentIndex(max(0, self.type_box.findData(rule["type"])))
            if rule["type"] in ("app_open", "app_close"):
                self.app_box.setCurrentIndex(max(0, self.app_box.findData(rule.get("app"))))
            elif rule["type"] == "output_device":
                self.device_box.setCurrentIndex(max(0, self.device_box.findData(rule.get("device"))))
            elif rule["type"] == "time":
                h, m = (rule.get("time") or "21:00").split(":")
                self.time_box.setTime(QTime(int(h), int(m)))
            self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(rule.get("preset"))))

        self._update_fields()

    def _update_fields(self):
        t = self.type_box.currentData()
        self.app_label.setVisible(t in ("app_open", "app_close"))
        self.app_box.setVisible(t in ("app_open", "app_close"))
        self.device_label.setVisible(t == "output_device")
        self.device_box.setVisible(t == "output_device")
        self.time_label.setVisible(t == "time")
        self.time_box.setVisible(t == "time")
        self._update_ok()

    def _update_ok(self):
        t = self.type_box.currentData()
        ok = self.preset_box.count() > 0 and self.preset_box.currentData() is not None
        if t in ("app_open", "app_close"):
            ok = ok and self.app_box.currentData() is not None
        elif t == "output_device":
            ok = ok and self.device_box.currentData() is not None
        self.ok_btn.setEnabled(ok)

    def result_rule(self):
        """Il dizionario della regola da salvare. Da chiamare solo dopo un exec() accettato."""
        t = self.type_box.currentData()
        rule = {"id": self.rule["id"] if self.rule else new_rule_id(),
                "enabled": self.rule.get("enabled", True) if self.rule else True,
                "type": t, "preset": self.preset_box.currentData()}
        if t in ("app_open", "app_close"):
            rule["app"] = self.app_box.currentData()
        elif t == "output_device":
            rule["device"] = self.device_box.currentData()
        else:
            rule["time"] = self.time_box.time().toString("HH:mm")
        return rule


class DuckingGroupDialog(QDialog):
    """Crea o modifica un gruppo di ducking: trigger, app da abbassare, soglia, riduzione, tempi."""

    def __init__(self, mixer, group=None):
        super().__init__(mixer)
        self.mixer = mixer
        self.group = group
        self.setWindowTitle(tr("Modifica gruppo di ducking") if group
                            else tr("Nuovo gruppo di ducking"))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(sc(440))

        g = group or {"enabled": True, "triggers": [], "targets": [], "threshold": -40,
                      "amount": 70, "unit": "%", "attack": 80, "release": 800, "hold": 500}

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self.on_cb = QCheckBox(tr("Attiva questo gruppo"))
        self.on_cb.setChecked(bool(g.get("enabled", True)))
        lay.addWidget(self.on_cb)

        apps = [n for n, i in mixer.sources.items() if not i.get("device")]
        mics = [n for n, i in mixer.sources.items() if i.get("device") == "in"]
        self.trig_boxes, self.targ_boxes = {}, {}
        lists = QHBoxLayout()
        lists.addLayout(self._check_list(tr("Trigger (app o microfono)"), apps + mics,
                                         self.trig_boxes, set(g.get("triggers", []))))
        lists.addLayout(self._check_list(tr("App da abbassare"), apps,
                                         self.targ_boxes, set(g.get("targets", []))))
        lay.addLayout(lists, 1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)

        def spin(lo, hi, suffix, value):
            box = QSpinBox()
            box.setRange(lo, hi)
            box.setSuffix(suffix)
            box.setValue(int(value))
            return box

        grid.addWidget(QLabel(tr("Soglia di attivazione")), 0, 0)
        self.thr_box = spin(-60, 0, " dB", g["threshold"])
        grid.addWidget(self.thr_box, 0, 1)
        grid.addWidget(QLabel(tr("Riduzione")), 1, 0)
        red = QHBoxLayout()
        self.amount_box = QSpinBox()
        self.unit_box = QComboBox()
        self.unit_box.addItem("%", "%")
        self.unit_box.addItem("dB", "dB")
        self._set_amount_range(g["unit"])
        self.amount_box.setValue(int(g["amount"]))
        self.unit_box.setCurrentIndex(max(0, self.unit_box.findData(g["unit"])))
        self.unit_box.currentIndexChanged.connect(self._on_unit)
        red.addWidget(self.amount_box)
        red.addWidget(self.unit_box)
        grid.addLayout(red, 1, 1)
        grid.addWidget(QLabel(tr("Attacco")), 2, 0)
        self.attack_box = spin(5, 2000, " ms", g["attack"])
        grid.addWidget(self.attack_box, 2, 1)
        grid.addWidget(QLabel(tr("Rilascio")), 3, 0)
        self.release_box = spin(20, 5000, " ms", g["release"])
        grid.addWidget(self.release_box, 3, 1)
        grid.addWidget(QLabel(tr("Mantenimento (hold)")), 4, 0)
        self.hold_box = spin(0, 5000, " ms", g["hold"])
        grid.addWidget(self.hold_box, 4, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)

        self.level_label = QLabel()
        lay.addWidget(self.level_label)

        self.ok_btn = QPushButton(tr("Ok"))
        self.ok_btn.clicked.connect(self.accept)
        cancel = QPushButton(tr("Annulla"))
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok_btn)
        lay.addLayout(buttons)

        for cb in list(self.trig_boxes.values()) + list(self.targ_boxes.values()):
            cb.toggled.connect(self._exclusive)
        self._exclusive()

        self._timer = QTimer(self, interval=100, timeout=self._update_level)
        self._timer.start()
        self._update_level()

    def _check_list(self, title, names, boxes, chosen):
        col = QVBoxLayout()
        col.addWidget(QLabel(title))
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setAlignment(Qt.AlignTop)
        il.setSpacing(4)
        for name in names:
            cb = QCheckBox(self.mixer.display_name(name))
            cb.setChecked(name in chosen)
            boxes[name] = cb
            il.addWidget(cb)
        if not names:
            il.addWidget(QLabel(tr("(nessuna fonte)")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(sc(110))
        scroll.setWidget(inner)
        col.addWidget(scroll)
        return col

    def _exclusive(self):
        """Una fonte non puo' essere insieme trigger e da abbassare, in questo gruppo."""
        trig = {n for n, cb in self.trig_boxes.items() if cb.isChecked()}
        targ = {n for n, cb in self.targ_boxes.items() if cb.isChecked()}
        for name, cb in self.targ_boxes.items():
            cb.setEnabled(name not in trig)
        for name, cb in self.trig_boxes.items():
            cb.setEnabled(name not in targ)

    def _set_amount_range(self, unit):
        lo, hi = (5, 95) if unit == "%" else (1, 40)
        self.amount_box.blockSignals(True)
        self.amount_box.setRange(lo, hi)
        self.amount_box.blockSignals(False)

    def _on_unit(self, _index):
        """Cambiando % <-> dB il valore si converte, cosi' la riduzione resta la stessa."""
        unit = self.unit_box.currentData()
        old = "%" if self.amount_box.maximum() == 95 else "dB"
        if unit == old:
            return
        amount = self.amount_box.value()
        if old == "%":
            new = round(-20 * math.log10(max(1e-3, 1 - amount / 100)))
        else:
            new = round((1 - 10 ** (-amount / 20)) * 100)
        self._set_amount_range(unit)
        new = max(self.amount_box.minimum(), min(self.amount_box.maximum(), new))
        self.amount_box.setValue(new)

    def _update_level(self):
        if not self.on_cb.isChecked():
            self.level_label.setText(tr("Ducking spento"))
            return
        trig = [n for n, cb in self.trig_boxes.items() if cb.isChecked()]
        targ = [n for n, cb in self.targ_boxes.items() if cb.isChecked()]
        if not (trig and targ):
            self.level_label.setText(tr("Scegli almeno un trigger e un\u2019app da abbassare"))
            return
        peak = self.mixer.guard.trigger_peak_db(trig)
        gid = self.group["id"] if self.group else None
        active = self.mixer.guard.group_state.get(gid, {}).get("active", False) if gid else False
        state = tr("ATTIVO") if active else tr("in attesa")
        self.level_label.setText(tr("Livello dei trigger: {db} dB  \u2022  ducking {state}",
                                    db=f"{peak:.0f}", state=state))

    def done(self, result):
        self._timer.stop()
        super().done(result)

    def result_group(self):
        """Il dizionario del gruppo da salvare. Da chiamare solo dopo un exec() accettato."""
        g = {"id": self.group["id"] if self.group else new_duck_id(),
             "enabled": self.on_cb.isChecked(),
             "triggers": [n for n, cb in self.trig_boxes.items() if cb.isChecked()],
             "targets": [n for n, cb in self.targ_boxes.items() if cb.isChecked()],
             "threshold": self.thr_box.value(), "amount": self.amount_box.value(),
             "unit": self.unit_box.currentData(), "attack": self.attack_box.value(),
             "release": self.release_box.value(), "hold": self.hold_box.value()}
        return clean_duck_group(g)


class ReorderArea(QWidget):
    """Contenitore i cui elementi si riordinano trascinandoli con il mouse.

    Si afferra un elemento nella zona indicata dal suo metodo grip_rect() e lo si trascina: una
    linea mostra dove verra' rilasciato. Gli elementi sono i widget del layout che sono istanze
    di item_type e hanno un attributo `name`. Slider e pulsanti non interferiscono: accettano
    loro il clic, quindi l'evento non arriva fin qui. Anche gli elementi disabilitati (app
    chiuse) si trascinano: Qt passa al contenitore i clic che il widget disabilitato rifiuta."""

    LINE = 3

    def __init__(self, horizontal, scroll, item_type, mime, on_move):
        super().__init__()
        self.horizontal = horizontal
        self.scroll = scroll
        self.item_type = item_type
        self.mime = mime
        self.on_move = on_move
        self._press = None
        self.setAcceptDrops(True)
        self._line = QFrame(self)
        self._line.setObjectName("dropLine")
        self._line.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._line.hide()

    def _items(self):
        lay = self.layout()
        found = []
        for i in range(lay.count() if lay is not None else 0):
            w = lay.itemAt(i).widget()
            if isinstance(w, self.item_type):
                found.append(w)
        return found

    def _insert_index(self, pos, items):
        """Posizione (0..n) in cui finirebbe un elemento rilasciato nel punto pos."""
        p = pos.x() if self.horizontal else pos.y()
        idx = 0
        for w in items:
            mid = (w.x() + w.width() / 2) if self.horizontal else (w.y() + w.height() / 2)
            if p > mid:
                idx += 1
        return idx

    def _show_line(self, idx, items):
        if not items:
            self._line.hide()
            return
        lo = (lambda w: w.x()) if self.horizontal else (lambda w: w.y())
        hi = (lambda w: w.x() + w.width()) if self.horizontal else (lambda w: w.y() + w.height())
        if idx >= len(items):
            at = hi(items[-1]) + 2
        elif idx == 0:
            at = lo(items[0]) - 2
        else:
            at = (lo(items[idx]) + hi(items[idx - 1])) / 2
        at = max(0, int(at) - 1)
        if self.horizontal:
            self._line.setGeometry(at, 0, self.LINE, self.height())
        else:
            self._line.setGeometry(0, at, self.width(), self.LINE)
        self._line.raise_()
        self._line.show()

    def mousePressEvent(self, e):
        self._press = None
        if e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            for w in self._items():
                if w.geometry().contains(pos) and w.grip_rect().contains(pos - w.pos()):
                    self._press = (w, pos)
                    e.accept()
                    return
        e.ignore()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        widget, start = self._press
        if (e.position().toPoint() - start).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        self._start_drag(widget, start)

    def mouseReleaseEvent(self, e):
        self._press = None

    def _start_drag(self, widget, start):
        drag = QDrag(self)
        data = QMimeData()
        data.setData(self.mime, QByteArray(widget.name.encode("utf-8")))
        drag.setMimeData(data)
        grip = widget.grip_rect()
        shot = widget.grab(grip)
        ghost = QPixmap(shot.size())
        ghost.setDevicePixelRatio(shot.devicePixelRatio())
        ghost.fill(Qt.transparent)
        p = QPainter(ghost)
        p.setOpacity(0.55)
        p.drawPixmap(0, 0, shot)
        p.end()
        drag.setPixmap(ghost)
        drag.setHotSpot(start - widget.pos() - grip.topLeft())
        timer = QTimer(self, interval=30, timeout=self._autoscroll)
        timer.start()
        try:
            drag.exec(Qt.MoveAction)
        finally:
            timer.stop()
            timer.deleteLater()
            self._line.hide()

    def _autoscroll(self):
        """Durante il trascinamento scorre l'area quando il mouse e' vicino al bordo."""
        vp = self.scroll.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        if not vp.rect().contains(pos):
            return
        bar = self.scroll.horizontalScrollBar() if self.horizontal else self.scroll.verticalScrollBar()
        p, size = (pos.x(), vp.width()) if self.horizontal else (pos.y(), vp.height())
        if p < 36:
            bar.setValue(bar.value() - 14)
        elif p > size - 36:
            bar.setValue(bar.value() + 14)
        else:
            return
        items = self._items()
        self._show_line(self._insert_index(self.mapFromGlobal(QCursor.pos()), items), items)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self.mime):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(self.mime):
            items = self._items()
            self._show_line(self._insert_index(e.position().toPoint(), items), items)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._line.hide()

    def dropEvent(self, e):
        self._line.hide()
        if not e.mimeData().hasFormat(self.mime):
            return
        name = e.mimeData().data(self.mime).data().decode("utf-8")
        items = self._items()
        idx = self._insert_index(e.position().toPoint(), items)
        before = items[idx].name if idx < len(items) else None
        e.acceptProposedAction()
        if before != name:
            self.on_move(name, before)


class PresetsDialog(QDialog):
    """Gestione dei preset: applica, tasto globale, aggiorna, rinomina, elimina."""

    def __init__(self, mixer):
        super().__init__(mixer)
        self.mixer = mixer
        self.setWindowTitle(tr("Preset"))
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setAlignment(Qt.AlignTop)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        inner = QWidget()
        inner.setLayout(self.rows_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(220)
        scroll.setWidget(inner)

        new_btn = QPushButton(tr("Salva stato attuale…"))
        new_btn.clicked.connect(mixer.save_preset_dialog)
        self.off_btn = QPushButton(tr("Disattiva preset (torna a prima)"))
        self.off_btn.clicked.connect(mixer.deactivate_preset)
        close = QPushButton(tr("Chiudi"))
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(new_btn)
        buttons.addWidget(self.off_btn)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(scroll)
        lay.addLayout(buttons)
        self.rebuild()

    def rebuild(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        presets = self.mixer.presets
        self.off_btn.setEnabled(bool(self.mixer.active_preset))
        if not presets:
            hint = QLabel(tr("Nessun preset salvato.\nUsa «Salva stato attuale…» per crearne uno."))
            hint.setStyleSheet(f"color: {PALETTE['hint']}; padding: 14px;")
            self.rows_layout.addWidget(hint)
            return
        for name, data in presets.items():
            self.rows_layout.addWidget(self._make_row(name, data))

    def _make_row(self, name, data):
        row = QFrame()
        row.setObjectName("row")
        active = name == self.mixer.active_preset
        label = ElidedLabel(("\u2713 " + name) if active else name)
        label.setToolTip(name + (tr(" (preset attivo)") if active else ""))
        key = data.get("hotkey")
        apply_btn = QPushButton(tr("Applica"))
        apply_btn.clicked.connect(lambda _=False, n=name: self.mixer.apply_preset(n))
        key_btn = QPushButton(f"\u2328 {key.upper()}" if key else "\u2328 " + tr("Assegna"))
        key_btn.setObjectName("key")
        key_btn.setToolTip(tr("Clic per assegnare un tasto globale (Esc = rimuovi)"))
        key_btn.clicked.connect(lambda _=False, n=name, b=key_btn: self.on_key(n, b))
        more = QPushButton("\u22ef")
        more.setToolTip(tr("Altre azioni"))
        more.clicked.connect(lambda _=False, n=name, b=more: self.show_more(n, b))
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(label, 1)
        lay.addWidget(apply_btn)
        lay.addWidget(key_btn)
        lay.addWidget(more)
        return row

    def on_key(self, name, btn):
        if self.mixer.start_preset_bind(name):
            btn.setText(tr("Premi un tasto…"))

    def show_more(self, name, btn):
        menu = QMenu(self)
        update = menu.addAction(tr("Aggiorna con lo stato attuale"))
        rename = menu.addAction(tr("Rinomina…"))
        menu.addSeparator()
        delete = menu.addAction(tr("Elimina"))
        chosen = menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        if chosen == update:
            self.mixer.update_preset(name)
        elif chosen == rename:
            self.mixer.rename_preset(name)
        elif chosen == delete:
            self.mixer.delete_preset(name)


DUCK_TOL = 0.006
DUCK_HYST_DB = 4.0


def duck_target_gain(amount, unit):
    """Guadagno finale (0..1) delle app abbassate: riduzione in % oppure in dB."""
    if unit == "dB":
        return 10 ** (-max(0.0, float(amount)) / 20)
    return max(0.0, min(1.0, 1 - float(amount) / 100))


class AudioGuard:
    """Ducking automatico (a piu' gruppi indipendenti) e limite massimo di volume.

    Ogni gruppo ha i suoi trigger, le sue app da abbassare, soglia, riduzione e tempi. Gruppi
    diversi possono avere la stessa app come bersaglio: in quel caso vale il guadagno piu' basso,
    cioe' la riduzione piu' forte tra quelle in corso in quel momento.

    Per ogni app gestita tiene, condivisi fra tutti i gruppi:
      base     il volume voluto dall'utente (senza ducking);
      applied  l'ultimo valore che il programma ha scritto o letto.
    Il volume effettivo e' min(base * guadagno, limite): non supera mai il limite. Se il volume
    letto da Windows e' diverso da `applied`, qualcuno l'ha cambiato (slider, preset, l'app
    stessa): quel valore diventa la nuova base. Se succede mentre un gruppo sta abbassando l'app,
    l'utente prende il controllo: su quell'app il ducking si ferma finche' dura quel giro, senza
    lotte con lo slider. A fine ducking ogni app torna alla sua base."""

    def __init__(self, host):
        self.host = host
        self.base = {}
        self.applied = {}
        self.override = set()
        self.active = False
        self.ducked = set()
        self.group_state = {}
        self._last = time.monotonic()
        self._n = 0
        self._was_busy = False
        self._meters = {}

    def _meter(self, session):
        key = session_key(session)
        if key not in self._meters:
            try:
                self._meters[key] = session._ctl.QueryInterface(IAudioMeterInformation)
            except Exception:
                self._meters[key] = None
        return self._meters[key]

    def trigger_peak_db(self, triggers):
        peak = 0.0
        for name in triggers:
            for s in self.host.groups.get(name, []):
                meter = self._meter(s)
                if meter is None:
                    continue
                try:
                    l, r = channel_peaks(meter)
                    peak = max(peak, l, r)
                except Exception:
                    pass
        return 20 * math.log10(peak) if peak > 1e-4 else -60.0

    def user_volume(self, name, current):
        """Volume voluto dall'utente: se il ducking sta abbassando l'app, quello di prima."""
        if name in self.ducked and name in self.base:
            return self.base[name]
        return current

    def _limit(self, name):
        return limit_pct(self.host.sources.get(name)) / 100

    def _write(self, sessions, value):
        for x in sessions:
            try:
                x.SimpleAudioVolume.SetMasterVolume(value, None)
            except Exception:
                pass

    def _save_recovery(self):
        save_duck_recovery({n: self.base[n] for n in self.ducked if n in self.base})

    def step(self):
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last))
        self._last = now
        self._n += 1
        sources = self.host.sources
        groups = self.host.groups
        cfg = self.host.ducking
        configs = ([g for g in cfg.get("groups") or [] if g.get("enabled")]
                  if cfg.get("enabled") else [])

        live_ids, effective, any_busy = set(), {}, False
        for g in configs:
            gid = g["id"]
            live_ids.add(gid)
            st = self.group_state.setdefault(gid, {"gain": 1.0, "active": False,
                                                    "hold_until": 0.0, "peak_db": -60.0})
            triggers = [n for n in g["triggers"] if n in sources]
            targets = [n for n in g["targets"]
                      if n in sources and n not in triggers and not n.startswith(DEV_PREFIX)]
            duck_on = bool(triggers and targets)
            tgain = duck_target_gain(g["amount"], g["unit"])

            if duck_on:
                st["peak_db"] = self.trigger_peak_db(triggers)
                opening = g["threshold"]
                if st["peak_db"] > opening or (st["active"] and st["peak_db"] > opening - DUCK_HYST_DB):
                    st["active"] = True
                    st["hold_until"] = now + g["hold"] / 1000
                elif st["active"] and now >= st["hold_until"]:
                    st["active"] = False
            else:
                st["active"] = False
                st["peak_db"] = -60.0

            want = tgain if st["active"] else 1.0
            if st["gain"] != want:
                span = max(1e-6, 1.0 - tgain)
                rate = g["attack"] if st["gain"] > want else g["release"]
                delta = span / max(0.005, rate / 1000) * dt
                st["gain"] = (max(want, st["gain"] - delta) if st["gain"] > want
                             else min(want, st["gain"] + delta))
            if st["gain"] >= 1.0 - 1e-3 and not st["active"]:
                st["gain"] = 1.0
            any_busy = any_busy or st["gain"] < 1.0 - 1e-3 or st["active"]
            # ogni bersaglio del gruppo entra in effective anche quando il gruppo non sta abbassando
            # (guadagno 1.0): e' quello che permette alla base di tornare al volume vero dell'app
            # non appena il gruppo smette, invece di restare bloccata al valore abbassato.
            for name in targets:
                effective[name] = min(effective.get(name, 1.0), st["gain"])

        self.group_state = {gid: st for gid, st in self.group_state.items() if gid in live_ids}
        self.active = any(st["active"] for st in self.group_state.values())
        self.override = {n for n in self.override if effective.get(n, 1.0) < 1.0 - 1e-3}

        settle = self._was_busy and not any_busy
        self._was_busy = any_busy
        if not (any_busy or settle) and self._n % 4:
            return

        managed = {n for n, i in sources.items() if not i.get("device") and limit_pct(i) < 100}
        if any_busy or settle:
            managed |= set(effective)
        for name in list(self.base):
            if not groups.get(name):
                if name in self.ducked and name not in self.override:
                    self.host.preset_pending.setdefault(name, {})["volume"] = round(self.base[name], 4)
                    self.host.persist_presets()
                self.base.pop(name, None)
                self.applied.pop(name, None)
                self.override.discard(name)
            elif name not in managed and name not in self.ducked:
                self.base.pop(name, None)
                self.applied.pop(name, None)
        new_ducked = set()
        for name in managed:
            sessions = groups.get(name)
            if not sessions:
                continue
            try:
                cur = float(sessions[0].SimpleAudioVolume.GetMasterVolume())
            except Exception:
                continue
            limit = self._limit(name)
            is_target = name in effective
            prev = self.applied.get(name)
            if prev is None or name not in self.base:
                self.base[name] = cur
            elif abs(cur - prev) > DUCK_TOL:
                if is_target and effective[name] < 1.0 - 1e-3:
                    self.override.add(name)
                self.base[name] = cur
            base = min(self.base[name], limit)
            self.base[name] = base
            gain = effective[name] if (is_target and name not in self.override) else 1.0
            if is_target and gain < 1.0 - 1e-3:
                new_ducked.add(name)
            desired = min(base * gain, limit)
            if abs(desired - cur) > 0.002:
                self._write(sessions, desired)
                self.applied[name] = desired
            else:
                self.applied[name] = cur
        if new_ducked != self.ducked:
            self.ducked = new_ducked
            self._save_recovery()
            self.host.on_ducked_changed()
        if self._n % 200 == 0:
            alive = {session_key(x) for sess in groups.values() for x in sess}
            self._meters = {k: m for k, m in self._meters.items() if k in alive}

    def release_all(self):
        """Alla chiusura rimette ogni app abbassata al volume voluto dall'utente."""
        for name in list(self.ducked):
            base = self.base.get(name)
            if base is not None:
                value = min(base, self._limit(name))
                self._write(self.host.groups.get(name, []), value)
                self.applied[name] = value
        self.ducked = set()
        self.override.clear()
        self.active = False
        for st in self.group_state.values():
            st["gain"] = 1.0
            st["active"] = False
        save_duck_recovery({})

    def set_ducking_enabled(self, on):
        self.host.ducking["enabled"] = bool(on)
        save_ducking(self.host.ducking)

    def save_group(self, group):
        groups = self.host.ducking.setdefault("groups", [])
        for i, g in enumerate(groups):
            if g["id"] == group["id"]:
                groups[i] = group
                break
        else:
            groups.append(group)
        save_ducking(self.host.ducking)

    def delete_group(self, gid):
        groups = self.host.ducking.get("groups") or []
        self.host.ducking["groups"] = [g for g in groups if g["id"] != gid]
        self.group_state.pop(gid, None)
        save_ducking(self.host.ducking)

    def set_group_enabled(self, gid, on):
        for g in self.host.ducking.get("groups") or []:
            if g["id"] == gid:
                g["enabled"] = bool(on)
                break
        save_ducking(self.host.ducking)


class Mixer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Audio Mixer v{APP_VERSION} (PID {os.getpid()})")
        self.resize(900, 520)
        self.setMinimumSize(520, 420)
        self._save_timer = QTimer(self, singleShot=True, interval=500,
                                  timeout=self.save_window)
        self._panel_open = True
        self._fonti_w = sc(FONTI_DEFAULT)
        self._sizes_ready = False
        self.router = OutputRouter()
        self.device_switcher = DefaultDeviceSwitcher()
        self.quick = load_quick_devices()
        self.output_applied = {}
        self._devices, self._dev_time = None, 0.0
        self._cap_devices, self._cap_time = None, 0.0
        self.dev_sessions = {}
        self.default_devices = set()
        self.default_out = None
        self.default_in = None
        self._enumerator = None
        self._tick_n = 0
        self.restore_window()
        self.strips = {}
        self.rows = {}
        self.groups = {}
        self.sources = load_sources()
        self.pending_reset = load_pending()
        if self.sources.pop(SYSTEM_NAME, None) is not None:
            save_sources(self.sources)
        self.pending_reset.discard(SYSTEM_NAME)
        self.settings = load_settings()
        configure_appearance(self.settings["theme"], self.settings["accent"],
                             self.settings["ui_scale"])
        global FADER
        self.fader = FADER = Fader(self, self.settings["fade_ms"])
        self.presets, self.preset_pending = load_presets()
        self.automations = load_automations()
        self._time_fired = {}
        self._automation_first_run = True
        self.last_audio = {}
        self.ducking = load_ducking()
        self.guard = AudioGuard(self)
        recovered = load_duck_recovery()
        if recovered:
            for _name, _vol in recovered.items():
                self.preset_pending.setdefault(_name, {})["volume"] = _vol
            self.persist_presets()
            save_duck_recovery({})
        self._presets_dlg = None
        self.active_preset = None
        self.preset_undo = None
        self.solo_set = set()
        self.solo_prev = load_solo_recovery()
        self._settings_dlg = None
        self.solo_applied = {}
        self._quitting = False
        self._closed = False
        self.config = load_config()
        self.recording = False
        self.bridge = Bridge()
        self.bridge.pressed.connect(self.toggle_mute)
        self.bridge.recorded.connect(self.apply_binding)
        self.bridge.call_main.connect(lambda fn: fn())
        self.bridge.preset_pressed.connect(self.apply_preset)
        self.bridge.preset_recorded.connect(self.apply_preset_binding)
        self.bridge.quick_pressed.connect(self.cycle_quick_device)
        self.bridge.quick_recorded.connect(self.apply_quick_binding)
        for name, key in self.config.items():
            self.register(name, key)
        used_keys = set(self.config.values())
        for pname, pdata in self.presets.items():
            key = pdata.get("hotkey")
            if not isinstance(key, str) or not key or key in used_keys:
                pdata.pop("hotkey", None)
                continue
            used_keys.add(key)
            self.register_preset(pname)
        if self.quick.get("hotkey") in used_keys:
            self.quick.pop("hotkey", None)
            save_quick_devices(self.quick)
        else:
            self.register_quick_key()

        self._dev_open = False
        self.reopen_settings_tab = None
        self.fonti_btn = QPushButton(tr("Fonti"))
        self.fonti_btn.setObjectName("panelTab")
        self.fonti_btn.setCheckable(True)
        self.fonti_btn.setToolTip(tr("Elenco delle applicazioni"))
        self.fonti_btn.clicked.connect(lambda: self.set_devices_open(False))
        self.dev_btn = QPushButton(tr("Dispositivi"))
        self.dev_btn.setObjectName("panelTab")
        self.dev_btn.setCheckable(True)
        self.dev_btn.setToolTip(tr("Elenco dei dispositivi (microfoni e casse) al posto delle app.\n"
                                   "I canali restano nel mixer, tra le app."))
        self.dev_btn.clicked.connect(lambda: self.set_devices_open(True))
        self.list_layout = QVBoxLayout()
        self.list_layout.setAlignment(Qt.AlignTop)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        list_inner = ReorderArea(False, list_scroll, SourceRow,
                                 "application/x-audiomixer-source",
                                 lambda n, b: self.move_source(n, b, same_kind=True))
        list_inner.setLayout(self.list_layout)
        self.list_inner = list_inner
        self.dev_layout = QVBoxLayout()
        self.dev_layout.setAlignment(Qt.AlignTop)
        self.dev_layout.setContentsMargins(0, 0, 0, 0)
        self.dev_layout.setSpacing(0)
        self.dev_inner = ReorderArea(False, list_scroll, SourceRow,
                                     "application/x-audiomixer-device",
                                     lambda n, b: self.move_source(n, b, same_kind=True))
        self.dev_inner.setLayout(self.dev_layout)
        sections = QWidget()
        sl = QVBoxLayout(sections)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addWidget(list_inner)
        sl.addWidget(self.dev_inner, 1)
        list_scroll.setWidget(sections)
        self.update_dev_section()
        self.fonti = QWidget()
        fl = QVBoxLayout(self.fonti)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        self.order_btn = QPushButton()
        self.order_btn.setObjectName("bar")
        self.order_btn.clicked.connect(self.show_order_menu)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 6, 0)
        head.setSpacing(0)
        head.addWidget(self.fonti_btn)
        head.addWidget(self.dev_btn)
        head.addStretch(1)
        head.addWidget(self.order_btn)
        fl.addLayout(head)
        fl.addWidget(list_scroll)
        self.update_order_button()

        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("toggleFonti")
        self.toggle_btn.setFixedWidth(sc(TOGGLE_W))
        self.toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.toggle_btn.setToolTip(tr("Mostra / nascondi l'elenco Fonti"))
        self.toggle_btn.clicked.connect(self.toggle_panel)

        self.hint = QLabel(tr("Apri l'elenco Fonti (freccia a sinistra) e clicca\n"
                              "l'occhio di una fonte per mostrarla qui."))
        self.hint.setStyleSheet(f"color: {PALETTE['hint']};")
        self.row = QHBoxLayout()
        self.row.setAlignment(Qt.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = ReorderArea(True, scroll, Strip,
                            "application/x-audiomixer-strip", self.move_source)
        inner.setLayout(self.row)
        scroll.setWidget(inner)

        self.left = QWidget()
        ll = QHBoxLayout(self.left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self.fonti, 1)
        ll.addWidget(self.toggle_btn)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self.on_splitter_moved)
        self.master = MasterStrip()
        self.system = SystemStrip()
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.splitter, 1)
        body.addWidget(self.system)
        body.addWidget(self.master)

        self.pin_btn = QPushButton("📌")
        self.pin_btn.setObjectName("bar")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip(tr("Sempre in primo piano"))
        self.pin_btn.setChecked(bool(self.settings["always_on_top"]))
        if self.pin_btn.isChecked():
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.pin_btn.toggled.connect(lambda v: self.set_option("always_on_top", v))
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("bar")
        self.settings_btn.setToolTip(tr("Impostazioni"))
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        self.preset_btn = QPushButton(tr("Preset") + " \u25be")
        self.preset_btn.setObjectName("bar")
        self.preset_btn.setToolTip(tr("Preset: salva e applica volumi, mute, uscite e fonti visibili"))
        self.preset_btn.clicked.connect(self.show_preset_menu)
        bar = QHBoxLayout()
        bar.addWidget(self.preset_btn)
        bar.addStretch(1)
        bar.addWidget(self.pin_btn)
        bar.addWidget(self.settings_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        root.addLayout(bar)
        root.addLayout(body, 1)
        self.apply_panel()

        self.tray = QSystemTrayIcon(QIcon(resource_path("icon.ico")), self)
        self.tray.setToolTip("Audio Mixer")
        self.tray_menu = QMenu(self)
        self.rebuild_tray_menu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.show()

        for name in self.sources:
            self.add_row(name)
        self.refresh()
        QTimer(self, interval=1000, timeout=self.refresh).start()
        QTimer(self, interval=50, timeout=self.tick).start()

    def display_name(self, name):
        """Nome mostrato: quello personalizzato, altrimenti quello del dispositivo / dell'app."""
        info = self.sources.get(name, {})
        return info.get("alias") or info.get("dev_name") or name

    def original_name(self, name):
        return self.sources.get(name, {}).get("dev_name") or name

    def _row_layout(self, name):
        return self.dev_layout if self.sources.get(name, {}).get("device") else self.list_layout

    def update_dev_section(self):
        """Due schede nel pannello: "Fonti" (le app) e "Dispositivi" (microfoni e casse). Quella
        scelta prende il posto dell'altra. La scheda Dispositivi compare solo se c'e' almeno un
        dispositivo. Cambia solo cosa si vede nel pannello: i canali nel mixer restano tra le app,
        nel loro ordine."""
        has = self.dev_layout.count() > 0
        show = has and self._dev_open
        self.dev_btn.setVisible(has)
        for btn, on in ((self.fonti_btn, not show), (self.dev_btn, show)):
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)
        self.list_inner.setVisible(not show)
        self.dev_inner.setVisible(show)

    def set_devices_open(self, flag):
        """Sceglie la scheda: True = Dispositivi, False = Fonti (app)."""
        if self._dev_open != bool(flag):
            self._dev_open = bool(flag)
            self._save_timer.start()
        self.update_dev_section()

    def add_row(self, name):
        info = self.sources[name]
        row = SourceRow(name, info.get("exe"), info.get("visible", False),
                        self.on_visible, self.rename_source, self.delete_source,
                        info.get("alias") or info.get("dev_name"), info.get("device"))
        self.rows[name] = row
        self._row_layout(name).addWidget(row)
        self.update_dev_section()

    def rename_source(self, name):
        info = self.sources[name]
        original = self.original_name(name)
        current = info.get("alias") or original
        text = ask_text(self, tr("Rinomina fonte"),
                        tr("Nuovo nome per «{name}»\n(lascia vuoto per il nome originale):",
                           name=original),
                        current)
        if text is None:
            return
        text = text.strip()
        if text and text != original:
            info["alias"] = text
        else:
            info.pop("alias", None)
        save_sources(self.sources)
        title = self.display_name(name)
        self.rows[name].set_title(title)
        if name in self.strips:
            self.strips[name].set_title(title)
        if self.settings.get("order_mode") == "az":
            self.relayout_order()

    def delete_source(self, name):
        """Toglie una fonte dall'elenco (solo se l'app non sta suonando)."""
        if self.groups.get(name):
            return
        is_device = bool(self.sources.get(name, {}).get("device"))
        if is_device:
            text = tr("Eliminare «{name}» dall'elenco?\n"
                      "Verranno tolti anche il nome personalizzato e il tasto rapido.\n"
                      "Se il dispositivo è collegato ricomparirà nell'elenco, nascosto.",
                      name=self.display_name(name))
        else:
            text = tr("Eliminare «{name}» dall'elenco?\n"
                      "Verranno tolti anche il nome personalizzato e il tasto rapido.\n"
                      "Alla prossima apertura dell'app, volume e mute torneranno ai valori "
                      "normali (100%, non mutata) e la fonte ricomparirà nell'elenco.",
                      name=name)
        if not confirm(self, tr("Elimina fonte"), text, tr("Elimina")):
            return
        if not is_device:
            self.pending_reset.add(name)
            save_pending(self.pending_reset)
        self.unregister(name)
        if self.config.pop(name, None) is not None:
            save_config(self.config)
        self.sources.pop(name, None)
        save_sources(self.sources)
        row = self.rows.pop(name, None)
        if row is not None:
            self.list_layout.removeWidget(row)
            self.dev_layout.removeWidget(row)
            row.deleteLater()
        self.update_dev_section()
        self.sync_strips()

    def on_visible(self, name, visible):
        self.sources[name]["visible"] = visible
        save_sources(self.sources)
        self.sync_strips()

    def refresh(self):
        prev_apps = {n for n in self.groups if not n.startswith(DEV_PREFIX)}
        prev_out = self.default_out
        groups = defaultdict(list)
        system = []
        for s in self.enum_sessions():
            if s.State == 2:
                continue
            name = app_name(s)
            if name == SYSTEM_NAME:
                system.append(s)
            else:
                groups[name].append(s)
        self.sync_devices(groups)
        self.groups = groups
        self.system.set_sessions(system)
        self.system.sync()
        for name, sessions in groups.items():
            try:
                vol = sessions[0].SimpleAudioVolume
                self.last_audio[name] = {
                    "volume": round(self.guard.user_volume(
                        name, self.fader.volume_state(sessions, vol.GetMasterVolume())), 4),
                    "mute": self.solo_prev.get(
                        name, self.fader.mute_state(sessions, bool(vol.GetMute())))}
            except Exception:
                pass
        self.fader.recover_sessions(groups, system)

        for name, sessions in groups.items():
            if name in self.pending_reset:
                reset_audio(sessions)
                self.run_router({x.ProcessId for x in sessions}, None, lambda err: None)
                self.pending_reset.discard(name)
                save_pending(self.pending_reset)

        changed = False
        added = False
        for name, sessions in groups.items():
            info = self.sources.get(name)
            if info is None:
                self.sources[name] = {"visible": False,
                                      "exe": app_exe(sessions)}
                self.add_row(name)
                changed = added = True
            elif not info.get("exe"):
                exe = app_exe(sessions)
                if exe:
                    info["exe"] = exe
                    changed = True
        if changed:
            save_sources(self.sources)
        if added and self.settings.get("order_mode") == "az":
            self.relayout_order()
        self.apply_pending_preset()
        for name, row in self.rows.items():
            row.set_active(name in groups)
            if row.kind:
                row.set_default(name in self.default_devices)
        for name in list(self.solo_set):
            if name not in groups:
                self.solo_set.discard(name)
                strip = self.strips.get(name)
                if strip is not None:
                    strip.solo.blockSignals(True)
                    strip.solo.setChecked(False)
                    strip.solo.blockSignals(False)
        if self.solo_set:
            self.apply_solo()
        else:
            self.restore_solo()
        self.sync_strips()
        self.refresh_master()
        self.apply_outputs()
        self._run_automations(prev_apps, prev_out)

    def capture_devices(self):
        """Dispositivi di ingresso (microfoni) attivi; l'elenco si aggiorna ogni 5 secondi."""
        now = time.monotonic()
        if self._cap_devices is None or now - self._cap_time > 5:
            try:
                self._cap_devices = AudioUtilities.GetAllDevices(
                    data_flow=EDataFlow.eCapture.value, device_state=DEVICE_STATE.ACTIVE.value)
            except Exception:
                self._cap_devices = self._cap_devices or []
            self._cap_time = now
        return self._cap_devices

    def default_device_id(self, flow):
        """Id del dispositivo predefinito di Windows ("out" = uscita, "in" = ingresso)."""
        try:
            if self._enumerator is None:
                self._enumerator = AudioUtilities.GetDeviceEnumerator()
            kind = EDataFlow.eRender.value if flow == "out" else EDataFlow.eCapture.value
            return self._enumerator.GetDefaultAudioEndpoint(kind, 1).GetId()
        except Exception:
            self._enumerator = None
            return None

    def sync_devices(self, groups):
        """Aggiunge ai gruppi i dispositivi collegati (come se fossero app che suonano) e
        registra tra le fonti quelli nuovi, nascosti. Un dispositivo scollegato resta
        nell'elenco: il suo canale si disabilita, come quello di un'app chiusa."""
        present = {}
        for flow, devices in (("out", self.render_devices()), ("in", self.capture_devices())):
            for d in devices:
                present[DEV_PREFIX + d.id] = (flow, d)
        for name in list(self.dev_sessions):
            if name not in present:
                del self.dev_sessions[name]
        out_id = self.default_device_id("out")
        in_id = self.default_device_id("in")
        self.default_out = DEV_PREFIX + out_id if out_id else None
        self.default_in = DEV_PREFIX + in_id if in_id else None
        self.default_devices = {d for d in (self.default_out, self.default_in) if d}
        changed = False
        for name, (flow, d) in present.items():
            title = d.FriendlyName or d.id
            info = self.sources.get(name)
            if info is None:
                self.sources[name] = {"visible": False, "device": flow, "dev_name": title}
                self.add_row(name)
                changed = True
            elif info.get("device") != flow or info.get("dev_name") != title:
                info["device"], info["dev_name"] = flow, title
                changed = True
                if not info.get("alias"):
                    self.rows[name].set_title(title)
                    if name in self.strips:
                        self.strips[name].set_title(title)
            sess = self.dev_sessions.get(name)
            if sess is None:
                sess = make_device_session(d)
                if sess is not None:
                    self.dev_sessions[name] = sess
            if sess is not None:
                groups[name] = [sess]
        if changed:
            save_sources(self.sources)

    def sync_strips(self):
        """Mostra nel mixer solo le fonti con l'occhio acceso."""
        wanted = [n for n, i in self.sources.items() if i.get("visible")]
        changed = False
        solo_changed = False
        for name in list(self.strips):
            if name not in wanted:
                self.strips.pop(name).deleteLater()
                changed = True
                if name in self.solo_set:
                    self.solo_set.discard(name)
                    solo_changed = True
        if solo_changed:
            if self.solo_set:
                self.apply_solo()
            else:
                self.restore_solo()
        for name in wanted:
            strip = self.strips.get(name)
            if strip is None:
                flow = self.sources[name].get("device")
                if flow:
                    strip = DeviceStrip(name, flow, self.config.get(name, ""), self.start_bind)
                else:
                    strip = Strip(
                        name, self.sources[name].get("exe"),
                        self.config.get(name, ""), self.start_bind, self.on_solo,
                        self.show_output_menu)
                self.strips[name] = strip
                strip.set_title(self.display_name(name))
                changed = True
            sessions = self.groups.get(name, [])
            strip.set_sessions(sessions)
            strip.setEnabled(bool(sessions))
            strip.sync()
            if strip.solo.isChecked() != (name in self.solo_set):
                strip.solo.blockSignals(True)
                strip.solo.setChecked(name in self.solo_set)
                strip.solo.blockSignals(False)
            if isinstance(strip, DeviceStrip):
                strip.set_default(name in self.default_devices)
            strip.set_output_hint(self.device_name(self.sources[name].get("output")))
            if not isinstance(strip, DeviceStrip):
                strip.set_limit(limit_pct(self.sources[name]))
                strip.set_ducked(name in self.guard.ducked)
        if changed:
            self.layout_strips()

    def fonti_min(self):
        """Larghezza minima del pannello Fonti: quella di base, ma mai meno di quanto serve alle
        schede e al pulsante d'ordine nella lingua attuale (le parole russe o tedesche sono piu' lunghe)."""
        head = self.fonti_btn.sizeHint().width() + self.order_btn.sizeHint().width() + sc(10)
        if not self.dev_btn.isHidden():
            head += self.dev_btn.sizeHint().width()
        return min(max(sc(FONTI_MIN), head), sc(FONTI_MAX))

    def apply_panel(self):
        self.fonti.setVisible(self._panel_open)
        self.toggle_btn.setText("\u25c0" if self._panel_open else "\u25b6")
        fmin, fmax, tw = self.fonti_min(), sc(FONTI_MAX), sc(TOGGLE_W)
        if self._panel_open:
            self.left.setMinimumWidth(fmin + tw)
            self.left.setMaximumWidth(fmax + tw)
            lw = max(fmin, min(fmax, self._fonti_w)) + tw
        else:
            self.left.setFixedWidth(tw)
            lw = tw
        total = max(self.splitter.width(), self.width() - 12)
        self.splitter.setSizes([lw, max(1, total - lw)])

    def on_splitter_moved(self, pos, index):
        if self._panel_open:
            self._fonti_w = max(self.fonti_min(), min(sc(FONTI_MAX), self.left.width() - sc(TOGGLE_W)))
            self._save_timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._sizes_ready:
            self._sizes_ready = True
            QTimer.singleShot(0, self.apply_panel)

    def toggle_panel(self):
        self._panel_open = not self._panel_open
        self.apply_panel()
        self.save_window()

    def save_window(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data = self.saveGeometry().toBase64().data().decode("ascii")
            with open(WINDOW_FILE, "w", encoding="utf-8") as f:
                json.dump({"geometry": data, "panel_open": self._panel_open,
                           "fonti_width": self._fonti_w, "devices_open": self._dev_open}, f)
        except Exception:
            pass

    def restore_window(self):
        try:
            with open(WINDOW_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            self._panel_open = bool(saved.get("panel_open", True))
            self._dev_open = bool(saved.get("devices_open", False))
            self.update_dev_section()
            self._fonti_w = max(self.fonti_min(), min(sc(FONTI_MAX),
                                                      int(saved.get("fonti_width", sc(FONTI_DEFAULT)))))
            self.restoreGeometry(QByteArray.fromBase64(saved["geometry"].encode("ascii")))
        except Exception:
            pass

    def moveEvent(self, e):
        super().moveEvent(e)
        self._save_timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._save_timer.start()

    def closeEvent(self, e):
        saving = False
        try:
            saving = QGuiApplication.isSavingSession()
        except Exception:
            pass
        if (self.settings.get("close_to_tray") and not self._quitting
                and not saving and self.tray.isVisible()):
            e.ignore()
            self.save_window()
            self.hide()
            return
        self.shutdown()
        super().closeEvent(e)

    def shutdown(self):
        """Chiusura vera: salva tutto e ripristina i mute del solo."""
        if self._closed:
            return
        self._closed = True
        self.save_window()
        self.guard.release_all()
        self.restore_solo()
        self.fader.finish_all()
        self.tray.hide()

    def quit_app(self):
        self._quitting = True
        self.shutdown()
        QApplication.quit()

    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.bring_to_front()

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visible()

    def open_settings(self, tab=0):
        self._settings_dlg = SettingsDialog(self, tab)
        self._settings_dlg.exec()
        self._settings_dlg = None
        if self.reopen_settings_tab is not None:
            tab = self.reopen_settings_tab
            self.reopen_settings_tab = None
            QTimer.singleShot(0, lambda: self.open_settings(tab))

    def change_language(self, setting):
        """Cambia lingua ("auto" o un codice di LANGUAGES), la salva e la applica subito, senza riavviare."""
        if setting != "auto" and setting not in LANGUAGES:
            setting = "auto"
        self.settings["language"] = setting
        save_settings(self.settings)
        set_language(resolve_language(setting))
        install_qt_translator(LANG)
        self.retranslate()

    def retranslate(self):
        """Rimette nella lingua attuale tutti i testi: quelli fissi della finestra, le due strip
        fisse, i menu, e ricostruisce canali ed elenco fonti."""
        self.fonti_btn.setText(tr("Fonti"))
        self.fonti_btn.setToolTip(tr("Elenco delle applicazioni"))
        self.dev_btn.setText(tr("Dispositivi"))
        self.dev_btn.setToolTip(tr("Elenco dei dispositivi (microfoni e casse) al posto delle app.\n"
                                   "I canali restano nel mixer, tra le app."))
        self.toggle_btn.setToolTip(tr("Mostra / nascondi l'elenco Fonti"))
        self.hint.setText(tr("Apri l'elenco Fonti (freccia a sinistra) e clicca\n"
                             "l'occhio di una fonte per mostrarla qui."))
        self.pin_btn.setToolTip(tr("Sempre in primo piano"))
        self.settings_btn.setToolTip(tr("Impostazioni"))
        self.preset_btn.setToolTip(tr("Preset: salva e applica volumi, mute, uscite e fonti visibili"))
        self.master.retranslate()
        self.system.retranslate()
        self.update_order_button()
        self.refresh_preset_ui()
        for name in list(self.strips):
            self.strips.pop(name).deleteLater()
        self.layout_strips()
        for lay in (self.list_layout, self.dev_layout):
            while lay.count():
                w = lay.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
        self.rows.clear()
        for name in self.ordered_names():
            self.add_row(name)
        self.update_dev_section()
        self.apply_panel()
        self.sync_strips()
        self.refresh()

    def set_option(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)
        if key == "fade_ms":
            self.fader.set_duration(value)
        if key == "always_on_top":
            self.pin_btn.blockSignals(True)
            self.pin_btn.setChecked(bool(value))
            self.pin_btn.blockSignals(False)
            self.set_always_on_top(bool(value))

    def set_limit(self, name, pct):
        """Limite massimo di volume di un'app (1-99 %; None = nessun limite). Salvato in sources.json."""
        info = self.sources.get(name)
        if info is None or info.get("device"):
            return
        if pct is None or pct >= 100:
            info.pop("max", None)
        else:
            info["max"] = max(1, int(pct))
        save_sources(self.sources)
        strip = self.strips.get(name)
        if strip is not None and not isinstance(strip, DeviceStrip):
            strip.set_limit(limit_pct(info))

    def on_ducked_changed(self):
        """Il ducking ha cominciato o smesso di abbassare qualche app: aggiorna i bordi."""
        for name, strip in self.strips.items():
            if not isinstance(strip, DeviceStrip):
                strip.set_ducked(name in self.guard.ducked)

    def change_appearance(self, theme=None, accent=None, scale=None):
        """Cambia tema, accento o scala, li salva e li applica subito (senza riavviare)."""
        if theme in THEMES:
            self.settings["theme"] = theme
        if valid_accent(accent):
            self.settings["accent"] = accent.lower()
        if scale is not None:
            self.settings["ui_scale"] = max(SCALE_MIN, min(SCALE_MAX, int(scale)))
        save_settings(self.settings)
        self.apply_appearance()

    def apply_appearance(self):
        old_scale = UI["scale"]
        configure_appearance(self.settings["theme"], self.settings["accent"],
                             self.settings["ui_scale"])
        self._fonti_w = round(self._fonti_w * UI["scale"] / old_scale)
        self.toggle_btn.setFixedWidth(sc(TOGGLE_W))
        QApplication.instance().setStyleSheet(build_style())
        for w in list(self.strips.values()) + [self.master, self.system]:
            w.apply_scale()
        for row in self.rows.values():
            row.apply_scale()
            row.refresh_style()
        self.hint.setStyleSheet(f"color: {PALETTE['hint']};")
        self.apply_panel()
        self._save_timer.start()
        self.update()

    def set_always_on_top(self, on):
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) == on:
            return
        was_visible = self.isVisible()
        geo = self.saveGeometry()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.restoreGeometry(geo)
        if was_visible:
            self.show()
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is not None:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()

    def render_devices(self, force=False):
        """Dispositivi di uscita attivi (l'elenco si aggiorna ogni 5 secondi)."""
        now = time.monotonic()
        if force or self._devices is None or now - self._dev_time > 5:
            try:
                self._devices = AudioUtilities.GetAllDevices(
                    data_flow=EDataFlow.eRender.value, device_state=DEVICE_STATE.ACTIVE.value)
            except Exception:
                self._devices = self._devices or []
            self._dev_time = now
        return self._devices

    def device_name(self, device_id):
        if not device_id:
            return ""
        for d in self.render_devices():
            if d.id == device_id:
                return d.FriendlyName or device_id
        return tr("dispositivo non collegato")

    def enum_sessions(self):
        """Sessioni audio di TUTTI i dispositivi di uscita: se un'app viene mandata a un'altra
        uscita, continua a comparire e a rispondere (GetAllSessions guarda solo il predefinito)."""
        if IAudioSessionControl2 is None:
            return AudioUtilities.GetAllSessions()
        sessions, ok = [], False
        for d in self.render_devices():
            try:
                enum = d.AudioSessionManager.GetSessionEnumerator()
                for i in range(enum.GetCount()):
                    ctl = enum.GetSession(i)
                    ctl2 = ctl.QueryInterface(IAudioSessionControl2) if ctl is not None else None
                    if ctl2 is not None:
                        sessions.append(AudioSession(ctl2))
                ok = True
            except Exception:
                continue
        return sessions if ok else AudioUtilities.GetAllSessions()

    def refresh_master(self):
        """Tiene la strip master sul dispositivo predefinito, anche quando cambia."""
        try:
            if self._enumerator is None:
                self._enumerator = AudioUtilities.GetDeviceEnumerator()
            dev_id = self._enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, 1).GetId()
        except Exception:
            dev_id, self._enumerator = None, None
        if dev_id != self.master.dev_id or (dev_id and self.master.endpoint is None):
            try:
                self.master.bind(AudioUtilities.GetSpeakers() if dev_id else None)
            except Exception:
                self.master.bind(None)
        self.master.sync()

    def apply_outputs(self):
        """Riapplica l'uscita scelta per ogni app, anche a quelle che si riaprono."""
        for name in list(self.output_applied):
            if name not in self.groups:
                self.output_applied.pop(name)
        for name, sessions in self.groups.items():
            device_id = self.sources.get(name, {}).get("output")
            if not device_id:
                continue
            pids = {x.ProcessId for x in sessions if x.ProcessId}
            done = self.output_applied.setdefault(name, set())
            new = pids - done
            if new:
                done |= new
                self.run_router(new, device_id, lambda err: None)

    def show_output_menu(self, name, pos):
        """Tasto destro sul canale: scelta del dispositivo di uscita per quell'app."""
        current = self.sources.get(name, {}).get("output")
        menu = QMenu(self)
        title = menu.addAction(tr("Uscita audio di questa app"))
        title.setEnabled(False)
        entries = [(menu.addAction(("\u25cf " if not current else "    ") + tr("Predefinito di Windows")), None)]
        devices = self.render_devices(force=True)
        for d in devices:
            mark = "\u25cf " if d.id == current else "    "
            entries.append((menu.addAction(mark + (d.FriendlyName or d.id)), d.id))
        if current and all(d.id != current for d in devices):
            gone = menu.addAction("\u25cf " + tr("(dispositivo scollegato)"))
            gone.setEnabled(False)
        limit_actions = {}
        custom = None
        if not self.sources.get(name, {}).get("device"):
            menu.addSeparator()
            sub = menu.addMenu(tr("Limite massimo di volume"))
            now_limit = limit_pct(self.sources.get(name))
            for pct in (None, 90, 80, 70, 60, 50, 40, 30):
                act = sub.addAction(tr("Nessun limite") if pct is None else f"{pct}%")
                act.setCheckable(True)
                act.setChecked((pct or 100) == now_limit)
                limit_actions[act] = pct
            custom = sub.addAction(tr("Altro…"))
        chosen = menu.exec(pos)
        if chosen is not None and chosen in limit_actions:
            self.set_limit(name, limit_actions[chosen])
            return
        if custom is not None and chosen == custom:
            value = ask_int(self, tr("Limite massimo di volume"),
                            tr("Volume massimo di «{name}» (%):", name=self.display_name(name)),
                            limit_pct(self.sources.get(name)) if limit_pct(self.sources.get(name)) < 100 else 60,
                            1, 99)
            if value is not None:
                self.set_limit(name, value)
            return
        for action, device_id in entries:
            if chosen == action:
                self.set_output(name, device_id)
                return

    def run_router(self, pids, device_id, on_done):
        """Cambia l'uscita in un thread (il processo di servizio puo' impiegare qualche secondo).
        on_done(errore) viene chiamata nel thread dell'interfaccia: errore e' None se tutto ok."""
        pids = {p for p in pids if p}

        def worker():
            err = None
            try:
                self.router.apply(pids, device_id)
            except Exception as e:
                err = str(e) or e.__class__.__name__
            self.bridge.call_main.emit(lambda: on_done(err))

        threading.Thread(target=worker, daemon=True).start()

    def set_output(self, name, device_id, silent=False):
        """silent=True: niente cursore d'attesa ne' messaggi d'errore (usato dai preset)."""
        sessions = self.groups.get(name, [])
        pids = {x.ProcessId for x in sessions if x.ProcessId}
        if not silent:
            QApplication.setOverrideCursor(Qt.WaitCursor)

        def finished(err):
            if not silent:
                QApplication.restoreOverrideCursor()
            if err:
                if silent:
                    return
                QMessageBox.warning(
                    self, tr("Uscita audio"),
                    tr("Non sono riuscito a cambiare l'uscita di «{name}».\n\n{err}\n\n"
                       "Questa funzione usa un'interfaccia non documentata di Windows, che potrebbe non "
                       "essere disponibile su questa versione. Il resto del mixer funziona normalmente.",
                       name=name, err=err))
                return
            info = self.sources.get(name)
            if info is None:
                return
            if device_id:
                info["output"] = device_id
            else:
                info.pop("output", None)
            save_sources(self.sources)
            self.output_applied[name] = set(pids) if device_id else set()
            if name in self.strips:
                self.strips[name].set_output_hint(self.device_name(device_id))
            self.refresh()

        self.run_router(pids, device_id, finished)

    @staticmethod
    def _set_mute(sessions, muted, name=None):
        fade_mute(sessions, muted, name)

    def on_solo(self, name, checked):
        if checked:
            self.solo_set.add(name)
            self.apply_solo()
        else:
            self.solo_set.discard(name)
            if self.solo_set:
                self.apply_solo()
            else:
                self.restore_solo()
        for strip in self.strips.values():
            strip.sync()

    def apply_solo(self):
        """Muta tutte le app tranne quelle in solo, ricordando il mute di prima.
        Vale anche per le app (e le nuove sessioni) che compaiono mentre il solo e' attivo."""
        if not self.solo_set:
            return
        for name, sessions in self.groups.items():
            if name.startswith(DEV_PREFIX):
                continue
            target = name not in self.solo_set
            new = [x for x in sessions if self.solo_applied.get(session_key(x)) != target]
            if not new:
                continue
            if name not in self.solo_prev:
                try:
                    self.solo_prev[name] = shown_mute(
                        sessions, bool(sessions[0].SimpleAudioVolume.GetMute()))
                except Exception:
                    continue
                save_solo_recovery(self.solo_prev)
            for x in new:
                self.solo_applied[session_key(x)] = target
            self._set_mute(new, target, name)

    def restore_solo(self):
        """Toglie il solo: ogni app torna al mute che aveva prima. Le app chiuse nel
        frattempo restano in attesa e vengono sistemate quando riappaiono."""
        changed = False
        for name in list(self.solo_prev):
            sessions = self.groups.get(name)
            if sessions:
                self._set_mute(sessions, self.solo_prev.pop(name), name)
                changed = True
        self.solo_applied.clear()
        if changed:
            save_solo_recovery(self.solo_prev)

    def register(self, name, key):
        if not key:
            return
        try:
            keyboard.add_hotkey(key, lambda n=name: self.bridge.pressed.emit(n))
        except Exception as e:
            print("Tasto non valido:", key, e)

    def unregister(self, name):
        key = self.config.get(name)
        if key:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass

    def toggle_mute(self, name):
        if name == SYSTEM_NAME:
            if self.system.sessions:
                self.system.mute.toggle()
            return
        strip = self.strips.get(name)
        if strip and strip.sessions:
            strip.mute.toggle()
            return
        sessions = self.groups.get(name, [])
        if not sessions:
            return
        try:
            new = not shown_mute(sessions, bool(sessions[0].SimpleAudioVolume.GetMute()))
        except Exception:
            return
        fade_mute(sessions, new, name)

    def start_bind(self, name):
        if self.recording:
            return
        self.recording = True
        self.strips[name].key_btn.setText(tr("Premi un tasto…"))

        def worker():
            try:
                key = keyboard.read_hotkey(suppress=False)
            except Exception:
                key = ""
            self.bridge.recorded.emit(name, key)

        threading.Thread(target=worker, daemon=True).start()

    def apply_binding(self, name, key):
        self.recording = False
        if key == "esc":
            key = ""
        clash = [p for p, d in self.presets.items() if key and d.get("hotkey") == key]
        quick_clash = bool(key) and self.quick.get("hotkey") == key
        if clash or quick_clash:
            used = ", ".join(quoted(p) for p in clash
                             + ([tr("cambio rapido di dispositivo")] if quick_clash else []))
            if not confirm(self, tr("Tasto già in uso"),
                           tr("Il tasto {key} è già assegnato a {used}.\n\n"
                              "Vuoi usarlo per il mute di «{name}» e toglierlo da lì?",
                              key=key.upper(), used=used, name=self.display_name(name)),
                           tr("Riassegna")):
                if name in self.strips:
                    self.strips[name].set_key(self.config.get(name, ""))
                return
            for p in clash:
                self.unregister_preset(p)
                self.presets[p].pop("hotkey", None)
            if clash:
                self.persist_presets()
                self.refresh_preset_ui()
            if quick_clash:
                self.unregister_quick_key()
                self.quick.pop("hotkey", None)
                save_quick_devices(self.quick)
                if self._settings_dlg is not None:
                    self._settings_dlg._update_quick_key_btn()
        for other, k in list(self.config.items()):
            if key and k == key and other != name:
                self.unregister(other)
                del self.config[other]
                if other in self.strips:
                    self.strips[other].set_key("")
        self.unregister(name)
        if key:
            self.config[name] = key
            self.register(name, key)
        else:
            self.config.pop(name, None)
        save_config(self.config)
        if name in self.strips:
            self.strips[name].set_key(key)

    def layout_strips(self):
        """Mette i canali nel layout secondo l'ordine delle fonti."""
        wanted = [n for n in self.ordered_names()
                  if self.sources[n].get("visible") and n in self.strips]
        while self.row.count():
            self.row.takeAt(0)
        self.row.addWidget(self.hint)
        for name in wanted:
            self.row.addWidget(self.strips[name])
        self.hint.setVisible(not wanted)

    def move_source(self, name, before, same_kind=False):
        """Riordino con il mouse: sposta `name` davanti a `before` (None = in fondo).
        L'ordine e' unico: vale per i canali e per l'elenco Fonti, ed e' quello del file
        sources.json (le nuove fonti si aggiungono in fondo).

        same_kind=True e' il riordino dentro un elenco che mostra solo app oppure solo dispositivi:
        le altre fonti non si vedono, quindi non si devono spostare. L'elemento cambia posto solo
        tra quelli del suo tipo: ogni tipo tiene le sue posizioni nell'ordine generale, e i
        dispositivi restano dove sono in mezzo alle app (trascinare un dispositivo "in fondo"
        all'elenco non lo manda in fondo a tutti i canali)."""
        if name not in self.sources or name == before:
            return
        if self.settings.get("order_mode") == "az":
            frozen = [(n, self.sources[n]) for n in self.ordered_names()]
            self.sources.clear()
            self.sources.update(frozen)
            self.set_order_mode("custom", relayout=False)
        if same_kind:
            names = list(self.sources)
            is_dev = bool(self.sources[name].get("device"))
            slots = [i for i, n in enumerate(names) if bool(self.sources[n].get("device")) == is_dev]
            kind = [names[i] for i in slots if names[i] != name]
            kind.insert(kind.index(before) if before in kind else len(kind), name)
            for i, n in zip(slots, kind):
                names[i] = n
        else:
            names = [n for n in self.sources if n != name]
            names.insert(names.index(before) if before in names else len(names), name)
        if names == list(self.sources):
            return
        items = [(n, self.sources[n]) for n in names]
        self.sources.clear()
        self.sources.update(items)
        save_sources(self.sources)
        self.relayout_order()

    def ordered_names(self):
        """Nomi delle fonti nell'ordine in cui si vedono: personale (quello di sources.json)
        oppure alfabetico A-Z (sul nome mostrato, quindi anche sul nome personalizzato)."""
        names = list(self.sources)
        if self.settings.get("order_mode") == "az":
            names.sort(key=lambda n: self.display_name(n).casefold())
        return names

    def relayout_order(self):
        """Rimette canali ed elenco Fonti nell'ordine attuale."""
        self.layout_strips()
        for n in self.ordered_names():
            row = self.rows.get(n)
            if row is not None:
                lay = self._row_layout(n)
                lay.removeWidget(row)
                lay.addWidget(row)
        self.update_order_button()

    def update_order_button(self):
        az = self.settings.get("order_mode") == "az"
        self.order_btn.setText("\u2195 A-Z" if az else "\u2195")
        self.order_btn.setToolTip(
            tr("Ordine delle fonti: alfabetico A-Z\n(clic per cambiare)") if az
            else tr("Ordine delle fonti: personale\n(clic per cambiare)"))
        if hasattr(self, "splitter"):
            self.apply_panel()

    def set_order_mode(self, mode, relayout=True):
        """L'ordine personale resta salvato anche mentre si guarda l'A-Z."""
        mode = "az" if mode == "az" else "custom"
        self.settings["order_mode"] = mode
        save_settings(self.settings)
        if relayout:
            self.relayout_order()
        else:
            self.update_order_button()

    def show_order_menu(self):
        menu = QMenu(self)
        current = self.settings.get("order_mode")
        for mode, label in (("custom", tr("Personale (trascina per riordinare)")),
                            ("az", tr("Alfabetico A-Z"))):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == mode or (mode == "custom" and current != "az"))
            act.triggered.connect(lambda _=False, m=mode: self.set_order_mode(m))
        menu.exec(self.order_btn.mapToGlobal(QPoint(0, self.order_btn.height())))

    def _apply_order(self, order):
        """Ordine salvato in un preset: lista personale + modo (personale / A-Z)."""
        names = [n for n in (order.get("list") or []) if isinstance(n, str) and n in self.sources]
        rest = [n for n in self.sources if n not in names]
        items = [(n, self.sources[n]) for n in names + rest]
        if [n for n, _ in items] != list(self.sources):
            self.sources.clear()
            self.sources.update(items)
            save_sources(self.sources)
        self.set_order_mode(order.get("mode"))

    def persist_presets(self):
        save_presets(self.presets, self.preset_pending)

    @staticmethod
    def _preset_label(name, data):
        text = name.replace("&", "&&")
        key = data.get("hotkey")
        return f"{text}\t{key.upper()}" if key else text

    def rebuild_tray_menu(self):
        """Menu della tray: Mostra/Nascondi, i preset (applicazione al volo) ed Esci."""
        menu = self.tray_menu
        menu.clear()
        menu.addAction(tr("Mostra / Nascondi")).triggered.connect(self.toggle_visible)
        menu.addSeparator()
        sub = menu.addMenu(tr("Preset"))
        self._add_preset_actions(sub)
        quick = [d for d in self.quick.get("devices", []) if isinstance(d, str)]
        if quick:
            menu.addSeparator()
            dsub = menu.addMenu(tr("Dispositivo di uscita"))
            for d in quick:
                act = dsub.addAction(self.display_name(d))
                act.setCheckable(True)
                act.setChecked(d == self.default_out)
                act.triggered.connect(lambda _=False, d=d: self.switch_to_device(d))
        menu.addSeparator()
        menu.addAction(tr("Esci")).triggered.connect(self.quit_app)

    def _add_preset_actions(self, menu):
        """Elenco dei preset (quello attivo spuntato) e, se c'e' un preset attivo, la voce per
        disattivarlo e tornare a com'era prima."""
        if not self.presets:
            menu.addAction(tr("Nessun preset salvato")).setEnabled(False)
            return
        for n, d in self.presets.items():
            act = menu.addAction(self._preset_label(n, d))
            act.setCheckable(True)
            act.setChecked(n == self.active_preset)
            act.triggered.connect(lambda _=False, n=n: self.apply_preset(n))
        if self.active_preset:
            menu.addSeparator()
            label = self.active_preset.replace("&", "&&")
            menu.addAction(tr("Disattiva «{name}» (torna a prima)", name=label)).triggered.connect(
                self.deactivate_preset)

    def refresh_preset_ui(self):
        """Dopo ogni modifica ai preset: aggiorna il menu della tray e la finestra Preset."""
        self.rebuild_tray_menu()
        name = self.active_preset
        if name:
            short = name if len(name) <= 14 else name[:13] + "\u2026"
            self.preset_btn.setText(tr("Preset: {name} ▾", name=short))
        else:
            self.preset_btn.setText(tr("Preset") + " \u25be")
        if self._presets_dlg is not None:
            self._presets_dlg.rebuild()

    def show_preset_menu(self):
        menu = QMenu(self)
        if self.presets:
            menu.addAction(tr("Applica preset")).setEnabled(False)
        self._add_preset_actions(menu)
        menu.addSeparator()
        menu.addAction(tr("Salva stato attuale…")).triggered.connect(self.save_preset_dialog)
        menu.addAction(tr("Gestisci preset…")).triggered.connect(self.open_presets_dialog)
        menu.exec(self.preset_btn.mapToGlobal(QPoint(0, self.preset_btn.height())))

    def open_presets_dialog(self):
        if self._presets_dlg is not None:
            return
        self._presets_dlg = PresetsDialog(self)
        self._presets_dlg.exec()
        self._presets_dlg = None

    def capture_preset(self):
        """Fotografia dello stato attuale: volume e mute delle app, solo, uscite, fonti visibili,
        ordine delle fonti, master e suoni di sistema. Di un'app che in questo momento non suona si salva l'ultimo
        volume e mute noti (se l'app e' stata vista da quando il mixer e' aperto).
        Il mute salvato e' quello "vero" dell'app, non quello imposto dal solo."""
        apps = {}
        for name, info in self.sources.items():
            entry = {"output": info.get("output")}
            sessions = self.groups.get(name)
            if sessions:
                try:
                    vol = sessions[0].SimpleAudioVolume
                    entry["volume"] = round(self.guard.user_volume(
                        name, self.fader.volume_state(sessions, vol.GetMasterVolume())), 4)
                    entry["mute"] = self.solo_prev.get(
                        name, self.fader.mute_state(sessions, bool(vol.GetMute())))
                except Exception:
                    pass
            elif name in self.last_audio:
                entry.update(self.last_audio[name])
            apps[name] = entry
        preset = {"apps": apps,
                  "visible": {n: bool(i.get("visible")) for n, i in self.sources.items()},
                  "solo": sorted(n for n in self.solo_set if n in self.sources),
                  "order": {"mode": "az" if self.settings.get("order_mode") == "az" else "custom",
                            "list": list(self.sources)}}
        ep = self.master.endpoint
        if ep is not None:
            try:
                preset["master"] = {"volume": round(ep.GetMasterVolumeLevelScalar(), 4),
                                    "mute": bool(ep.GetMute())}
            except Exception:
                pass
        if self.system.sessions:
            try:
                sys_sess = self.system.sessions
                vol = sys_sess[0].SimpleAudioVolume
                preset["system"] = {
                    "volume": round(self.fader.volume_state(sys_sess, vol.GetMasterVolume()), 4),
                    "mute": self.fader.mute_state(sys_sess, bool(vol.GetMute()))}
            except Exception:
                pass
        return preset

    def _store_preset(self, name):
        new = self.capture_preset()
        old = self.presets.get(name)
        if old and old.get("hotkey"):
            new["hotkey"] = old["hotkey"]
        self.presets[name] = new
        self.persist_presets()
        self.refresh_preset_ui()

    def save_preset_dialog(self):
        name = ask_text(self, tr("Salva preset"), tr("Nome del preset:"))
        name = (name or "").strip()
        if not name:
            return
        if name in self.presets and not confirm(
                self, tr("Salva preset"),
                tr("Esiste già un preset «{name}». Sovrascriverlo con lo stato attuale?", name=name),
                tr("Sovrascrivi")):
            return
        self._store_preset(name)

    def update_preset(self, name):
        if name not in self.presets:
            return
        if confirm(self, tr("Aggiorna preset"),
                   tr("Sovrascrivere il preset «{name}» con lo stato attuale?", name=name),
                   tr("Aggiorna")):
            self._store_preset(name)

    def rename_preset(self, old):
        if old not in self.presets:
            return
        new = ask_text(self, tr("Rinomina preset"), tr("Nuovo nome per «{name}»:", name=old), old)
        new = (new or "").strip()
        if not new or new == old:
            return
        if new in self.presets:
            QMessageBox.warning(self, tr("Rinomina preset"),
                                tr("Esiste già un preset «{name}».", name=new))
            return
        self.unregister_preset(old)
        self.presets = {(new if k == old else k): v for k, v in self.presets.items()}
        if self.active_preset == old:
            self.active_preset = new
        for r in self.automations.get("rules") or []:
            if r.get("preset") == old:
                r["preset"] = new
        save_automations(self.automations)
        self.register_preset(new)
        self.persist_presets()
        self.refresh_preset_ui()

    def delete_preset(self, name):
        if name not in self.presets:
            return
        if not confirm(self, tr("Elimina preset"), tr("Eliminare il preset «{name}»?", name=name),
                       tr("Elimina")):
            return
        self.unregister_preset(name)
        self.presets.pop(name)
        if self.active_preset == name:
            self.active_preset = None
            self.preset_undo = None
        self.persist_presets()
        self.refresh_preset_ui()

    @staticmethod
    def _apply_audio(sessions, entry, name=None, fade=True):
        """Volume e mute di un preset. Con `fade` scivolano gradualmente (se la dissolvenza e'
        attiva); senza, per le app appena comparse, che non devono partire a volume pieno."""
        if not fade:
            for s in sessions:
                try:
                    vol = s.SimpleAudioVolume
                    if "volume" in entry:
                        vol.SetMasterVolume(max(0.0, min(1.0, float(entry["volume"]))), None)
                    if "mute" in entry:
                        vol.SetMute(int(bool(entry["mute"])), None)
                except Exception:
                    pass
            return
        try:
            if "volume" in entry:
                fade_volume(sessions, float(entry["volume"]))
            if "mute" in entry:
                fade_mute(sessions, bool(entry["mute"]), name)
        except Exception:
            pass

    def _preset_output(self, name, device_id):
        info = self.sources.get(name)
        if info is None or (info.get("output") or None) == (device_id or None):
            return
        self.set_output(name, device_id, silent=True)

    def apply_preset(self, name):
        preset = self.presets.get(name)
        if not preset:
            return
        if self.active_preset is None:
            self.preset_undo = self.capture_preset()
        self.active_preset = name
        self._apply_data(preset)
        self.refresh_preset_ui()

    def deactivate_preset(self):
        """Toglie il preset attivo e riporta volumi, mute, solo, uscite, fonti visibili e ordine
        com'erano prima di attivarlo."""
        if self.active_preset is None:
            return
        undo = self.preset_undo
        self.active_preset = None
        self.preset_undo = None
        if undo:
            self._apply_data(undo)
        self.refresh_preset_ui()

    def _apply_data(self, preset):
        if self.solo_set:
            self.solo_set.clear()
            self.restore_solo()
            for strip in self.strips.values():
                strip.solo.blockSignals(True)
                strip.solo.setChecked(False)
                strip.solo.blockSignals(False)
        changed = False
        for n, shown in (preset.get("visible") or {}).items():
            info = self.sources.get(n)
            shown = bool(shown)
            if info is None or bool(info.get("visible")) == shown:
                continue
            info["visible"] = shown
            changed = True
            row = self.rows.get(n)
            if row is not None:
                row.eye.blockSignals(True)
                row.eye.setChecked(shown)
                row.eye.blockSignals(False)
                row.refresh_style()
        if changed:
            save_sources(self.sources)
        order = preset.get("order")
        if isinstance(order, dict):
            self._apply_order(order)
        master = preset.get("master")
        if isinstance(master, dict) and self.master.endpoint is not None:
            try:
                if "volume" in master:
                    self.master.endpoint.SetMasterVolumeLevelScalar(
                        max(0.0, min(1.0, float(master["volume"]))), None)
                if "mute" in master:
                    self.master.endpoint.SetMute(int(bool(master["mute"])), None)
            except Exception:
                pass
            self.master.sync()
        system = preset.get("system")
        if isinstance(system, dict) and self.system.sessions:
            self._apply_audio(self.system.sessions, system, SYSTEM_NAME)
            self.system.sync()
        pending = {}
        for n, entry in (preset.get("apps") or {}).items():
            if n == SYSTEM_NAME or not isinstance(entry, dict):
                continue
            sessions = self.groups.get(n)
            if sessions:
                self._apply_audio(sessions, entry, n)
                if "output" in entry:
                    self._preset_output(n, entry["output"])
            else:
                pending[n] = dict(entry)
        for n in preset.get("solo") or []:
            if not isinstance(n, str) or not self.sources.get(n, {}).get("visible"):
                continue
            if self.groups.get(n):
                self.solo_set.add(n)
            else:
                pending.setdefault(n, {})["solo"] = True
        self.preset_pending = pending
        self.persist_presets()
        self.sync_strips()
        if self.solo_set:
            self.apply_solo()
            for strip in self.strips.values():
                strip.sync()

    def apply_pending_preset(self):
        """Valori di un preset per le app che non suonavano: applicati appena compaiono."""
        if not self.preset_pending:
            return
        done = [n for n in self.preset_pending if self.groups.get(n)]
        for n in done:
            entry = self.preset_pending.pop(n)
            self._apply_audio(self.groups[n], entry, n, fade=False)
            if "output" in entry:
                self._preset_output(n, entry["output"])
            if entry.get("solo") and self.sources.get(n, {}).get("visible"):
                self.solo_set.add(n)
        if done:
            self.persist_presets()

    def register_preset(self, name):
        key = self.presets.get(name, {}).get("hotkey")
        if not key:
            return
        try:
            keyboard.add_hotkey(key, lambda n=name: self.bridge.preset_pressed.emit(n))
        except Exception as e:
            print("Tasto non valido:", key, e)

    def unregister_preset(self, name):
        key = self.presets.get(name, {}).get("hotkey")
        if key:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass

    def start_preset_bind(self, name):
        """Registra il tasto globale di un preset. False se sta gia' registrando un altro tasto."""
        if self.recording:
            return False
        self.recording = True

        def worker():
            try:
                key = keyboard.read_hotkey(suppress=False)
            except Exception:
                key = ""
            self.bridge.preset_recorded.emit(name, key)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def apply_preset_binding(self, name, key):
        self.recording = False
        preset = self.presets.get(name)
        if preset is None:
            self.refresh_preset_ui()
            return
        if key == "esc":
            key = ""
        if key:
            clash_apps = [a for a, k in self.config.items() if k == key]
            clash_presets = [p for p, d in self.presets.items()
                             if p != name and d.get("hotkey") == key]
            clash_quick = self.quick.get("hotkey") == key
            if clash_apps or clash_presets or clash_quick:
                used = ", ".join([tr("il mute di «{name}»", name=self.display_name(a)) for a in clash_apps]
                                 + [tr("il preset «{name}»", name=p) for p in clash_presets]
                                 + ([tr("il cambio rapido di dispositivo")] if clash_quick else []))
                if not confirm(self, tr("Tasto già in uso"),
                               tr("Il tasto {key} è già assegnato a: {used}.\n\n"
                                  "Vuoi usarlo per «{name}» e toglierlo da lì?",
                                  key=key.upper(), used=used, name=name),
                               tr("Riassegna")):
                    self.refresh_preset_ui()
                    return
                for a in clash_apps:
                    self.unregister(a)
                    self.config.pop(a, None)
                    if a in self.strips:
                        self.strips[a].set_key("")
                if clash_apps:
                    save_config(self.config)
                for p in clash_presets:
                    self.unregister_preset(p)
                    self.presets[p].pop("hotkey", None)
                if clash_quick:
                    self.unregister_quick_key()
                    self.quick.pop("hotkey", None)
                    save_quick_devices(self.quick)
                    if self._settings_dlg is not None:
                        self._settings_dlg._update_quick_key_btn()
        self.unregister_preset(name)
        if key:
            preset["hotkey"] = key
        else:
            preset.pop("hotkey", None)
        self.register_preset(name)
        self.persist_presets()
        self.refresh_preset_ui()

    def register_quick_key(self):
        key = self.quick.get("hotkey")
        if not key:
            return
        try:
            keyboard.add_hotkey(key, lambda: self.bridge.quick_pressed.emit())
        except Exception as e:
            print("Tasto non valido:", key, e)

    def unregister_quick_key(self):
        key = self.quick.get("hotkey")
        if key:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass

    def start_quick_bind(self):
        """Registra il tasto globale del cambio rapido. False se sta gia' registrando un altro
        tasto."""
        if self.recording:
            return False
        self.recording = True

        def worker():
            try:
                key = keyboard.read_hotkey(suppress=False)
            except Exception:
                key = ""
            self.bridge.quick_recorded.emit(key)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def apply_quick_binding(self, key):
        self.recording = False
        if key == "esc":
            key = ""
        if key:
            clash_apps = [a for a, k in self.config.items() if k == key]
            clash_presets = [p for p, d in self.presets.items() if d.get("hotkey") == key]
            if clash_apps or clash_presets:
                used = ", ".join([tr("il mute di «{name}»", name=self.display_name(a)) for a in clash_apps]
                                 + [tr("il preset «{name}»", name=p) for p in clash_presets])
                if not confirm(self, tr("Tasto già in uso"),
                               tr("Il tasto {key} è già assegnato a: {used}.\n\n"
                                  "Vuoi usarlo per il cambio rapido di dispositivo e toglierlo da lì?",
                                  key=key.upper(), used=used),
                               tr("Riassegna")):
                    if self._settings_dlg is not None:
                        self._settings_dlg._update_quick_key_btn()
                    return
                for a in clash_apps:
                    self.unregister(a)
                    self.config.pop(a, None)
                    if a in self.strips:
                        self.strips[a].set_key("")
                if clash_apps:
                    save_config(self.config)
                for p in clash_presets:
                    self.unregister_preset(p)
                    self.presets[p].pop("hotkey", None)
                if clash_presets:
                    self.persist_presets()
                    self.refresh_preset_ui()
        self.unregister_quick_key()
        if key:
            self.quick["hotkey"] = key
        else:
            self.quick.pop("hotkey", None)
        self.register_quick_key()
        save_quick_devices(self.quick)
        if self._settings_dlg is not None:
            self._settings_dlg._update_quick_key_btn()

    def remembered_volume(self, device_id):
        v = self.quick.get("volumes", {}).get(device_id)
        return v if isinstance(v, (int, float)) and 0 <= v <= 1 else None

    def switch_to_device(self, device_id):
        """Rende `device_id` il dispositivo di uscita predefinito di Windows. Ricorda il volume
        di quello che si lascia e, se conosce quello del dispositivo scelto, lo ripristina."""
        if not device_id or device_id == self.default_out:
            return
        old = self.default_out
        old_sess = self.dev_sessions.get(old) if old else None
        if old_sess is not None:
            try:
                self.quick.setdefault("volumes", {})[old] = round(
                    float(old_sess.SimpleAudioVolume.GetMasterVolume()), 4)
                save_quick_devices(self.quick)
            except Exception:
                pass

        raw_id = device_id[len(DEV_PREFIX):] if device_id.startswith(DEV_PREFIX) else device_id

        def worker():
            err = None
            try:
                self.device_switcher.apply(raw_id)
            except Exception as e:
                err = str(e) or e.__class__.__name__
            self.bridge.call_main.emit(lambda: self._switch_done(device_id, err))

        threading.Thread(target=worker, daemon=True).start()

    def _switch_done(self, device_id, err):
        if err:
            QMessageBox.warning(
                self, tr("Dispositivo di uscita"),
                tr("Non sono riuscito a cambiare il dispositivo predefinito.\n\n{err}\n\n"
                   "Questa funzione usa un'interfaccia non documentata di Windows, che potrebbe "
                   "non essere disponibile su questa versione.", err=err))
            return
        want = self.remembered_volume(device_id)
        sess = self.dev_sessions.get(device_id)
        if want is not None and sess is not None:
            try:
                sess.SimpleAudioVolume.SetMasterVolume(want, None)
            except Exception:
                pass
        self.rebuild_tray_menu()
        self.refresh()

    def cycle_quick_device(self):
        """Il tasto rapido: passa al dispositivo successivo tra quelli scelti (in ciclo)."""
        devices = [d for d in self.quick.get("devices", []) if isinstance(d, str)]
        if len(devices) < 2:
            return
        try:
            idx = devices.index(self.default_out)
            nxt = devices[(idx + 1) % len(devices)]
        except ValueError:
            nxt = devices[0]
        self.switch_to_device(nxt)

    def _catchup_time_rule(self, rules, now_minutes):
        """All'avvio, quale preset a orario dovrebbe gia' essere attivo: quello con l'orario piu'
        vicino e passato oggi, o se nessuno e' ancora passato, l'ultimo del giorno prima (come un
        termostato: il programma di ieri sera resta valido fino al primo orario di oggi)."""
        def minutes(r):
            h, m = r["time"].split(":")
            return int(h) * 60 + int(m)
        candidates = [r for r in rules if r.get("enabled", True) and r["type"] == "time"
                     and r.get("preset") in self.presets]
        if not candidates:
            return None
        past = [r for r in candidates if minutes(r) <= now_minutes]
        chosen = max(past, key=minutes) if past else max(candidates, key=minutes)
        return chosen["id"]

    def _run_automations(self, prev_apps, prev_out):
        """Preset automatici: scatta quando si apre o chiude un'app, quando cambia il dispositivo
        di uscita predefinito, o a un orario preciso. Le regole sono valutate nell'ordine in cui
        sono state create; se piu' di una scatta nello stesso istante, vince l'ultima. Chiamato a
        ogni refresh (circa una volta al secondo)."""
        first_run = self._automation_first_run
        self._automation_first_run = False
        data = self.automations
        if not data.get("enabled"):
            return
        rules = data.get("rules") or []
        curr_apps = {n for n in self.groups if not n.startswith(DEV_PREFIX)}
        opened = curr_apps - prev_apps
        closed = prev_apps - curr_apps
        out_changed = self.default_out is not None and self.default_out != prev_out
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        now_hm = time.strftime("%H:%M", now)
        now_minutes = now.tm_hour * 60 + now.tm_min
        catchup_id = self._catchup_time_rule(rules, now_minutes) if first_run else None
        for rule in rules:
            if not rule.get("enabled", True) or rule.get("preset") not in self.presets:
                continue
            t = rule["type"]
            fire = False
            if t == "app_open":
                fire = rule.get("app") in opened
            elif t == "app_close":
                fire = rule.get("app") in closed
            elif t == "output_device":
                fire = out_changed and rule.get("device") == self.default_out
            else:
                fire = (rule.get("time") == now_hm and self._time_fired.get(rule["id"]) != today
                       or rule["id"] == catchup_id)
                if fire:
                    self._time_fired[rule["id"]] = today
            if fire:
                self.apply_preset(rule["preset"])

    def set_automation_enabled(self, on):
        self.automations["enabled"] = bool(on)
        save_automations(self.automations)

    def save_rule(self, rule):
        rules = self.automations.setdefault("rules", [])
        for i, r in enumerate(rules):
            if r["id"] == rule["id"]:
                rules[i] = rule
                break
        else:
            rules.append(rule)
        save_automations(self.automations)

    def delete_rule(self, rule_id):
        rules = self.automations.get("rules") or []
        self.automations["rules"] = [r for r in rules if r["id"] != rule_id]
        self._time_fired.pop(rule_id, None)
        save_automations(self.automations)

    def set_rule_enabled(self, rule_id, on):
        for r in self.automations.get("rules") or []:
            if r["id"] == rule_id:
                r["enabled"] = bool(on)
                break
        save_automations(self.automations)

    def tick(self):
        try:
            self.guard.step()
        except Exception:
            if not getattr(self, "_guard_err", False):
                self._guard_err = True
                import traceback
                log_error("Errore nel ducking / limiti:\n" + traceback.format_exc())
        for strip in self.strips.values():
            strip.tick_meter()
        self.master.tick_meter()
        self.system.tick_meter()
        self._tick_n += 1
        if self._tick_n % 8 == 0:
            self.master.sync()
            for strip in self.strips.values():
                if isinstance(strip, DeviceStrip):
                    strip.sync()


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--set-output":
        sys.exit(cli_set_output(sys.argv[2], sys.argv[3], sys.argv[4]))
    if len(sys.argv) >= 4 and sys.argv[1] == "--set-default-device":
        sys.exit(cli_set_default_device(sys.argv[2], sys.argv[3]))
    try:
        import ctypes
        if not getattr(sys, "frozen", False):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("audiomixer.app")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("icon.ico")))
    _saved = load_settings()
    set_language(resolve_language(_saved["language"]))
    install_qt_translator(LANG)
    configure_appearance(_saved["theme"], _saved["accent"], _saved["ui_scale"])
    app.setStyleSheet(build_style())
    sys.excepthook = _log_excepthook
    for problem in LANGUAGE_PROBLEMS:
        log_error(problem)

    if already_running():
        sock = QLocalSocket()
        sock.connectToServer(APP_ID)
        if sock.waitForConnected(300):
            sock.write(b"show")
            sock.waitForBytesWritten(300)
        sys.exit(0)
    server = QLocalServer()
    QLocalServer.removeServer(APP_ID)
    server.listen(APP_ID)

    try:
        w = Mixer()
    except Exception:
        import traceback
        log_error("Errore all'avvio:\n" + traceback.format_exc())
        raise
    refresh_autostart_entry()

    def raise_window():
        server.nextPendingConnection()
        w.bring_to_front()

    server.newConnection.connect(raise_window)
    app.aboutToQuit.connect(w.shutdown)
    wants_tray = "--minimized" in sys.argv or w.settings.get("start_minimized")
    if not wants_tray:
        w.show()
    elif not QSystemTrayIcon.isSystemTrayAvailable():
        waited = [0]

        def wait_for_tray():
            waited[0] += 1
            if QSystemTrayIcon.isSystemTrayAvailable():
                tray_timer.stop()
                w.tray.hide()
                w.tray.show()
            elif waited[0] >= 60:
                tray_timer.stop()
                w.show()

        tray_timer = QTimer(app, interval=500, timeout=wait_for_tray)
        tray_timer.start()
    sys.exit(app.exec())
