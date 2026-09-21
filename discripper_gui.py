#!/usr/bin/env python3
"""
DiscRipper GUI - a window over exactly the same engine as the CLI.

Nothing is reimplemented here. This module drives the functions in
discripper.py, and the settings tabs are *generated* from that module's DEFAULTS
dictionary, so every option the program has is present and editable without this
file having to list them - a new setting appears in the window the moment it
exists in the config schema.

Launch with:  gui.cmd        (or "rip.cmd gui", or "discripper.py gui")

Requires tkinter. The bundled "embeddable" Python that "rip.cmd bundle" installs
deliberately ships without it; the text wizard covers every option too.
"""

import copy
import os
import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

# THE MAP'S COLOURS, from the one table that holds them. A leaf module
# with no imports of its own, so it can be pulled in HERE - at module
# scope, before any class body runs - which is the whole reason it is a
# separate file: SlimBar.PALETTE and MiniMonitor.LEGEND are class
# attributes evaluated at import, and this file must never import the
# engine (a second copy of a 35,000-line module with its own globals,
# including the ones the strip reads). See map_colour.py for what the
# eight drawing surfaces used to disagree about.
from map_colour import MAP_COLOUR, MAP_LEGEND, map_colour

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

# Two palettes, dark by default. The window is something you leave open for
# hours next to everything else, so it stays plain: conventional raised buttons
# and grooved, titled panels rather than flat accent-coloured slabs.
PALETTES = {
    # Neutral grey on purpose. A blue-tinted dark theme reads as generic
    # dark-mode chrome; with the cast taken out, the progress bar and the status
    # glyphs are the only strong colour on screen, which is where the eye should
    # go during a rip.
    "dark": {
        "bg":       "#252525",   # window
        "card":     "#2e2e2e",   # panels
        "row":      "#2e2e2e",   # settings row
        "row_alt":  "#353535",   # settings row, striped
        "border":   "#4a4a4a",
        "edge_hi":  "#5a5a5a",   # raised-edge highlight
        "edge_lo":  "#1c1c1c",   # raised-edge shadow
        "btn":      "#3c3c3c",
        "btn_act":  "#484848",
        "field":    "#202020",
        "text":     "#e8e8e8",
        "muted":    "#aaaaaa",
        # 5.05:1 on #252525. Was #828282 - 3.99:1, under the 4.5:1 WCAG AA
        # floor for text this small, and this is the palette the progress
        # strip draws its smallest readouts in.
        "faint":    "#949494",
        "accent":   "#5d9bf2",
        "ok":       "#6fc48d",
        "warn":     "#e2b25c",
        "err":      "#ef7f77",
        "track":    "#1c1c1c",
        "status":   "#202020",
        "disabled": "#8f8f8f",
        # THE OTHER PROCESS. A background encode's lines land in the same log
        # as the rip in the drive, and until this they were the same grey - so
        # two films reported into one column with nothing to tell them apart.
        # Violet because every other signal colour here is spoken for: accent
        # is blue, ok green, warn amber, err red. 7.4:1 on the log's #202020.
        "bg_job":   "#b995e8",
    },
    "light": {
        "bg":       "#f0f0f0",
        "card":     "#f6f6f6",
        "row":      "#fbfbfb",
        "row_alt":  "#f2f2f2",
        "border":   "#9e9e9e",
        "edge_hi":  "#ffffff",
        "edge_lo":  "#909090",
        "btn":      "#e5e5e5",
        "btn_act":  "#d8d8d8",
        "field":    "#ffffff",
        "text":     "#121212",
        "muted":    "#545454",
        # 4.82:1 on #f0f0f0. Was #808080 - 3.47:1, the worse of the two.
        "faint":    "#696969",
        "accent":   "#14509b",
        "ok":       "#16713f",
        "warn":     "#8c5209",
        "err":      "#b3271f",
        "track":    "#dcdcdc",
        "status":   "#e4e4e4",
        "disabled": "#8d8d8d",
        "bg_job":   "#6a3d9a",   # 8.6:1 on #ffffff
    },
}

# Mutated in place so every module-level reference to CLR follows a theme change.
CLR = dict(PALETTES["dark"])


def set_palette(name):
    CLR.clear()
    CLR.update(PALETTES.get(name, PALETTES["dark"]))
    return name if name in PALETTES else "dark"

FONT = "Segoe UI"
MONO = "Consolas"

def choices_for(dr, dotted):
    """The valid values for a setting, from the engine's own table.

    A function and not a dict because this file never imports the engine - dr
    arrives in run() - and importing it here would load a second copy of a
    35,000-line module with its own globals, including the ones the strip reads.
    Same shape as language_choices and salvage_effort_text.

    THIS USED TO BE A TABLE HERE, sixty lines of it, in a different file from
    the code that validates the values. It had drifted: the window offered
    copy|opus|aac for video.acodec while flac was implemented and measured, and
    salvage|stop for video.on_read_errors while grind was live code - and
    because the combobox is readonly, both were unreachable from the window
    rather than merely undocumented. Five more settings named a closed set in a
    comment only and so got a blank text box. See SETTING_CHOICES.

    Returns None, not (), when there is no closed set: the callers test for
    None to decide picker-or-text-box, and an empty tuple is falsey the same
    way a missing key is.
    """
    vals = dr.setting_choices(dotted)
    return [str(v) for v in vals] if vals else None

AUDIO_FORMATS = ["flac", "wav", "alac", "wavpack", "opus", "mp3", "aac", "vorbis"]

# What each fit mode costs, said in the window rather than in the README
FIT_BLURB = {
    "pad": "Black bars are added so the whole picture fits. Nothing is lost - "
           "the bars are simply in the file instead of drawn by the player.",
    "crop": "Zoomed until the screen is full, and the edges are cut off. This "
            "is the TV's Zoom button, and it really does throw picture away.",
    "stretch": "Squashed to fill the screen. No pixels are lost and everybody "
               "is the wrong shape.",
}


def open_file(path):
    """Hand one file to whatever Windows opens it with."""
    try:
        os.startfile(str(path))     # noqa: S606 - it is the user's own output
        return True
    except (OSError, AttributeError):
        return False

# 0-100 settings that are a level rather than a count, and so want a slider
PERCENT_KEYS = ("alerts.volume", "general.mini_monitor_opacity")

# section -> (tab label, one-line orientation shown at the top of the tab)
SECTION_TABS = [
    ("general", "General", "How a rip behaves start to finish: which drive, what "
                           "to do about collisions, what happens when it ends."),
    ("audio", "Audio CD", "Audio CDs are ripped by cyanrip, with AccurateRip "
                          "verification and MusicBrainz tags."),
    ("video", "DVD / Blu-ray", "Video discs are decrypted and remuxed losslessly "
                               "by MakeMKV. Compression is optional and always "
                               "a second step."),
    ("data", "Data discs", "Data discs become a bit-exact image, or a plain copy "
                           "of their files."),
    ("alerts", "Alerts", "Sounds and desktop popups, per event."),
    ("tools", "Engines", "Where the external ripping programs live."),
]


def _dir_key(key):
    return key.endswith("out_dir")


def _file_key(key):
    return key.startswith("tools.")


def enable_dpi_awareness():
    """Tell Windows we scale ourselves.

    Without this a Tk window is rendered at 96 DPI and then bitmap-stretched by
    the compositor, which is exactly what "bad resolution" looks like on any
    display running above 100%."""
    import ctypes
    for attempt in (
            lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),  # per-monitor
            lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),  # system
            lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            if attempt() in (0, 1):     # S_OK, or a non-zero BOOL
                return True
        except Exception:
            continue
    return False


def claim_app_identity():
    """Tell Windows who we are, before any window exists.

    Without an explicit AppUserModelID the shell identifies a process by the
    executable behind it, and ours is python.exe - so the taskbar button wears
    the Python icon however carefully the window sets its own, and every Python
    program running groups under that one button.

    Must run before the first window is created, or the shell has already
    decided what to call us."""
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Nathan.DiscRipper")
        return True
    except Exception:
        return False


def _colorref(hexrgb):
    """#rrggbb -> the 0x00bbggrr integer every Win32 colour API wants."""
    h = hexrgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_window_chrome(win, dark, caption=None, text=None, border=None):
    """Colour the title bar to match the window.

    The strip with the minimise/maximise/close buttons is not ours: the desktop
    window manager paints it, so it stays light no matter what Tk is told, and a
    dark window under a white cap is the first thing the eye lands on. Windows 10
    1809 and later can be asked for a dark frame, and Windows 11 will take exact
    colours, so the bar ends up the same shade as the window below it."""
    import ctypes
    from ctypes import wintypes
    try:
        win.update_idletasks()
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = int(win.winfo_id())
        # Tk's toplevel is sometimes the frame and sometimes a child of it
        targets = [h for h in (ctypes.windll.user32.GetParent(hwnd), hwnd) if h]
    except Exception:
        return False

    def send(target, attr, value, size):
        try:
            return dwm.DwmSetWindowAttribute(
                wintypes.HWND(target), wintypes.DWORD(attr),
                ctypes.byref(value), size) == 0
        except Exception:
            return False

    ok = False
    for target in targets:
        flag = ctypes.c_int(1 if dark else 0)
        # 20 is the documented attribute; 19 is what 1809..1903 shipped with
        for attr in (20, 19):
            if send(target, attr, flag, 4):
                ok = True
                break
        for attr, colour in ((35, caption), (36, text), (34, border)):
            if colour:
                send(target, attr, ctypes.c_uint(_colorref(colour)), 4)
    if ok:
        try:                            # make the frame repaint immediately
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(targets[0]), None, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        except Exception:
            pass
    return ok


class TrayIcon:
    """An icon in the notification area, with a menu and a live tooltip.

    Two things make this awkward without a dependency, and both are dealt with
    here rather than by adding one. The icon has to be a real HICON, so it is
    built from bytes by the engine's disc_icon_bits. And a tray icon reports clicks by posting
    a message to a window, which normally means creating a hidden window and
    running a second message loop - instead the Tk window's own WNDPROC is
    subclassed, so Tk's existing event loop delivers them and there is no second
    thread. The handler only enqueues; anything that touches widgets is done by
    the main loop, out of the window procedure."""

    MSG = 0x0400 + 42                   # WM_APP + 42
    ADD, MODIFY, DELETE = 0, 1, 2
    TIP, MESSAGE, ICON = 0x04, 0x01, 0x02
    # Which windows already have one of these on them. A second instance would
    # chain its procedure in front of the first and both would answer the same
    # click, so refuse instead of quietly doing everything twice.
    _hooked = set()

    def __init__(self, app):
        self.app = app
        self.ok = False
        self._added = False
        self._hicon = None
        self._old_proc = None
        self._doubled = False
        try:
            self._install()
        except Exception:
            self.ok = False

    def _install(self):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        self._u32 = ctypes.windll.user32
        self._shell = ctypes.windll.shell32
        self.app.update_idletasks()
        self._hwnd = int(self.app.winfo_id())
        if self._hwnd in TrayIcon._hooked:
            self._doubled = True
            return

        bits = self.app.dr.disc_icon_bits()
        buf = ctypes.create_string_buffer(bits, len(bits))
        self._u32.CreateIconFromResourceEx.restype = wintypes.HICON
        small = self._u32.GetSystemMetrics(49) or 16      # SM_CXSMICON
        self._hicon = self._u32.CreateIconFromResourceEx(
            buf, len(bits), True, 0x00030000, small, small, 0)
        if not self._hicon:
            return

        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT),
                        ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128),
                        ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT),
                        ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD)]
        self._NID = NOTIFYICONDATA
        self._nid = NOTIFYICONDATA()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        self._nid.hWnd = self._hwnd
        self._nid.uID = 1
        self._nid.uFlags = self.TIP | self.MESSAGE | self.ICON
        self._nid.uCallbackMessage = self.MSG
        self._nid.hIcon = self._hicon
        self._nid.szTip = f"{self.app.dr.APP}"

        # Subclass Tk's window rather than making one of our own: Tk is already
        # pumping messages for this HWND, so the callbacks arrive for free.
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND,
                                     wintypes.UINT, ctypes.c_ulonglong,
                                     ctypes.c_longlong)
        self._proc = WNDPROC(self._on_message)
        setter = getattr(self._u32, "SetWindowLongPtrW", None) \
            or self._u32.SetWindowLongW
        setter.restype = ctypes.c_longlong
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        self._old_proc = setter(self._hwnd, -4,          # GWLP_WNDPROC
                                ctypes.cast(self._proc, ctypes.c_void_p).value)
        if not self._old_proc:
            return
        TrayIcon._hooked.add(self._hwnd)
        self.ok = True

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == self.MSG and self.ok:
            event = lparam & 0xFFFF
            if event in (0x0202, 0x0203):       # left button up / double click
                self.app.ui_q.put(("tray", "show"))
            elif event in (0x0205, 0x007B):     # right button up / context menu
                self.app.ui_q.put(("tray", "menu"))
        try:
            return self._u32.CallWindowProcW(
                self._ct.c_void_p(self._old_proc),
                self._ct.wintypes.HWND(hwnd), self._ct.wintypes.UINT(msg),
                self._ct.c_ulonglong(wparam), self._ct.c_longlong(lparam))
        except Exception:
            return 0

    def _notify(self, action):
        try:
            return bool(self._shell.Shell_NotifyIconW(
                action, self._ct.byref(self._nid)))
        except Exception:
            return False

    def add(self):
        if not self.ok or self._added:
            return self._added
        self._added = self._notify(self.ADD)
        return self._added

    def remove(self):
        if self._added:
            self._notify(self.DELETE)
            self._added = False

    def tip(self, text):
        """Up to 127 characters, and Windows truncates without complaint."""
        if not self.ok or not self._added:
            return
        self._nid.szTip = str(text)[:127]
        self._notify(self.MODIFY)

    def close(self):
        self.remove()
        if self._old_proc:
            try:
                setter = getattr(self._u32, "SetWindowLongPtrW", None) \
                    or self._u32.SetWindowLongW
                setter(self._hwnd, -4, self._old_proc)
            except Exception:
                pass
            self._old_proc = None
            TrayIcon._hooked.discard(self._hwnd)
        if self._hicon:
            try:
                self._u32.DestroyIcon(self._hicon)
            except Exception:
                pass
            self._hicon = None
        self.ok = False


def taskbar_bounds():
    """Where the taskbar is, and where the free space in it starts.

    Returns (left, top, right, bottom, clock_left) or None. Windows has no
    supported way to put a control *inside* the taskbar any more - deskbands were
    the mechanism and Windows 11 dropped them - so the strip is instead sized to
    the taskbar's own height and laid over it, which looks the same and needs no
    COM server registered. clock_left is the notification area's left edge, so
    the strip can park just before the clock rather than under the buttons."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        tray = u32.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return None
        r = wintypes.RECT()
        if not u32.GetWindowRect(tray, ctypes.byref(r)):
            return None
        clock = r.right
        notify = u32.FindWindowExW(tray, None, "TrayNotifyWnd", None)
        if notify:
            n = wintypes.RECT()
            if u32.GetWindowRect(notify, ctypes.byref(n)) and n.left > r.left:
                clock = n.left
        return r.left, r.top, r.right, r.bottom, clock
    except Exception:
        return None


class TaskbarProgress:
    """The rip's progress drawn inside the taskbar button, the way Explorer
    draws a file copy.

    ITaskbarList3 through ctypes - no dependency, and nothing to draw ourselves.
    Worth having even with the pinned strip switched on, because this is the one
    readout that survives minimising the window: amber when a rip stalls, red
    when it fails, so an overnight run that went wrong says so from the taskbar.

    Every method is silent on failure. A progress readout is a nicety, and no
    part of a rip should end because a shell interface would not bind."""

    CLSID = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
    IID = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
    # vtable: 3 HrInit, 9 SetProgressValue, 10 SetProgressState
    NOPROGRESS, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8
    # "waiting" is paused as well: a rip that has stopped to ask the user
    # something has stopped for the same reason as far as the button is
    # concerned - it is not going anywhere until somebody comes and looks.
    STATES = {"none": NOPROGRESS, "working": INDETERMINATE, "normal": NORMAL,
              "error": ERROR, "stalled": PAUSED, "waiting": PAUSED}

    def __init__(self, win):
        self.ok = False
        self._hwnd = self._p = self._state = None
        try:
            self._bind(win)
        except Exception:
            self.ok = False

    def _bind(self, win):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        ole32 = ctypes.windll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                        ("d3", wintypes.WORD), ("d4", ctypes.c_byte * 8)]
        clsid, iid = GUID(), GUID()
        for text, out in ((self.CLSID, clsid), (self.IID, iid)):
            if ole32.CLSIDFromString(wintypes.LPCWSTR(text),
                                     ctypes.byref(out)) != 0:
                return
        ole32.CoInitialize(None)
        p = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                  ctypes.byref(iid), ctypes.byref(p)) != 0:
            return
        vt = ctypes.cast(p, ctypes.POINTER(
            ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE
        if proto(ctypes.c_long, ctypes.c_void_p)(vt[3])(p) != 0:
            return
        self._value = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                            ctypes.c_ulonglong, ctypes.c_ulonglong)(vt[9])
        self._setstate = proto(ctypes.c_long, ctypes.c_void_p, wintypes.HWND,
                               ctypes.c_int)(vt[10])
        self._release = proto(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
        win.update_idletasks()
        # GA_ROOT: the button belongs to the top-level frame, and Tk's toplevel
        # is a child of it rather than the frame itself
        self._hwnd = ctypes.windll.user32.GetAncestor(int(win.winfo_id()), 2) \
            or int(win.winfo_id())
        self._p = p
        self.ok = True

    def state(self, name):
        """normal | working | stalled | error | none."""
        if not self.ok:
            return
        flag = self.STATES.get(name, self.NOPROGRESS)
        if flag == self._state:
            return
        self._state = flag
        try:
            self._setstate(self._p, self._ct.wintypes.HWND(self._hwnd), flag)
        except Exception:
            self.ok = False

    def set(self, frac):
        """Fraction done, or None for a stage with no percentage yet."""
        if not self.ok:
            return
        if frac is None:
            self.state("working")
            return
        self.state("normal")
        try:
            self._value(self._p, self._ct.wintypes.HWND(self._hwnd),
                        int(max(0.0, min(1.0, frac)) * 1000), 1000)
        except Exception:
            self.ok = False

    def attention(self, on=True):
        """Flash the button until the window is brought to the front.

        FlashWindowEx with FLASHW_TIMERNOFG is the shell's own way of saying a
        window wants somebody, and it stops on its own the moment the window
        gets the foreground - so nothing has to remember to turn it off if the
        user simply clicks the window. Turning it off explicitly is still done
        when the question is answered, for the case where it was answered
        without the window ever being focused."""
        if not self.ok:
            return False
        try:
            ct = self._ct
            from ctypes import wintypes

            class FLASHWINFO(ct.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD),
                            ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            info = FLASHWINFO()
            info.cbSize = ct.sizeof(FLASHWINFO)
            info.hwnd = wintypes.HWND(self._hwnd)
            # FLASHW_TRAY | FLASHW_TIMERNOFG, or FLASHW_STOP
            info.dwFlags = (0x00000002 | 0x0000000C) if on else 0
            info.uCount = 0
            info.dwTimeout = 0
            ct.windll.user32.FlashWindowEx(ct.byref(info))
            return True
        except Exception:
            return False

    def failed(self):
        """Leave the button red and full, so a rip that died overnight shows it
        without the window having to be found and read."""
        if self.ok:
            self.set(1.0)
            self.state("error")

    def clear(self):
        self.state("none")

    def close(self):
        if self._p is not None and self.ok:
            try:
                self.clear()
                self._release(self._p)
            except Exception:
                pass
        self.ok = False
        self._p = None


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------


class Scrollable(ttk.Frame):
    """A vertically scrolling container; the settings tabs are taller than any
    sensible window."""

    def __init__(self, parent, bg=None):
        super().__init__(parent, style="Bg.TFrame")
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=bg or CLR["bg"])
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Bg.TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_inner(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")


class SlimBar(tk.Canvas):
    """A progress bar drawn rather than themed, so its height is its own.

    The strip wants bars a few points tall and ttk will not give it them. Two
    measurements, neither of which the widget admits to:

      -thickness is accepted and ignored. vista reports 22 points for thickness
      3, 8, 15 and 30 alike; clam 18 for all four.

      Constraining the height from outside - a frame with propagation off, which
      is the only thing that does change how tall a themed bar draws - stops the
      fill being painted. identify() still reports the pbar element sitting
      there at 5 points, so the layout is fine and the paint is not; the bar
      comes out an empty trough.

    So this is a trough and a rectangle placed across a fraction of it, which
    works at any height down to one point, on any theme, and can be checked by
    asking the fill how wide it is rather than by counting pixels.
    """

    # THE SAME COLOURS THE DOCS USE, FROM THE SAME TABLE, so a bar in the
    # window and a strip in docs/two-ways-to-cross-damage.html cannot mean
    # different things by the same amber - which is exactly what they did
    # until 5 Sep. Asked for 2 Sep; see map_colour.py, which records the four
    # places the two had drifted.
    #
    # `ground=None` is how the unmeasured states arrive as None here, and None
    # is the honest drawing on a bar: "skipped sections the same gray as the
    # beginning unfilled bar", asked for in those words. Nothing in a skipped
    # stretch was measured, so it is in exactly the state the unread end of
    # the bar is in, and a colour of its own said "here is a finding" about
    # ground nobody asked a question of.
    PALETTE = dict(
        {g: map_colour(g, ground=None) for g in MAP_COLOUR},
        # THE MUX PIECES, which are not map states and so are not in that
        # table. See mux_picture: step 6 draws the film on its own clock, and
        # a new piece means the mux hit damage it had to restart past - so the
        # seam between two colours is a place worth seeing.
        **{"1": "#8f6ad6",      # violet
           "2": "#2f9e9e",      # teal
           "3": "#c9a227",      # gold
           "4": "#c46b9e",      # rose
           "5": "#4a8fd6",      # cyan-blue
           "6": "#8a9440"})     # olive

    def __init__(self, parent, height=6):
        super().__init__(parent, height=max(1, int(height)), bd=0,
                         highlightthickness=0, background=CLR["track"])
        self._frac = 0.0
        self._done = False
        self._tint = CLR["accent"]
        # THE PICTURE, when there is one. A string of map status characters,
        # one per cell - see discripper.map_cells. Empty for work that has no
        # map (an encode, a scan) and then the bar is a plain fill, which is
        # all a job with one number to report can honestly be.
        self._cells = ""
        self._runs = ()         # (lo, hi, colour) coalesced, in cell units
        # AND AN OVERRIDE, for things the map cannot express: a probe in
        # flight, an edge measured a moment ago, a view that is not the whole
        # disc. [(lo_frac, hi_frac, colour)] in 0..1 of the bar's width, so
        # the animator does the sector arithmetic and the bar only draws.
        # None, not (): see set_paint - an empty view is not the same thing
        # as no view.
        self._paint = None
        self._head = None
        self.bind("<Configure>", self._on_size, add="+")

    def _on_size(self, _e=None):
        self._draw()

    def set(self, frac):
        """frac None reads as nothing done, which is what an idle bar is."""
        self._frac = max(0.0, min(1.0, float(frac or 0.0)))
        self._draw()
        return self._frac

    def set_cells(self, cells):
        """Colour per section, from the map. "" goes back to a plain fill.

        Coalesced here rather than at draw time: the string is a couple of
        hundred characters and mostly one run, and <Configure> fires in bursts
        while a strip is dragged.
        """
        cells = str(cells or "")
        if cells == self._cells:
            return
        self._cells = cells
        runs, i = [], 0
        while i < len(cells):
            j = i
            while j < len(cells) and cells[j] == cells[i]:
                j += 1
            col = self.PALETTE.get(cells[i], None)
            if col:
                runs.append((i, j, col))
            i = j
        self._runs = tuple(runs)
        self._draw()

    def cells(self):
        """The map string this bar is drawn from.

        Also what a SECOND bar reuses: the overview shown under a zoomed bar
        draws the same picture of the disc the main bar was drawing before the
        zoom took its axis away - see set_zoom_overview - and it has to be the
        same picture rather than one assembled separately, or the two bars
        would disagree about the disc.
        """
        return self._cells

    def set_paint(self, items):
        """Draw exactly these spans, in 0..1 of the width. None - and only
        None - gives the map back.

        Not merged with the map: while this is set the bar is showing
        something other than the whole disc, and mixing the two would draw the
        disc's own colours at a scale they do not mean anything at.

        () IS AN EMPTY VIEW, NOT THE END OF ONE, and until 8 Sep it was both.
        A zoomed bar has nothing to draw on any frame where the pulse is off
        and nothing inside the bracket has been measured yet - which is every
        other frame of a bracket sitting on a stalled read. Each of those
        frames handed the bar back, so a salvage stopped on one bad block
        swapped the whole 400-pixel picture of the disc in and out twice a
        second. Measured on Guardians Of The Galaxy, stalled at 10.72 GB: one
        blue pixel, then 400 pixels of green, 570 ms apart, for as long as the
        read took.
        """
        if items is None:
            want = None
        else:
            want = tuple((max(0.0, min(1.0, float(a))),
                          max(0.0, min(1.0, float(b))), c)
                         for a, b, c in items if c)
        if want == self._paint:
            return
        self._paint = want
        self._draw()

    def painting(self):
        return self._paint is not None


    def set_head(self, frac):
        """A caret where the drive is reading, or None for none.

        Drawn last so it is never buried by a section, and in the accent -
        the one colour on the bar that is not a claim about the disc.
        """
        want = None if frac is None else max(0.0, min(1.0, float(frac)))
        if want == getattr(self, "_head", None):
            return
        self._head = want
        self._draw()

    def _draw(self):
        """One rectangle per run, or one for the fraction when there is no map.

        Canvas rather than placed Frames: a salvage ends with hundreds of runs
        and that many widgets costs more to lay out than the whole strip.
        """
        try:
            self.delete("seg")
            w = max(1, int(self.winfo_width()))
            h = max(1, int(self.winfo_height()))
        except tk.TclError:
            return
        try:
            if self._paint is not None:
                for a, b, col in self._paint:
                    x0 = int(a * w)
                    # AT LEAST A PIXEL, for the same reason a map run gets
                    # one: a probe is a single sector and it is the whole
                    # point of the picture.
                    x1 = max(x0 + 1, int(b * w))
                    self.create_rectangle(x0, 0, x1, h, fill=col,
                                          width=0, tags="seg")
                return
            if self._runs:
                n = max(1, len(self._cells))
                for lo, hi, col in self._runs:
                    x0 = int(lo * w / n)
                    # AT LEAST ONE PIXEL. A run narrower than a pixel is
                    # exactly the run worth seeing - one bad cluster in 46 GB
                    # - and rounding it away would hide the finding.
                    x1 = max(x0 + 1, int(hi * w / n))
                    self.create_rectangle(x0, 0, x1, h, fill=col,
                                          width=0, tags="seg")
                return
            if self._frac > 0:
                self.create_rectangle(0, 0, max(1, int(self._frac * w)), h,
                                      fill=self._tint, width=0, tags="seg")
        except tk.TclError:
            pass
        finally:
            self._draw_head(w, h)

    def _draw_head(self, w, h):
        """LAST, and outside the returns above. Every branch of _draw returns
        early, so a caret drawn inside one of them would only appear in that
        one case - which is how it went missing the first time.

        NOT OVER A ZOOMED VIEW. `_head` is a fraction of the whole disc and a
        painted bar is showing a 32 MB window of it, so drawing one on the
        other puts the caret wherever the arithmetic happens to land. The
        probe sliver already marks the read there, in the window's own
        coordinates.
        """
        if self._head is None or self._paint is not None:
            return
        try:
            x = max(1, min(w - 1, int(self._head * w)))
            self.create_rectangle(x - 1, 0, x + 1, h, fill=CARET,
                                  width=0, tags="seg")
        except tk.TclError:
            pass

    def frac(self):
        return self._frac

    def tint(self, colour=None):
        """Recolour the fill. None restores the ordinary accent.

        A bar that is not moving looks the same whether the rip finished or
        the drive is sitting on a read it will never return. Measured: the
        strip held 10.27 GiB for 21m48s and nothing on it said which.
        """
        want = colour or CLR["accent"]
        if getattr(self, "_tint", None) == want:
            return
        self._tint = want
        self._draw()

    def set_done(self, done):
        """Green for arrived, accent for on the way - a full bar and a nearly
        full one are the same shape at a glance, and two colours are not."""
        self._done = bool(done)
        self.repaint()

    def is_done(self):
        return self._done

    def fill_colour(self):
        return str(self._tint)

    def set_height(self, px):
        try:
            self.configure(height=max(1, int(px)))
        except tk.TclError:
            pass
        self._draw()

    def repaint(self, trough=None):
        """Colours re-read from CLR, so a theme switch lands on them too.

        AND THE TINT MEMO GOES WITH IT. tint() skips the work when the colour
        it is asked for is the one it last applied - but this sets the fill
        directly, so after any repaint that memo described a colour the bar no
        longer had. It went stale in both directions: a later tint() to the
        remembered colour did nothing when it should have painted, and the
        symptom the owner found - resizing the strip "fixing" a stuck amber bar
        - was this path quietly overwriting a state nobody had cleared.
        """
        want = CLR["ok"] if self._done else CLR["accent"]
        self._tint = want
        try:
            self.configure(background=trough or CLR["track"])
        except tk.TclError:
            pass
        self._draw()


# WHERE THE DRIVE IS READING, on a bar that is mostly green.
#
# Not CLR["accent"]: at #5d9bf2 that is luminance 0.31 against the read bar's
# 0.29 - a 1.07:1 ratio, which at two pixels wide is invisible. This is the
# same hue three steps darker, 3.6:1 against green and 4.9:1 against the
# amber of a fabricated section.
CARET = "#14294f"


class FocusAnimator:
    """The bar zooming in on a probe, and out again.

    ASKED FOR 2 Sep, in detail, for the island probes and for a block
    refusing. What it is for: those are the two moments in a salvage where the
    interesting thing is happening in a span too small to see. A 32 MB bracket
    is 0.07% of a Blu-ray, so on a full-length bar the whole of the island
    phase happens inside one pixel - the bar sits still for minutes while the
    most interesting work of the run goes on inside it.

    HOW THE ZOOM IS DONE. Not by growing a rectangle: by moving the WINDOW the
    bar is drawn over, from the whole disc down to the span, interpolated. The
    request described it as "that section's coloration expand[ing] outward
    until it occupies the whole progress bar" and observed that it works
    "because every section that's not blue is surrounded in blue" - which is
    exactly what interpolating the window looks like, and it stays honest at
    every intermediate frame because every frame is a real view of real
    positions rather than a rectangle being stretched.

    IT IS FED BY POLLING, not by events. discripper.focus_state() is a
    snapshot of where the engine is looking and what just happened there;
    `seq` tells a new event from the same one polled twice. The engine has no
    idea any of this exists.
    """

    # A tick of its own rather than the strip's 80 ms drain, because the
    # blinks want a rate chosen for the eye and the drain's is chosen for the
    # queue.
    TICK_MS = 16

    # ...and the two blinks want DIFFERENT rates, which was asked for: the
    # probe pulse is "flashing fairly slowly, so it catches the eye", and the
    # failure is "a little quicker than the blue sliver pulse". Slow and
    # steady reads as waiting; fast reads as something having happened.
    PULSE_MS = 570              # the probe in flight
    FAIL_MS = 205               # ...and the two flashes when it refuses
    FAIL_FLASHES = 2

    LEAD_FLASHES = 3            # before the zoom, asked for by name
    LEAD_MS = 258
    ZOOM_MS = 950
    HOLD_MS = 680               # the "brief pause" before it repeats
    # How long "Error isolated - continuing" stays up after the zoom has
    # finished. Asked for by name: two seconds.
    SETTLED_MS = 2000

    # A FIFTH OF THE BAR, centred on the span. Asked for in those words. It
    # is the smallest window that reads as "over here" rather than as a
    # flicker at a single pixel.
    LEAD_SPAN = 0.2

    # THE MAP'S OWN THREE, from the map's own table - see map_colour.py. The
    # animator paints the same three states the bar underneath it paints, so a
    # probe that lands green and a cell that is green have to be the same
    # green; they were three more literals until 5 Sep.
    LIED = map_colour("F")      # the drive answered, and the answer was false
    BAD = map_colour("-")
    GOT = map_colour("+")
    # ...and the quiet blue-grey of ground nobody ever asked about, which is
    # what the second look goes back for. NOT red: the map draws a skipped
    # stretch in this because nothing in it was measured, and flashing it red
    # would say the disc had refused something it was never asked.
    SKIPPED = map_colour("x")
    # ...and three that are the ANIMATION rather than the map: where the strip
    # is looking, not what it found.
    NAVY = "#26467d"            # the island lead-in
    PROBE = "#1b3f8f"           # a probe: in flight, and solid when it lands
    # ...and a lighter one for the moment a probe SUCCEEDS, where the point is
    # that it stands out from the bar it is about to become part of.
    PROBE_LIT = "#4f86e8"

    def __init__(self, strip):
        self.strip = strip
        # TWO HOSTS, ONE ANIMATOR. The strip has an `.app`; the window IS the
        # app. Everything else it needs - a bar, an `after`, somewhere to put
        # the endpoints - both of them have.
        self.app = getattr(strip, "app", strip)
        self.state = "idle"
        self.seq = -1
        self.kind = ""
        self.lo = self.hi = 0
        self.t0 = 0.0
        self.n = 0
        self._job = None
        self._last_probe = None

    # -- the clock ------------------------------------------------------
    def start(self):
        if self._job is None:
            self._schedule()

    def stop(self):
        if self._job is not None:
            try:
                self.strip.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def _schedule(self):
        try:
            self._job = self.strip.after(self.TICK_MS, self._tick)
        except tk.TclError:
            self._job = None

    def _now(self):
        import time as _t
        return _t.time()

    def _phase(self, ms):
        """How far through the current phase, 0..1, and how many times the
        clock has gone round."""
        gone = (self._now() - self.t0) * 1000.0
        return min(1.0, gone / max(1, ms)), int(gone // max(1, ms))

    def _enter(self, state):
        self.state = state
        self.t0 = self._now()
        self.n = 0

    # -- the frame ------------------------------------------------------
    def _tick(self):
        self._job = None
        try:
            if not self.strip.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._frame()
        except Exception:                                        # noqa: BLE001
            # A DRAWING BUG MUST NOT STOP A RIP, or even stop the strip. The
            # animation is a nicety over a run that is doing real work.
            #
            # TclError USED TO RETURN HERE, and that was the worst of the
            # three outcomes: no release and no reschedule, so one failed
            # frame retired the clock with the bar still painted over - a
            # zoomed view of the last span left on a bar reporting a
            # different film, with nothing left running to take it off.
            # Releasing is what a frame that cannot be drawn should do.
            try:
                self._release()
            except tk.TclError:
                pass
        self._schedule()

    def _release(self):
        """Give the bar back to the map."""
        self.state = "idle"
        self.kind = ""
        try:
            self.strip.bar.set_paint(None)
            self.strip.set_focus_ends("", "")
            self._say("")
            self._caption("")
            self._overview(None)
        except tk.TclError:
            pass

    def _say(self, text):
        """One line about the zoom, where there is a line for it."""
        fn = getattr(self.strip, "set_focus_facts", None)
        if fn is not None:
            fn(text)

    # What the line above the bar says at each stage of the zoom, and in what
    # colour. "" hands the line back to the stage text.
    CAP_FOUND = "Error segment detected"
    CAP_LEFT = "Finding left boundary"
    CAP_RIGHT = "Finding right boundary"
    CAP_DONE = "Error isolated - continuing"
    CAP_ISLAND = "Probing inside the damage"
    CAP_REREAD = "Re-reading what the drive invented"
    # THE SECOND LOOK'S THREE, and they are not the others' three with a word
    # changed. Nothing here has failed: the sweep stepped past this stretch on
    # a clock without asking the drive for any of it, and the pass now
    # underway is the one that finds out whether it was ever unreadable. So
    # the notice is not "error", and the verdict is not "isolated".
    CAP_SKIPPED = "Skipped section - going back for it"
    CAP_SKIPPED_AT = "Reading it back from the far end"
    CAP_SKIPPED_DONE = "Second look done - continuing"

    def _lead_caption(self):
        """The notice, and whether it is red.

        Red means the disc refused something. A skipped stretch is the one
        span the strip zooms to that nobody has asked a question of yet.
        """
        if self.kind == "skipped":
            return self.CAP_SKIPPED, ""
        return self.CAP_FOUND, "err"

    def _done_caption(self):
        """The verdict, once the bar is back at full length."""
        if self.kind == "skipped":
            return self.CAP_SKIPPED_DONE
        return self.CAP_DONE

    def _lead_colour(self):
        """What the lead-in flash is drawn in, per kind.

        NAVY FOR AN ISLAND, RED FOR A REFUSAL. Asked for: those two want
        telling apart before the zoom, because one is the app going looking
        for film and the other is the disc refusing to give any. A re-read is
        the amber the map gives a fabricated unit, and the second look is a
        fourth thing again - ground nobody has asked - which flashes in the
        map's own quiet blue-grey for that.
        """
        return {"island": self.NAVY, "reread": self.LIED,
                "skipped": self.SKIPPED}.get(self.kind, self.BAD)

    def _overview(self, lo=None, hi=None, colour=""):
        """The whole-film bar under the zoomed one, or nothing.

        Only where there is room: the window always has it, the detached strip
        has it at its taller sizes, and docked there is no row to spare - see
        set_zoom_overview on each host.
        """
        fn = getattr(self.strip, "set_zoom_overview", None)
        if fn is None:
            return
        try:
            fn(lo, hi, colour)
        except tk.TclError:
            pass

    def _caption(self, text, colour=""):
        """Take over the line above the bar, or give it back with ""."""
        if getattr(self, "_cap", None) == (text, colour):
            return
        self._cap = (text, colour)
        fn = getattr(self.strip, "set_focus_caption", None)
        if fn is not None:
            try:
                fn(text, colour)
            except tk.TclError:
                pass

    def _focused_caption(self, st):
        """What is being looked for, now that there is a bar to look at."""
        if self.kind == "island":
            return self.CAP_ISLAND
        if self.kind == "reread":
            return self.CAP_REREAD
        if self.kind == "skipped":
            return self.CAP_SKIPPED_AT
        side = str(st.get("side") or "")
        if side == "left":
            return self.CAP_LEFT
        if side == "right":
            return self.CAP_RIGHT
        return self.CAP_FOUND

    def _facts(self, st):
        """What this span is and how far through its questions we are.

        THE COLOURS CANNOT SAY ANY OF THIS. They say where; a person watching
        an island phase wants to know how many questions it is allowed to ask
        and how many came back, because that is what decides whether the next
        few minutes are worth waiting for.
        """
        dr = self.app.dr
        sec = dr.SectorReader.SECTOR
        span = max(1, self.hi - self.lo)
        mb = span * sec / 1e6
        film = span * sec / dr.BD_FEATURE_BYTES_PER_SEC
        bits = []
        if self.kind == "reread":
            # WHAT THE PHASE IS ACTUALLY DOING, which is not obvious from a
            # bar that empties and refills: each pass asks the whole band
            # again and keeps only what verifies.
            p, np_ = int(st.get("pass_n") or 0), int(st.get("passes") or 0)
            bits.append(f"re-reading {mb:,.1f} MB the drive answered for "
                        f"but did not read")
            if np_:
                bits.append(f"pass {p} of {np_}")
            _h = sum(b - a for a, b in (st.get("held") or ()))
            if _h:
                bits.append(f"{_h * sec / 1e6:,.1f} MB verified so far")
            _l = sum(b - a for a, b in (st.get("lied") or ()))
            if _l:
                bits.append(f"{_l * sec / 1e6:,.1f} MB still false")
            return "   ·   ".join(bits)
        if self.kind == "skipped":
            # WHAT THE PASS IS FOR, in the only two numbers that decide
            # whether it is worth the minutes: how much film was written off
            # unasked, and how much of it has come back. Nothing here is a
            # measurement of the disc yet, which is why it does not say
            # "damage" - the sweep stepped past this on a clock.
            bits.append(f"{mb:,.1f} MB stepped past unasked"
                        f" - {film:,.1f} s of film")
            back = sum(b - a for a, b in (st.get("got") or ())) * sec / 1e6
            bits.append(f"{back:,.1f} MB back so far" if back >= 0.05
                        else "reading it back from the far end")
            return "   ·   ".join(bits)
        if self.kind == "island":
            bits.append(f"inside {mb:,.1f} MB of damage"
                        f" - {film:,.1f} s of film")
            cap = int(st.get("cap") or 0)
            if cap:
                bits.append(f"probe {int(st.get('probes') or 0)} of {cap}")
                got = int(st.get("hits") or 0)
                bits.append(f"{got} came back" if got
                            else "nothing back yet")
        else:
            bits.append(f"both edges of the damage, "
                        f"{mb:,.1f} MB apart at most")
            edges = len(st.get("edges") or ())
            if edges > 1:
                # each overshoot is a jump that landed in the damage too
                _j = edges - 1
                bits.append(f"{_j} jump{'' if _j == 1 else 's'} landed in it "
                            f"as well")
        got = st.get("got") or ()
        if got:
            back = sum(b - a for a, b in got) * sec / 1e6
            if back >= 0.01:
                bits.append(f"{back:,.2f} MB recovered so far")
        return "   ·   ".join(bits)

    def _frame(self):
        st = self.app.dr.focus_state()
        kind = str(st.get("kind") or "")
        # A NEW SPAN IS A NEW ANIMATION. Not a new `seq`: seq bumps on every
        # read, and restarting the lead-in flashes on each of the sixteen
        # sectors of an edge walk would be a strobe.
        if kind and (kind != self.kind or int(st.get("lo") or 0) != self.lo):
            self.kind = kind
            self.lo = int(st.get("lo") or 0)
            self.hi = int(st.get("hi") or 0)
            self._enter("lead")
        elif kind and self.state in ("zoom_out", "hold"):
            # THE SAME SPAN, RE-OPENED, and it is not a new event.
            #
            # MEASURED 7 Sep, on the report that the probe cursor is not over
            # the ground being probed during the island phase. The island
            # phase opens a bracket, runs its six coarse probes, CLOSES the
            # focus, and then re-opens the same bracket for the fine pass -
            # the one with up to sixty-four probes and every walk-out, which
            # is most of the phase. The logs say the common case is one
            # bracket, so the fine pass almost always re-opens the span the
            # coarse pass just closed.
            #
            # Whether that was visible was a coin toss on the poll: catch the
            # closed moment and the bar zooms out (0.95 s), holds the verdict
            # (2 s), flashes the lead-in (1.5 s) and zooms back in (0.95 s) -
            # five seconds of the fine pass drawn at the wrong scale, with
            # the pulse over whole-disc ground rather than over the probe.
            #
            # Going back in is not an event, so it does not get the lead-in
            # flash: straight to the zoom, which is the shortest honest way
            # back to a bar that describes the span.
            self._enter("zoom_in")
        elif not kind and self.state in ("idle", "lead", "zoom_in",
                                         "focused"):
            # THE ENGINE HAS MOVED ON. Zoom out rather than cutting, so the
            # eye is put back where the whole disc is.
            if self.state == "focused" or self.state == "zoom_in":
                self._enter("zoom_out")
            elif self.state != "idle":
                self._release()
        if kind:
            self.hi = max(self.hi, int(st.get("hi") or 0))

        if self.state == "idle":
            return
        if self.state == "lead":
            return self._draw_lead(st)
        if self.state == "zoom_in":
            return self._draw_zoom(st, out=False)
        if self.state == "focused":
            self._say(self._facts(st))
            self._caption(self._focused_caption(st))
            # THE SPAN, PULSING, ON A BAR OF THE WHOLE FILM. Its own blink
            # rate: the probe pulse inside the zoomed bar is the read in
            # flight and this is a standing "you are here", so sharing a
            # clock would make the two read as one event.
            _a, _b = self._span_frac()
            self._overview(_a, _b,
                           self.NAVY if self._blink(self.PULSE_MS * 2)
                           else self.PROBE)
            return self._draw_focused(st)
        if self.state == "zoom_out":
            return self._draw_zoom(st, out=True)
        if self.state == "hold":
            # ASKED FOR: the verdict stays up for two seconds AFTER the
            # zoom-out finishes, not just during it. HOLD_MS is the pause
            # before the animation may repeat; SETTLED_MS is how long the
            # words stay, and it is the longer of the two.
            _f, _ = self._phase(self.HOLD_MS)
            _held = (self._now() - self.t0) * 1000.0
            self._caption(self._done_caption()
                          if _held < self.SETTLED_MS else "")
            if _f >= 1.0 and _held >= self.SETTLED_MS:
                self._release()
            return

    # -- each state -----------------------------------------------------
    def _at(self, sector, fallback=None):
        """Where a sector sits on the bar, 0..1, through the one mapping.

        None when it is not on the bar at all - ground outside the requested
        extents is not drawn, so there is nowhere to point at.
        """
        try:
            got = self.app.dr.picture_frac(sector)
        except Exception:                                        # noqa: BLE001
            got = None
        return fallback if got is None else got

    def _span_frac(self):
        """Where the span sits on a full-length bar, as 0..1."""
        a = self._at(self.lo, 0.0)
        b = self._at(max(self.hi, self.lo + 1) - 1, None)
        if b is None:
            # The far end is off the picture - a berth that reached past the
            # end of the extent. Show to the end of the bar rather than
            # collapsing the span to nothing.
            b = 1.0
        return (a, b) if b > a else (a, min(1.0, a + 1e-4))

    def _draw_lead(self, _st):
        """A fifth of the bar, centred on the span, flashing."""
        self._say("")
        # THE SAME WORDS FOR EVERY KIND OF DAMAGE. What the flash means is
        # "something is wrong HERE" - which edge, which probe and which pass
        # are all questions for after the zoom, when there is a bar showing
        # the place they are about. The second look is the exception and says
        # so: see _lead_caption.
        self._caption(*self._lead_caption())
        _f, times = self._phase(self.LEAD_MS)
        if times >= self.LEAD_FLASHES * 2:
            return self._enter("zoom_in")
        # THE DISC UNDERNEATH, ON BOTH BEATS. The flash is a fifth of the bar
        # and the bar has not moved yet, so the other four fifths are still
        # the whole-disc picture they were a frame ago - drawing them blank on
        # one beat and in the map's colours on the other made the LEAD-IN a
        # whole-bar strobe too, when what was asked for was a section
        # flashing. It is the zoom's own first frame (vlo 0, vhi 1), so the
        # flash now runs into the zoom instead of cutting to it.
        base = self._over_window(_st, 0.0, 1.0)
        if times % 2:
            self.strip.bar.set_paint(base)
            return
        a, b = self._span_frac()
        mid = (a + b) / 2.0
        half = self.LEAD_SPAN / 2.0
        lo = max(0.0, min(1.0 - self.LEAD_SPAN, mid - half))
        self.strip.bar.set_paint(base + [(lo, lo + self.LEAD_SPAN,
                                          self._lead_colour())])

    def _lerp_window(self, f):
        """The view, interpolated between the whole disc and the span."""
        a, b = self._span_frac()
        f = max(0.0, min(1.0, f))
        return a * f, b * f + (1.0 - f)

    def _draw_zoom(self, st, out):
        # Going in it is still the notice; coming out it is the verdict, and
        # the verdict holds for SETTLED_MS after the bar is back.
        if out:
            self._caption(self._done_caption())
        else:
            self._caption(*self._lead_caption())
        # The overview comes in with the zoom and goes out with it, so the
        # ordinary case is one bar. Asked for by name: "when it's done, remove
        # the second progress bar (the full-length one flashing the section)."
        if out:
            self._overview(None)
        else:
            _a, _b = self._span_frac()
            self._overview(_a, _b, self.NAVY)
        f, _ = self._phase(self.ZOOM_MS)
        if f >= 1.0:
            if out:
                return self._enter("hold")
            return self._enter("focused")
        # eased, so it reads as a camera move rather than a linear slide
        e = f * f * (3.0 - 2.0 * f)
        vlo, vhi = self._lerp_window(e if not out else 1.0 - e)
        self.strip.bar.set_paint(self._over_window(st, vlo, vhi))

    def _over_window(self, st, vlo, vhi):
        """The disc's own colours, plus this event's, over a view window."""
        total = 0
        cells = str(self.app.dr.MAP_PICTURE.get("cells") or "")
        span = max(1e-9, vhi - vlo)
        out = []
        if cells:
            n = len(cells)
            pal = SlimBar.PALETTE
            i = 0
            while i < n:
                j = i
                while j < n and cells[j] == cells[i]:
                    j += 1
                col = pal.get(cells[i])
                if col:
                    a, b = (i / n - vlo) / span, (j / n - vlo) / span
                    if b > 0.0 and a < 1.0:
                        out.append((max(0.0, a), min(1.0, b), col))
                i = j
        for a, b, col in self._event_spans(st, total):
            a, b = (a - vlo) / span, (b - vlo) / span
            if b > 0.0 and a < 1.0:
                out.append((max(0.0, a), min(1.0, b), col))
        return out

    def _event_spans(self, st, _total=0):
        """This event's own marks, in 0..1 of the bar."""
        out = []
        for x in (st.get("edges") or ()):
            a = self._at(x)
            if a is not None:
                out.append((a, a, self.BAD))
        for a, b in (st.get("got") or ()):
            pa, pb = self._at(a), self._at(max(a, b - 1))
            if pa is not None and pb is not None:
                out.append((pa, pb, self.GOT))
        at, n = int(st.get("at") or 0), max(1, int(st.get("n") or 1))
        if at:
            col = {"good": self.PROBE, "bad": self.BAD}.get(
                str(st.get("outcome") or ""))
            if col is None:
                col = self.PROBE if self._blink(self.PULSE_MS) else None
            pa, pb = self._at(at), self._at(at + n - 1)
            if col and pa is not None and pb is not None:
                out.append((pa, pb, col))
        return out

    def _blink(self, ms):
        return int((self._now() * 1000.0) // max(1, ms)) % 2 == 0

    def _draw_focused(self, st):
        """The span, filling the bar. Everything here is a real position."""
        lo, hi = self.lo, max(self.hi, self.lo + 1)
        span = max(1, hi - lo)
        sec = self.app.dr.SectorReader.SECTOR
        self.strip.set_focus_ends(f"{lo * sec / 1e9:.2f} GB",
                                  f"{hi * sec / 1e9:.2f} GB")
        out = []
        # ZOOMED, THE AXIS IS THE SPAN ITSELF, so sector arithmetic is right
        # here where it is wrong at full length: the span is one contiguous
        # stretch of disc by construction - it runs between two edges the
        # walks measured - so there are no extent gaps inside it to skip.
        #
        # THE EDGES, as slivers of red at either end. They are why this span
        # is a span: both of them refused.
        for x in (st.get("edges") or ()):
            if lo <= x < hi:
                out.append(((x - lo) / span, (x - lo + 1) / span, self.BAD))
        # THE DRIVE'S LIES, UNDERNEATH WHAT VERIFIED, so a unit that came good
        # on a later pass covers the amber it left on an earlier one.
        for a, b in (st.get("lied") or ()):
            a, b = max(a, lo), min(b, hi)
            if b > a:
                out.append(((a - lo) / span, (b - lo) / span, self.LIED))
        for a, b in (st.get("got") or ()):
            a, b = max(a, lo), min(b, hi)
            if b > a:
                out.append(((a - lo) / span, (b - lo) / span, self.GOT))
        # ...AND WHAT VERIFIED LAST, because it is the only thing here that is
        # settled. Held ground survives a pass reset; everything else on this
        # bar is about the attempt in hand.
        for a, b in (st.get("held") or ()):
            a, b = max(a, lo), min(b, hi)
            if b > a:
                out.append(((a - lo) / span, (b - lo) / span, self.GOT))
        at, n = int(st.get("at") or 0), max(1, int(st.get("n") or 1))
        if lo <= at < hi:
            a, b = (at - lo) / span, (at - lo + n) / span
            outcome = str(st.get("outcome") or "")
            if outcome == "good":
                # SOLID, and it stays: the ground it found is film now.
                out.append((a, b, self.PROBE))
            elif outcome == "bad":
                # TWICE, and quicker than the pulse. A refusal is an event.
                if self._probe_fail_flash(at):
                    out.append((a, b, self.BAD))
            elif self._blink(self.PULSE_MS):
                out.append((a, b, self.PROBE))
        self.strip.bar.set_paint(out)

    def _probe_fail_flash(self, at):
        """On for the first two beats after a refusal, then off for good."""
        if self._last_probe != at:
            self._last_probe = at
            self._fail_t0 = self._now()
        gone = (self._now() - getattr(self, "_fail_t0", 0.0)) * 1000.0
        beats = int(gone // self.FAIL_MS)
        if beats >= self.FAIL_FLASHES * 2:
            return False
        return beats % 2 == 0


class Card(ttk.LabelFrame):
    """A grooved, titled panel - the plain old group box.

    A titled border says what a group of controls is for without needing a
    colour, a shadow or a heavier font, which suits a utility like this better
    than flat accent-coloured slabs."""

    def __init__(self, parent, title=None, subtitle=None, pad=10):
        super().__init__(parent, text=f" {title} " if title else "",
                         style="Card.TLabelframe", padding=(pad, 6, pad, pad))
        self.columnconfigure(0, weight=1)
        self._sub = None
        r = 0
        if subtitle:
            self._sub = ttk.Label(self, text=subtitle, style="Sub.TLabel",
                                  wraplength=600, justify="left")
            self._sub.grid(row=r, column=0, sticky="we", pady=(0, 6))
            r += 1
            self.bind("<Configure>", self._wrap_sub)
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.grid(row=r, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.rowconfigure(r, weight=1)

    def _wrap_sub(self, e):
        if self._sub is not None:
            try:
                self._sub.configure(wraplength=max(240, e.width - 40))
            except tk.TclError:
                pass


class Tip:
    """Hover text. Every setting gets one showing its config key and meaning."""
    _open = None

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.win = None
        self.after_id = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _e=None):
        self.cancel()
        self.after_id = self.widget.after(450, self.show)

    def cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def show(self):
        if self.win or not self.text:
            return
        if Tip._open is not None:
            try:
                Tip._open.hide()
            except Exception:
                pass
        Tip._open = self
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.attributes("-topmost", True)
        frame = tk.Frame(self.win, background=CLR["border"])
        frame.pack()
        # Themed, not the classic cream: the label draws in CLR["text"], which
        # in the dark palette is near-white and was invisible on a cream ground.
        tk.Label(frame, text=self.text, justify="left", background=CLR["field"],
                 foreground=CLR["text"], wraplength=430,
                 font=(FONT, 9)).pack(padx=1, pady=1, ipadx=7, ipady=5)
        self.win.wm_geometry(f"+{x}+{y}")

    def hide(self, _e=None):
        self.cancel()
        if self.win:
            self.win.destroy()
            self.win = None
        if Tip._open is self:
            Tip._open = None


# Every WinEvent callback ever handed to Windows, kept for the life of the
# process. UnhookWinEvent does not promise that an event already on its way will
# not still arrive, and a trampoline the collector has taken is not a callback
# that does nothing - it is a call into freed memory, which on the first attempt
# here took the interpreter down with a GIL error rather than an exception. They
# are a few dozen bytes each and a strip is opened a handful of times.
_WIN_EVENT_CALLBACKS = []


class MiniMonitor(tk.Toplevel):
    # How tall the bars draw. SOLO_H is the one bar on its own; TOT_H and
    # ITEM_H are the pair, and they add up to less than SOLO_H so that gaining
    # a second bar never costs the strip a point of height.
    SOLO_H = 14
    TOT_H = 10
    ITEM_H = 5

    """A small frameless strip that stays on top of everything, so a rip can be
    watched without the window being in the way.

    Frameless on purpose: a title bar on something this size is most of its
    height, and a strip that reads as a readout rather than a window is easier to
    leave lying on the desktop. The cost is that Windows will not drag, resize,
    snap or close it for us - hence the drag bindings, the grip on the right-hand
    edge, the right-click menu, and the double-click back to the window.

    Two shapes. Floating, it is three lines deep and sits anywhere. Docked, it is
    one line, exactly as tall as the taskbar, and parked inside it - see
    taskbar_bounds for why that is an overlay rather than a real taskbar
    control."""

    PAD = 8
    GRIP = 5                            # width of the resize edge, unscaled
    MIN_W = 150                         # unscaled; below this nothing fits
    MIN_H = 20                          # scaled: one line and its border
    BAR_W = 150                         # the bar never gets less than this
    DOCK_BAR_W = 90                     # ...except docked, where it has a line
                                        # nearly to itself and the numbers on
                                        # the line above need the room more
    DOCK_W = 440                        # docked default: room for a rip's line
    # THE WIDEST EACH DOCKED READOUT EVER GETS, in the format it is written
    # in. Not a real film's numbers: a threshold measured against 0.6 / 32.7
    # GiB is a threshold that moves when the disc does, and the one thing a
    # width tier must not do is change its mind halfway through a rip. There
    # is no 888 GB Blu-ray; this is as wide as the FORMAT can be.
    WIDEST = {"pct": "100.00%", "detail": "888.8 / 888.8 GiB",
              "elapsed": "8:08:08", "eta": "888:88 left",
              "rate": "888.8 MiB/s"}
    # Docked, width is the only room there is to make, so it is the only thing
    # that decides how much is shown - see _dock_edges, which works the four
    # thresholds out from the font instead of carrying them as constants.
    #
    # THEY USED TO BE CONSTANTS - 260, 300, 370 and 470 - and they were the
    # widths at which each field fitted in a layout that has not existed since
    # 3 Sep, when the readouts became stacked PAIRS either side of the bar.
    # Two rows of numbers need about a third less width than one row of four,
    # so every threshold was too high by that much and the strip withheld
    # fields with visible empty space beside them. That is backlog 18(a) and
    # (e), and it was measured rather than argued: see
    # measurements/measure_dock.py, which is also the file the old comment
    # here said to see and which had never been written.
    # THE BUTTON THAT STANDS WHERE THE BACKGROUND NOTE USED TO BE, and what
    # is left of its wording as the strip narrows - widest first.
    #
    # A LADDER OF WHOLE LABELS RATHER THAN AN ELLIPSIS, which is the one place
    # on this strip that does not clip. Everything else here is a readout, and
    # a readout cut short still reads as a fact ("Zack Snyder's Justice Le..."
    # is still the film). A control cut short reads as a rendering fault: "Show
    # background task pro…" says nothing about what pressing it does. So it is
    # drawn whole in whichever wording fits, or not drawn at all.
    BG_BTN_TEXTS = ("Show background task progress",
                    "Show background progress", "Background progress",
                    "Background")
    FG_BTN_TEXTS = ("Show foreground task progress",
                    "Show foreground progress", "Foreground progress",
                    "Foreground")
    BTN_PAD = 6                         # inside the button, each side, unscaled
    BG_H = 8                            # the background job's own bar, detached
    WATCH_MS = 200                      # how often to check it is still on top
    # what to show, poorest first. _auto_tier picks one from the room there is,
    # and general.mini_monitor_detail can pin it instead
    TIERS = ("minimal", "compact", "normal", "full", "detailed", "log")
    LOG_LINES = 200                     # kept in the tail; a strip is not an archive

    def __init__(self, app, docked=None):
        super().__init__(app)
        self.app = app
        self.withdraw()                 # shape and place it before it is seen
        self.wm_overrideredirect(True)
        self.set_opacity()
        self._drag = self._sizing = None
        self._hidden_with_taskbar = False
        self._raises = 0                # consecutive checks that had to lift it
        self._owned = False             # is the taskbar keeping it on top for us
        self._eclipsed = False          # taken down: something owns the display
        self._hwnd_id = None            # cached; the hooks cannot ask Tk for it
        self._proto = None              # the shape of a WinEvent callback
        self._hook = self._hook_cb = None           # the window in front changed
        self._front_hook = self._front_cb = None    # ...or changed size
        self._look = False              # a hook says the display may have changed
        self._refront = False           # ...and by something else, so re-aim
        self._shape = None              # (rows, tier) it is currently laid out for
        self._merge_extra = False       # no line spare: fold it into the one above
        # Whether the docked layout is drawing the readouts as stacked pairs,
        # in which case `extra` is not one of them - see the note in
        # _dock_layout. False for every floating shape.
        self._stacked = False
        self._log_row = None            # which row the log took, if any
        self._flash_left = 0
        self._flash_colour = CLR["ok"]
        # The zoomed view's endpoints, and the stage without them on it.
        self._focus_ends = ("", "")
        self._plain_stage = ""
        self._legend_on = None
        self._focus_facts = None
        self._fg = {}                   # foregrounds to put back after a blink
        # WHICH TASK THE STRIP IS DESCRIBING. False is the rip in the drive;
        # True is the job behind it, and then the foreground's own progress
        # reports are refused - see show() and idle(). The strip owns this
        # rather than the window because the strip is what the two buttons are
        # on, and a rebuilt strip starting on the foreground is the right
        # answer anyway.
        self._bg_view = False
        self._bg_on = False             # is there background work to describe
        self._btn_text = ""             # what the view button currently says
        self._done = False              # bar green and full: this disc is done
        self._stalled = False           # solid yellow: nothing is moving
        # Its own geometry, kept rather than read back with winfo_*: those report
        # the last mapped size, so during a drag they lag a frame behind and a
        # resize computed from a stale width creeps. Named _gw and not _w because
        # tkinter keeps every widget's Tcl path name in self._w - assigning an
        # int there breaks the creation of any child widget.
        self._gw = self._gh = self._gx = self._gy = 0
        self.docked = self._wants_dock() if docked is None else docked
        sc = app.sc

        self.edge = edge = tk.Frame(self, background=CLR["border"])
        edge.pack(fill="both", expand=True)
        self.body = body = tk.Frame(edge, background=CLR["card"])
        body.pack(fill="both", expand=True, padx=1, pady=1)

        self.inner = tk.Frame(body, background=CLR["card"])
        self.inner.pack(side="left", fill="both", expand=True)

        # Three grips, because the strip is resized in both directions now: the
        # right edge for width, the bottom edge for height, the corner for both.
        # All three are placed over the edges rather than packed beside the
        # contents, because a packed one takes its width out of the layout - and
        # five points of height is a great deal to spend inside a taskbar that
        # only has forty-four. Created after the contents so they sit on top of
        # them: among siblings, later means nearer the front.
        self.grip = tk.Frame(body, background=CLR["border"],
                             cursor="sb_h_double_arrow")
        self.grip_b = tk.Frame(body, background=CLR["border"],
                               cursor="sb_v_double_arrow")
        self.grip_c = tk.Frame(body, background=CLR["edge_hi"],
                               cursor="size_nw_se")
        self._place_grips()
        for w, axes in ((self.grip, "w"), (self.grip_b, "h"),
                        (self.grip_c, "wh")):
            w.bind("<Button-1>", lambda e, a=axes: self._grab_size(e, a))
            w.bind("<B1-Motion>", self._resize)
            w.bind("<ButtonRelease-1>", self._drop_size)
        grid = tk.Frame(self.inner, background=CLR["card"])
        grid.pack(fill="both", expand=True)
        self.grid_host = grid
        # A KEY FOR THE COLOURS, and what the zoom is doing. Detached only:
        # docked the strip shows six things and these would be a seventh.
        #
        # BUILT HERE, NOT WITH THE REST OF THE STATE ABOVE, because both are
        # children of grid_host and grid_host does not exist until this line.
        # Constructing them earlier crashed the whole window on startup for
        # anyone with the strip switched on, which is the default - and the
        # suite never noticed because it never builds an App. It does now.
        # THE WHOLE FILM, under the zoomed bar - see set_zoom_overview. Same
        # place in the build order and for the same reason: it is a child of
        # grid_host, which does not exist until a few lines above this.
        self.whole_bar = SlimBar(grid, height=int(6 * self.app.sc))
        # THE DRIVE-IS-FREE BANNER - see set_drive_free. Bold and green, and
        # it takes the whole top row when it is up.
        self.free_lbl = tk.Label(grid, text="", anchor="w",
                                 background=CLR["card"],
                                 foreground=CLR["ok"])
        self.legend_row = tk.Frame(grid, bd=0, highlightthickness=0,
                                   background=CLR["card"])
        for _name, _col in self.LEGEND:
            sw = tk.Frame(self.legend_row,
                          # NO COLOUR OF ITS OWN FOR SKIPPED, and the absence
                          # is the statement: it is the track's own grey
                          # because nothing in it was measured.
                          background=_col or CLR["track"],
                          width=int(9 * self.app.sc),
                          height=int(6 * self.app.sc), bd=0,
                          highlightthickness=0)
            sw.pack(side="left", padx=(0, 3))
            sw.pack_propagate(False)
            tk.Label(self.legend_row, text=_name, background=CLR["card"],
                     foreground=CLR["faint"], font=self.app.f_small
                     ).pack(side="left", padx=(0, 10))
        self.facts_lbl = tk.Label(grid, text="", anchor="w",
                                  background=CLR["card"],
                                  foreground=CLR["bg_job"],
                                  font=self.app.f_small)
        # THE BACKGROUND JOB'S OWN BAR AND ITS LINE, detached only, and
        # directly under the bar it sits beneath - "same as the app itself",
        # asked for 9 Sep. The window has had this block since 31 Aug (see
        # bg_area); the strip had a parenthetical, because a taskbar row has
        # no second bar's worth of height to give. A strip dragged out onto
        # the desktop does, and this is what the height is for.
        #
        # Its own bar and not a second series on the rip's: BG_STATE exists
        # precisely because the two are different films and neither may move
        # the other's readout. See BackgroundJobs.
        self.bg_head = tk.Label(grid, text="", anchor="w",
                                background=CLR["card"],
                                foreground=CLR["bg_job"],
                                font=self.app.f_small, bd=0, padx=0, pady=0,
                                highlightthickness=0)
        self.bg_bar = SlimBar(grid, self.BG_H * sc)
        self.bg_bar.tint(CLR["bg_job"])
        self._bg_head_text = ""         # what it says, so a tick can skip
        self._bg_bar_frac = None        # ...and so can the canvas
        # The bar zooming in on a probe. Its own clock; see FocusAnimator.
        self.anim = FocusAnimator(self)
        self.anim.start()

        # No border, no internal padding, no highlight ring on any of them: a
        # tk.Label carries several points of chrome by default, and two lines of
        # that is most of the difference between fitting inside a taskbar and
        # having the bottom line clipped off. Spacing is the grid's job here.
        bare = dict(bd=0, padx=0, pady=0, highlightthickness=0,
                    background=CLR["card"])
        self.dot = tk.Label(grid, text="●", foreground=CLR["faint"],
                            font=app.f_base, **bare)
        # width=1 so the disc's name asks for almost nothing and is stretched by
        # its column instead. Asking for its full length made it the widest
        # thing on the strip, and the numbers beside it were pushed off the edge
        # rather than the name giving up the room - which is the wrong way
        # round: a name can be read half-clipped, a byte count cannot.
        # One cell for both, because they are one phrase - "Rogue One -
        # decrypting data" - and the strip's own grid cannot put them next to
        # each other: the column they would share has to stretch for the bar
        # below it, so a title in it and a stage in the next one end up at
        # opposite ends of the strip.
        self.headline = tk.Frame(grid, bd=0, highlightthickness=0,
                                 background=CLR["card"])
        # THE WAY OVER TO THE OTHER TASK, where a sentence about it used to be.
        #
        # "Waiting for the next disc" is true and it is not the whole truth: a
        # film can be being encoded behind it, which is the thing somebody
        # glancing at the strip actually wants to know - is this machine busy,
        # and how far along. That used to be a footnote on the title's line,
        # and a footnote is exactly as much of it as the strip had room for:
        # "searching for the quality of How To Train Y" is a screenshot, cut
        # at the width of a taskbar, of the two facts anybody wanted - which
        # film, and how far - with both of them missing.
        #
        # So the room goes to a button instead, and pressing it turns the
        # whole strip over to answering the question. Asked for 9 Sep.
        #
        # A LABEL DRESSED AS A BUTTON, not a ttk.Button: docked, the strip is
        # exactly as tall as the taskbar and every point of its height is
        # spoken for - a themed button carries several points of chrome above
        # and below its text, which is the difference between fitting inside a
        # taskbar and having the bottom line clipped off. Same reason every
        # readout here is a bare tk.Label. A border, a hand cursor and the
        # button face say what it is.
        #
        # Violet because that is already what this app means by "the other
        # process": the background job's lines in the log, the background
        # card's percentage in the window, and the note this replaces.
        self.view_btn = tk.Label(
            self.headline, text="", anchor="w", bd=1, relief="solid",
            padx=int(self.BTN_PAD * sc), pady=0, highlightthickness=0,
            foreground=CLR["bg_job"], background=CLR["btn"],
            font=app.f_small, cursor="hand2")
        self.view_btn.bind("<Button-1>", self._tap_view)
        self.view_btn.bind("<Button-3>", self._popup)
        # LIGHTS UP UNDER THE POINTER, which is the other half of looking like
        # a button: a bordered label on a strip where everything is a drag
        # handle otherwise gives no sign that this one does something. The
        # right-click menu still opens on it, because the strip is frameless
        # and that menu is the only way to dock it, hide it or resize it.
        self.view_btn.bind("<Enter>",
                           lambda _e: self._btn_face(CLR["btn_act"]))
        self.view_btn.bind("<Leave>", lambda _e: self._btn_face(CLR["btn"]))
        self.title_lbl = tk.Label(self.headline, text="No rip in progress",
                                  anchor="w", foreground=CLR["text"],
                                  font=app.f_bold, **bare)
        self.title_lbl.pack(side="left")
        # length is only the size it *asks* for, and it is stretched by its
        # column in every layout here - so it asks for very little, and the
        # column's floor decides how short the bar may actually get. Asking for
        # 120 held the column open at that width and pushed the byte counts off
        # the end of a narrow strip.
        # Two bars in one grid cell, so every layout below goes on placing one
        # thing where the bar goes and does not need to know there are two of
        # them. The whole-run one is on top and is the thicker: on a batch it is
        # the number being waited on, and the per-item one is a detail of it -
        # which is the opposite of how much room each would get if they were
        # equals. The per-item bar keeps its normal weight when it is the only
        # one there.
        # Each bar sits in a frame of its own whose height is set explicitly,
        # because a progressbar's height cannot be. Measured: ttk's -thickness
        # is accepted and then ignored by every theme on this machine - vista
        # reports 22 points for thickness 3, 8, 15 and 30 alike, clam 18 for all
        # four - so the only thing that actually decides how tall a bar draws is
        # the box it is packed into with propagation off. Nothing is constrained
        # until there are two of them, so a strip with one bar is pixel for
        # pixel the strip it always was.
        # The two bars and their two percentages in one cell, gridded against
        # each other: a number that belongs to a bar has to be on that bar's
        # line, and putting them in the strip's own grid would have left the
        # layouts deciding that for them one row at a time.
        self.bars = tk.Frame(grid, bd=0, highlightthickness=0,
                             background=CLR["card"])
        self.bars.columnconfigure(0, weight=1)
        self.total_bar = SlimBar(self.bars, self.TOT_H * sc)
        self.bar = SlimBar(self.bars, self.SOLO_H * sc)
        # BOLD, like the strip's own percentage field: on a two-percentage
        # strip this is the figure that field stands down for - see
        # _paint_pcts - so bolding one and not the other would mean the
        # emphasis came and went with the room rather than marking the same
        # kind of fact. The per-item figure below stays quiet; it is a detail
        # of this one.
        self.total_pct = tk.Label(self.bars, text="", anchor="e",
                                  foreground=CLR["text"], font=app.f_numb,
                                  **bare)
        self.item_pct = tk.Label(self.bars, text="", anchor="e",
                                 foreground=CLR["muted"], font=app.f_small,
                                 **bare)
        # A ROW FOR THE BAR AND THE TWO POSITIONS IT RUNS BETWEEN. Every
        # place that used to grid `self.bar` grids this instead; the bar keeps
        # its own height and its own set_* calls.
        self.barrow = tk.Frame(self.bars, bd=0, highlightthickness=0,
                               background=CLR["card"])
        self.barrow.columnconfigure(1, weight=1)
        self.dock_lo = tk.Label(self.barrow, text="", anchor="w",
                                foreground=CLR["bg_job"], font=app.f_small,
                                **bare)
        self.dock_hi = tk.Label(self.barrow, text="", anchor="e",
                                foreground=CLR["bg_job"], font=app.f_small,
                                **bare)
        self.bar.grid_forget()
        self.bar = SlimBar(self.barrow, self.SOLO_H * sc)
        self.bar.grid(row=0, column=1, sticky="we")
        self.barrow.grid(row=0, column=0, columnspan=2, sticky="we")
        self._has_total = False
        self._two_pcts = False
        # BOLD - see App.f_numb. This is the field that survives every width.
        self.pct = tk.Label(grid, text="", foreground=CLR["text"],
                            font=app.f_numb, anchor="e", **bare)
        # Docked, the stage is its own field rather than part of the disc's
        # name: the name is the one thing on the strip that can be clipped
        # without much loss, and joining the two made clipping the name clip the
        # stage off the end of it instead.
        # Lighter and smaller than the name it follows: it is what is
        # happening, not what it is happening to.
        self.stage_lbl = tk.Label(self.headline, text="", anchor="w",
                                  foreground=CLR["muted"], font=app.f_small,
                                  **bare)
        self.detail = tk.Label(grid, text="", anchor="e",
                               foreground=CLR["muted"], font=app.f_small, **bare)
        # Stands in the bar's own cell while a rip is stalled. A bar that has
        # not moved for four minutes is worse than no bar: it looks like a rip
        # in progress, which is exactly what it is not.
        self.stall_lbl = tk.Label(grid, text="", anchor="w",
                                  foreground=CLR["bg"], font=app.f_bold, **bare)
        # only ever filled in at the larger sizes, where there is room for them
        # THE DOCKED STRIP'S OWN READOUTS, one label each so the two rows
        # can stack them in pairs. `extra` still exists and still carries the
        # joined sentence for the floating shapes, which have a line to spend
        # on it; docked, a single label could not be in two rows at once.
        #
        # ASKED FOR 3 Sep: "bordering the progress bar, have the GiB fraction
        # stacked on top of percent; to the right of that, have time elapsed
        # stacked on estimated time remaining, and to the right of that have
        # transfer speed on the bottom row." All four in f_small, "the same
        # text format as the GiB fraction is currently".
        # `**bare` LIKE EVERY OTHER LABEL ON THE STRIP, and these three were
        # the exception. A default tk.Label carries a point of internal padx
        # and a point of border on each side, so each of these asked for about
        # seven points more than its text - and _nums_width, which measures
        # them in the font, could not see that. Three labels' worth of chrome
        # is 20 of the 20 points by which the phrase's column was
        # over-estimated (measured 5 Sep, measurements/measure_dock.py), which
        # is backlog 18(e): the strip did not know how much space it had.
        self.dock_elapsed = tk.Label(grid, text="", anchor="e",
                                     foreground=CLR["faint"], **bare)
        self.dock_eta = tk.Label(grid, text="", anchor="e",
                                 foreground=CLR["faint"], **bare)
        self.dock_rate = tk.Label(grid, text="", anchor="e",
                                  foreground=CLR["faint"], **bare)
        self.extra = tk.Label(grid, text="", anchor="w",
                              foreground=CLR["faint"], font=app.f_small, **bare)
        self.info_lbl = tk.Label(grid, text="", anchor="w",
                                 foreground=CLR["faint"], font=app.f_small,
                                 **bare)
        # the activity log's tail. Disabled and borderless: it is a readout, not
        # somewhere to type, and it inherits the drag bindings like everything
        # else so the strip still moves when you take hold of it here
        # A hairline above the log. The lines above it are this rip's
        # numbers and the log is the window's own history - two different kinds
        # of thing that had been reading as one list of grey text.
        # EVERY STAGE OF THE RIP, with the one in hand marked. "step 4 of 6"
        # says how much is left and not what is coming, and a strip dragged
        # wide has the room to answer that - so the room answers it. The list
        # comes from StagePlan by way of JOB["step_names"]: it is the plan the
        # rip is actually following, not a fixed sentence, so a stage that is
        # switched off is not in it at all.
        #
        # A frame of labels rather than one string, because the current stage
        # is marked with a background and a tk.Text tag would be the same code
        # with a widget nobody can measure the width of in between.
        self.stages = tk.Frame(grid, bd=0, highlightthickness=0,
                               background=CLR["card"])
        self.stage_chips = []           # the labels, reused between repaints
        self._stage_list = ()           # (names, current) last drawn
        self.rule = tk.Frame(grid, height=max(1, int(sc)), bd=0,
                             highlightthickness=0, background=CLR["border"])
        # WRAPPED, NOT CLIPPED. Asked for 31 Aug, from a screenshot of the
        # strip cutting the salvage's own sentences in half: "Recorded as
        # unreadable; movin", "Marking the next 32 MB as SKIPPED - not bad,".
        # The end of those lines is the part worth reading. A wrapped line
        # costs a row of a short log, which is a better trade than losing the
        # verb - and the strip that shows the log at all is the wide one.
        self.log = tk.Text(grid, height=2, wrap="word", bd=0, padx=0, pady=0,
                           highlightthickness=0, background=CLR["card"],
                           foreground=CLR["muted"], font=app.f_small,
                           state="disabled", cursor="", insertwidth=0,
                           spacing1=0, spacing3=0)

        self.menu = tk.Menu(self, tearoff=0, background=CLR["card"],
                            foreground=CLR["text"],
                            activebackground=CLR["btn_act"],
                            activeforeground=CLR["text"], activeborderwidth=0,
                            borderwidth=1, relief="solid", font=app.f_base)
        self.menu.add_command(label="Show the window",
                              command=self._act(app.uncollapse))
        self.menu.add_separator()
        self.dock_var = tk.BooleanVar(value=self.docked)
        self.menu.add_checkbutton(label="Sit in the taskbar",
                                  variable=self.dock_var,
                                  command=self._act(app.toggle_mini_dock))
        # AT THE STRIP, because that is the thing it is about - and the strip
        # is frameless, so there is no title bar to right-click for it.
        self.top_var = tk.BooleanVar(
            value=bool(app.dr.sget(app.settings,
                                   "general.mini_monitor_top", True)))
        self.menu.add_checkbutton(label="Always on top",
                                  variable=self.top_var,
                                  command=self._toggle_top)
        # how much it shows, at the thing it applies to rather than three tabs
        # away in the settings
        self.detail_menu = detail = tk.Menu(
            self.menu, tearoff=0, background=CLR["card"],
            foreground=CLR["text"], activebackground=CLR["btn_act"],
            activeforeground=CLR["text"], activeborderwidth=0,
            borderwidth=1, relief="solid", font=app.f_base)
        self._fill_detail_menu()
        self.menu.add_cascade(label="Show", menu=detail)
        self.menu.add_command(label="Reset size and position",
                              command=self._act(app.reset_mini_geometry))
        self.menu.add_separator()
        self.menu.add_command(label="Hide this strip", command=app.hide_mini)

        for w in self._every_widget():
            self._bind_drag(w)

        self._relayout()
        self.idle()
        self._place()
        self._shape = (self._rows_that_fit(), self._tier())
        self._relayout()                # now that its real size is known
        self.idle()
        # Windows can still take it off screen for reasons of its own; catch that
        # the moment it happens rather than a second later on the clock tick.
        self.bind("<Unmap>", self._on_unmap)
        self.deiconify()
        self.apply_top()
        self._hwnd_id = self.hwnd()
        self._hook_front()
        self._glance()
        self._watch()

    def _bind_drag(self, w):
        """Everything on the strip is a handle: it is frameless, so there is no
        title bar to take hold of. Called for the stage chips too, which are
        created after __init__ has walked the tree."""
        w.bind("<Button-1>", self._grab_or_dismiss)
        w.bind("<B1-Motion>", self._move)
        w.bind("<ButtonRelease-1>", self._drop)
        w.bind("<Double-Button-1>", lambda _e: self.app.uncollapse())
        w.bind("<Button-3>", self._popup)

    def _grab_or_dismiss(self, event):
        """A click takes the banner down, or takes hold of the strip.

        The drive-is-free banner is dismissed by clicking the strip, and the
        strip is dragged by clicking the strip - so the first click after the
        banner appears is the banner's and every one after it is a drag. It
        still starts a drag as well, because a click that both dismissed and
        moved the strip would be a click that moved the strip by accident.
        """
        if self.dismiss_drive_free():
            return None
        return self._grab(event)

    def hwnd(self):
        """The window Windows actually manages for this strip.

        Tk wraps every toplevel in a window of its own and hands winfo_id() the
        *child* that lives inside it. Everything Win32 is asked about a strip has
        to go through the wrapper instead, and getting that wrong is not a
        near-miss: on a child window GWLP_HWNDPARENT is the parent rather than
        the owner, so clearing it - which is what used to happen here, in the
        name of stopping the strip being hidden along with the main window -
        reparented the strip's contents to the desktop. They stuck at the top
        left corner of the screen, immovable, while wm_geometry went on
        faithfully moving the real window, by then an empty rectangle, into the
        taskbar.

        There was nothing to detach in the first place: an override-redirect
        toplevel's wrapper is already unowned. GetAncestor(GA_ROOT) is the same
        route TaskbarProgress takes to find the button it draws on."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.GetAncestor.restype = wintypes.HWND
            u32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
            return u32.GetAncestor(wintypes.HWND(int(self.winfo_id())), 2)
        except Exception:
            return None

    def _on_unmap(self, _e=None):
        if self._hidden_with_taskbar or self._eclipsed or self.app._closing:
            return              # down with the taskbar or a full-screen app, or
                                # the app is shutting: all three meant to be
        try:
            if self.winfo_exists():
                self.after_idle(self.reassert)
        except tk.TclError:
            pass                # on its way out; nothing to put back

    def _toggle_top(self):
        want = bool(self.top_var.get())
        self.app._set_and_save("general.mini_monitor_top", want)
        self.apply_top()
        if self.docked and not want:
            # Said rather than silently ignored: the box is ticked off and
            # nothing changes, which looks broken.
            self.app._status("The docked strip is always on top - it sits "
                             "inside the taskbar, which is.")

    def _fill_detail_menu(self):
        """The levels this shape actually has, described as what they show.

        Docked it stops at the time remaining, because that is where the docked
        strip stops - offering three more levels that do nothing would be a menu
        lying about the thing it is attached to."""
        levels = [("auto", "As much as it has room for"),
                  ("minimal", "Disc, stage and bar only"),
                  ("compact", "and the percentage"),
                  ("normal", "and the byte counts")]
        levels += [("full", "and the time remaining")] if self.docked else [
            ("full", "and the elapsed time and rate"),
            ("detailed", "and what the disc is"),
            ("log", "and the activity log")]
        self.detail_menu.delete(0, "end")
        for level, label in levels:
            self.detail_menu.add_radiobutton(
                label=label, value=level, variable=self.app.detail_var,
                command=lambda v=level: self.app.set_mini_detail(v))

    def _wants_top(self):
        """Always on top, or an ordinary window anything may cover?

        DOCKED IS NOT A CHOICE, and that is not a limitation being papered
        over: the docked strip is an overlay drawn inside the taskbar, which is
        itself always on top - see taskbar_bounds - so a docked strip that gave
        up its z-order would be behind the thing it is sitting in, which is to
        say invisible. The setting is offered for the floating shape, which is
        the shape that gets in the way.
        """
        if self.docked:
            return True
        return bool(self.app.dr.sget(self.app.settings,
                                     "general.mini_monitor_top", True))

    def apply_top(self):
        """Put the z-order policy into effect. Returns whether it is on top.

        Three mechanisms, and all three have to agree or the setting does
        nothing: the -topmost attribute, the taskbar OWNERSHIP that own_taskbar
        arranges (Windows keeps an owned window above its owner, and the
        taskbar is topmost, so ownership alone would defeat this), and the
        healing in reassert.
        """
        on = self._wants_top()
        try:
            self.attributes("-topmost", bool(on))
        except tk.TclError:
            pass
        if on:
            self.own_taskbar()
        else:
            self.release_taskbar()
            self._raises = 0
        return on

    def release_taskbar(self):
        """Give up the taskbar as owner, so the strip can be covered."""
        self._owned = False
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            hwnd_id = self.hwnd()
            if not hwnd_id:
                return False
            setter = (getattr(u32, "SetWindowLongPtrW", None)
                      or u32.SetWindowLongW)
            setter.restype = ctypes.c_longlong
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
            setter(wintypes.HWND(hwnd_id), -8, 0)   # GWLP_HWNDPARENT
        except Exception:
            pass
        return True

    def _wants_dock(self):
        return str(self.app.dr.sget(self.app.settings,
                                    "general.mini_monitor_dock",
                                    "float")).lower() == "taskbar"

    def _every_widget(self, w=None):
        w = self if w is None else w
        # The grips resize rather than drag, and the view button is pressed:
        # _bind_drag would give it <Button-1> for a drag and a double-click
        # that opens the window, and a control that moves the strip when you
        # aim at it is a control nobody can hit.
        if w in (self.grip, self.grip_b, self.grip_c,
                 getattr(self, "view_btn", None)):
            return []
        out = [w]
        for c in w.winfo_children():
            if not isinstance(c, (ttk.Progressbar, tk.Menu)):
                out += self._every_widget(c)
        return out

    def _place_grips(self):
        """Only the edges that can actually be dragged.

        Docked, the height belongs to the taskbar and is not ours to change, so
        the bottom and corner grips are taken away rather than left sitting
        there to be dragged at with nothing happening."""
        g = max(3, int(self.GRIP * self.app.sc))
        self.grip.place(relx=1.0, rely=0.0, anchor="ne", width=g, relheight=1.0)
        if self.docked:
            self.grip_b.place_forget()
            self.grip_c.place_forget()
        else:
            self.grip_b.place(relx=0.0, rely=1.0, anchor="sw", height=g,
                              relwidth=1.0)
            self.grip_c.place(relx=1.0, rely=1.0, anchor="se", width=g * 2,
                              height=g * 2)

    def set_opacity(self):
        """How solid the strip is, as a percentage of the setting.

        Clamped well clear of invisible: a strip nobody can see is a strip
        nobody can right-click to put back, and it is always on top of
        something."""
        try:
            pct = int(self.app.dr.sget(self.app.settings,
                                       "general.mini_monitor_opacity", 94) or 94)
        except (TypeError, ValueError):
            pct = 94
        self.attributes("-alpha", max(20, min(100, pct)) / 100.0)

    # -- how much to show -------------------------------------------------
    def _line(self):
        """The height of one line of the strip, bar included."""
        sc = self.app.sc
        return max(self.app.f_bold.metrics("linespace"),
                   int(12 * sc)) + max(1, int(sc))

    def _rows_that_fit(self):
        """How many lines the height it has been dragged to will take."""
        h = self._gh or self.winfo_reqheight()
        rows = max(1, min(24, (h - 2) // self._line()))
        # Docked it is two lines whatever the taskbar's height, because two is
        # the shape the fields are dealt into. A third line inside a taskbar
        # would have to be filled with something, and there is nothing else the
        # docked strip is allowed to show.
        return min(2, rows) if self.docked else rows

    def _dock_edges(self):
        """The four widths at which the docked strip gains a field, measured.

        In the order they arrive: the percentage, the byte counts, the time
        remaining, then the elapsed time and the read rate. Each is the
        previous one plus a gap and the widest that readout's column can be,
        starting from the bar at its own floor - which is the arithmetic
        _dock_layout actually performs, written once instead of estimated as
        four constants that then have to be maintained by hand. See WIDEST for
        why the strings are formats rather than a film's numbers, and _grip_w
        for why the resize edge is in the base.

        The pairs share a column, so the column is the wider of the two: the
        byte counts over the percentage, the elapsed time over the time
        remaining. The rate has nothing above it and is its own width.
        """
        sc = self.app.sc
        pad, gap = int(self.PAD * sc), int(8 * sc)
        f_small, f_num = self.app.f_small, self.app.f_numb
        # the border, the pad in front of the bar, the bar's floor, the
        # right-hand margin and the grip drawn over it
        base = 2 + pad + int(self.DOCK_BAR_W * sc) + pad + self._grip_w()
        pct = f_num.measure(self.WIDEST["pct"])
        first = max(pct, f_small.measure(self.WIDEST["detail"]))
        clocks = max(f_small.measure(self.WIDEST["elapsed"]),
                     f_small.measure(self.WIDEST["eta"]))
        rate = f_small.measure(self.WIDEST["rate"])
        e_pct = base + gap + pct
        e_detail = base + gap + first
        e_eta = e_detail + gap + clocks
        return e_pct, e_detail, e_eta, e_eta + gap + rate

    def _auto_tier(self):
        """More room, more information.

        Width decides how much can sit on a line and height decides how many
        lines there are, so both are asked. The thresholds are the widths at
        which each field stops fitting beside the ones before it, rounded to
        something a person dragging an edge can land on.

        The order they arrive in is the order they are wanted in: how far along,
        then how long it has taken, then what the disc actually is, and last the
        log - which is the one that needs real room rather than one more line,
        and the one you go looking for when something has gone wrong."""
        sc, w = self.app.sc, (self._gw or self.winfo_reqwidth())
        rows = self._rows_that_fit()
        if self.docked:
            # Docked, the height is the taskbar's and cannot be dragged, so
            # width is the only room there is to make - and it is therefore the
            # only thing deciding how much is shown. The order is fixed: the
            # disc, the stage and the bar always, then the percentage, then the
            # byte counts, then the time remaining. Each threshold is the width
            # at which that field fits beside the ones already there without
            # the bar being squeezed below its floor - worked out from the
            # font, not carried as a number; see _dock_edges.
            for edge, tier in zip(self._dock_edges(),
                                  ("minimal", "compact", "normal", "full")):
                if w < edge:
                    return tier
            return "detailed"
        # HEIGHT DECIDES ONCE THERE ARE LINES TO FILL, and it did not used to.
        #
        # OBSERVED 31 Aug from a screenshot: a strip dragged to 470 px tall and
        # about 420 wide showed the title, the bar and one row of numbers, with
        # two thirds of it empty. The ladder gated everything above "normal"
        # behind `wide = w >= 480`, so no amount of height unlocked anything -
        # and 480 was the width at which those fields fitted on ONE LINE.
        #
        # They wrap now. A field too long for the width costs a second line
        # instead of being withheld, which is what the height was there for.
        # So width only decides the one- and two-line shapes, where there is no
        # second line to wrap onto, and rows decide the rest.
        if rows <= 1:
            return ("minimal" if w < int(300 * sc)
                    else "compact" if w < int(560 * sc) else "normal")
        if rows == 2:
            return "compact" if w < int(300 * sc) else "normal"
        if w < int(240 * sc):
            # Below the bar's own floor plus a number beside it. Wrapping
            # cannot help here; there is nothing to wrap onto.
            return "compact"
        if rows >= 6:
            return "log"
        if rows >= 5:
            return "detailed"
        if rows >= 4:
            return "full"
        return "normal"

    def _tier(self):
        want = str(self.app.dr.sget(self.app.settings,
                                    "general.mini_monitor_detail",
                                    "auto")).lower()
        return want if want in self.TIERS else self._auto_tier()

    def _shows(self):
        """Which of the fields this size and setting call for."""
        rank = self.TIERS.index(self._tier())
        rows = self._rows_that_fit()
        if self.docked:
            # Six things, and never a seventh. The elapsed time, the rate, the
            # drive, what the disc is and the log all want a line of their own,
            # and a taskbar has no line to give - so docked they are not on the
            # list at all, however the level was arrived at.
            return {"bar": True, "pct": rank >= 1, "detail": rank >= 2,
                    "extra": rank >= 3,
                    # The elapsed and the rate, folded into `extra` beside the
                    # time remaining rather than given a field of their own -
                    # a taskbar has two lines and both are dealt out already.
                    "clock": rank >= 4,
                    "info": False, "log": False,
                    # A taskbar has one spare line and it is spoken for. The
                    # stage list is the widest thing the strip can draw, and
                    # the background job's bar would be a third and a fourth.
                    # Docked it gets the button instead - see set_bg_button.
                    "stages": False, "bg": False}
        out = {"bar": True,
               "pct": rank >= 1,
               "detail": rank >= 2,
               "extra": rank >= 3,
               # These two need a line of their own, so however the level was
               # arrived at they only appear when there is one spare. Docked is
               # the exception for the disc line: there it shares the title's
               # line, at the right-hand end, because there is no spare line to
               # be had inside a taskbar and width is the thing being grown.
               "clock": True,       # floating, it has a line of its own
               "info": rank >= 4 and (self.docked or rows >= 5),
               "log": rank >= 5 and rows >= 6,
               # ROOM IN BOTH DIRECTIONS, and the width test is made against
               # the list actually in hand rather than a guessed threshold -
               # see _stages_fit. A stage list with half its stages cut off
               # is worse than the "step 4 of 6" it sits under.
               "stages": rank >= 3 and rows >= 4 and self._stages_fit()}
        out["bg"] = self._bg_on and self._bg_rows_spare(out, rows)
        return out

    def _bg_rows_spare(self, show, rows):
        """Are two rows going spare for the background job's own block?

        MEASURED AGAINST WHAT THIS SHAPE ALREADY WANTS, not a row count picked
        in advance, for the same reason _stages_fit measures the stage list in
        the font: what fits depends on what else the tier has asked for, and a
        threshold written down here would be a copy of the layout's own
        arithmetic that stops being true the next time the layout changes.

        What the rest of the shape wants: the title's line and the bar, one
        line each for the stage list and the three readouts that are switched
        on, and - for the log - the hairline above it and at least one line of
        it. Anything left over after that is spare, and two of it is a line
        and a bar.
        """
        want = 2                        # the title's line, and the bar
        want += sum(1 for k in ("stages", "detail", "extra", "info")
                    if show.get(k))
        if show.get("log"):
            want += 2                   # the rule, and a line of log
        return rows - want >= 2

    def _stages_fit(self):
        """Is the strip wide enough for the whole list, at its own font?

        Measured with the font, not estimated from a character count: the
        stages are words of very different lengths ("reading the disc",
        "checksumming") and the plan's length changes with the settings, so
        there is no threshold to hard-code. Nothing to draw counts as not
        fitting, which keeps an empty row off the strip.
        """
        names = self._stage_list[0] if self._stage_list else ()
        if not names:
            return False
        f = self.app.f_small
        pad = int(self.PAD * self.app.sc) * 2
        gap = int(10 * self.app.sc)
        try:
            want = sum(f.measure(str(x)) + gap for x in names) + pad
        except tk.TclError:
            return False
        return (self._gw or self.winfo_reqwidth()) >= want

    def set_stages(self, names, current=""):
        """The plan and the stage in hand. Re-laid out only when it changes.

        Called from the progress sink on every tick, so it does nothing at all
        on the overwhelming majority of them.
        """
        names = tuple(str(x) for x in (names or ()) if str(x))
        want = (names, str(current or ""))
        if want == self._stage_list:
            return False
        was_fit = self._shows().get("stages")
        self._stage_list = want
        self._paint_stages()
        # The plan's own width decides whether it can be shown, so a new plan
        # can turn the row on or off - which is a relayout, not a repaint.
        if self._shows().get("stages") != was_fit:
            self._relayout()
            self.reshape(force=True)
        return True

    def _paint_stages(self):
        """Draw the chips. Grey box on the one in hand, which is what was asked
        for; the ones already done are a shade brighter than the ones to come,
        because "where am I" and "what is left" are both questions."""
        names, current = (self._stage_list or ((), ""))
        try:
            at = list(names).index(current) if current in names else -1
        except ValueError:
            at = -1
        while len(self.stage_chips) < len(names):
            lbl = tk.Label(self.stages, bd=0, padx=int(5 * self.app.sc),
                           pady=0, highlightthickness=0,
                           font=self.app.f_small, background=CLR["card"])
            lbl.pack(side="left")
            self._bind_drag(lbl)
            self.stage_chips.append(lbl)
        for i, lbl in enumerate(self.stage_chips):
            if i >= len(names):
                lbl.pack_forget()
                continue
            if not lbl.winfo_ismapped():
                lbl.pack(side="left")
            if i == at:
                lbl.configure(text=names[i], background=CLR["btn_act"],
                              foreground=CLR["text"])
            else:
                lbl.configure(text=names[i], background=CLR["card"],
                              foreground=CLR["muted"] if (at >= 0 and i < at)
                              else CLR["faint"])

    def reshape(self, force=False):
        """Re-lay the strip out if its size has changed what it can show.

        Called on every motion of a resize drag, so it does nothing at all
        unless the answer actually moved - which it does two or three times in
        a drag, not sixty."""
        want = (self._rows_that_fit(), self._tier())
        if want == self._shape and not force:
            return False
        self._shape = want
        self._relayout()
        self.app.repaint_mini()
        return True

    # -- shape ------------------------------------------------------------
    def _relayout(self):
        """Lay the strip out, then let the drive-is-free banner take its row.

        TWO STEPS BECAUSE THE BANNER IS NOT A SHAPE, it is something that
        happens to one: it can arrive at any moment during a rip, in any of
        the four shapes, and what it does is take the phrase's place. Doing it
        after the shape has been laid out means there is exactly one piece of
        code that knows where the phrase is, and it is the code that just put
        it there. See _free_banner_row."""
        self._relayout_shape()
        self._free_banner_row()

    def _relayout_shape(self):
        """Arrange the six widgets for the room there is.

        One line, two or three, and within that as many of the fields as the
        width allows - see _auto_tier. The shapes share their column meanings so
        that growing the strip adds to what is there rather than rearranging it:
        column 1 is always the stretchy one and always holds the bar."""
        g, sc = self.grid_host, self.app.sc
        every = (self.dot, self.headline, self.rule, self.bars, self.pct,
                 self.detail, self.extra, self.info_lbl, self.log,
                 self.stages, self.stall_lbl, self.legend_row,
                 self.facts_lbl, self.bg_head, self.bg_bar)
        for w in every:
            w.grid_forget()
        # AND THE MEMOS GO WITH THEM. set_legend and set_focus_facts skip the
        # work when the value has not changed, which after a grid_forget would
        # mean staying hidden until it did. Cleared here, the next animator
        # frame puts them back - so a strip dragged mid-zoom heals itself.
        self._legend_on = None
        self._focus_facts = None
        for c in range(5):
            g.columnconfigure(c, weight=0, minsize=0)
        for r in range(24):
            g.rowconfigure(r, weight=0)
        rows, show = self._rows_that_fit(), self._shows()
        # off unless the tall floating shape turns it back on below: a wrapped
        # line in a two-line strip costs a line the strip has not got
        self._wrap_floating(0)
        pad = int(self.PAD * sc)
        # docked, the height is the taskbar's and every point of it is spoken
        # for; floating, it can afford to breathe
        tight = max(1, int(sc)) if self.docked else int(5 * sc)
        g.configure(padx=0, pady=0)
        self.bar.repaint(CLR["track"])
        self.total_bar.repaint(CLR["track"])
        # AND THE VIOLET GOES BACK ON. repaint() sets the fill directly and
        # clears the tint memo, so a theme switch or a drag left the
        # background job's bar drawn in the foreground's accent - the one
        # colour it must not be, since the whole point of a second bar is
        # that it is a different film.
        self.bg_bar.repaint(CLR["track"])
        self.bg_bar.tint(CLR["bg_job"])
        self.bg_head.configure(background=CLR["card"],
                               foreground=CLR["bg_job"])
        self.view_btn.configure(background=CLR["btn"],
                                foreground=CLR["bg_job"])
        self._log_style()
        self.stages.configure(background=CLR["card"])
        self._paint_stages()            # the palette may have changed
        self._style_numbers()
        # the bar's column keeps a floor: a progress bar squeezed to a stub by
        # the text either side of it is decoration
        floor = self.DOCK_BAR_W if (self.docked and rows == 2) else self.BAR_W
        g.columnconfigure(1, weight=1, minsize=int(floor * sc))
        gap = int(8 * sc)
        # With a log the spare height belongs to the log, so the lines above it
        # stay their own size; without one the lines share the height between
        # them and the strip reads as evenly spaced rather than top-heavy.
        if not show["log"]:
            for r in range(rows):
                g.rowconfigure(r, weight=1)

        if self.docked:
            self._dock_layout(rows, show, pad, gap, tight)
            return

        if rows <= 1:
            # everything that fits, on the one line there is
            self.dot.grid(row=0, column=0, padx=(pad, int(5 * sc)))
            self.headline.grid(row=0, column=1, sticky="we", padx=(0, gap))
            g.columnconfigure(1, weight=3, minsize=int(90 * sc))
            g.columnconfigure(2, weight=2, minsize=int(self.BAR_W * sc))
            self.bars.grid(row=0, column=2, sticky="we")
            col = 3
            for w, on in ((self.pct, show["pct"]), (self.detail, show["detail"]),
                          (self.extra, show["extra"])):
                if on:
                    w.grid(row=0, column=col, sticky="e", padx=(gap, 0))
                    col += 1
            (self.extra if show["extra"] else self.detail if show["detail"]
             else self.pct if show["pct"] else self.bars).grid_configure(
                padx=(gap, pad))
            return

        # Two lines or more. Everything below the first starts in column 1, the
        # same column the title starts in, so the lines share a left edge
        # instead of the bar hanging out under the dot - and the last thing on
        # each line carries the right-hand padding, so they share that edge too.
        self._merge_extra = False
        self._log_row = None
        self.dot.grid(row=0, column=0, sticky="w",
                      padx=(pad, int(5 * sc)), pady=(tight, 0))
        self.headline.grid(row=0, column=1, sticky="we", pady=(tight, 0))

        if rows == 2:
            # The taskbar's shape, and the fields are dealt out evenly between
            # its two lines rather than piled onto the second: what the disc is
            # and how much of it is done on top, the bar and how far along
            # underneath. Two lines each carrying something at both ends, ending
            # at the same margin - and the bar keeps most of a line to itself
            # instead of being squeezed into a corner of one by the numbers
            # sharing it, which is what made them stop fitting.
            #
            # The columns are shared between the lines, so the counts and the
            # percentage right-align on the same edge, and so do the disc line
            # and the rate. They line up because they are literally the same
            # columns.
            cols = (1, 2, 3)
            rowsets = (
                (0, (tight, 0), ((self.headline, True, "we"),
                                 (self.detail, show["detail"], "e"),
                                 (self.info_lbl, show["info"], "e"))),
                (1, (0, tight), ((self.bars, True, "we"),
                                 (self.pct, show["pct"], "e"),
                                 (self.extra, show["extra"], "e"))),
            )
            for row, pady, items in rowsets:
                shown = [i for i, (_w, on, _s) in enumerate(items) if on]
                for i, (w, on, stick) in enumerate(items):
                    if not on:
                        w.grid_forget()
                        continue
                    # Each field reaches as far as the next one on its own line,
                    # and the last reaches the margin. Without that a line
                    # holding fewer fields than the other stopped short of it,
                    # which is exactly the raggedness this is meant to avoid -
                    # the columns only line the two lines up when both lines
                    # have something in them.
                    after = [j for j in shown if j > i]
                    end = cols[after[0]] - 1 if after else cols[-1]
                    w.grid(row=row, column=cols[i],
                           columnspan=max(1, end - cols[i] + 1),
                           sticky=stick, pady=pady,
                           padx=(0 if i == 0 else gap, 0 if after else pad))
            return

        # Three or more: one column of left-aligned lines, with only the
        # percentage sitting off to the right beside its bar. Everything else
        # reads down the left edge - a rate and an elapsed time pinned to the
        # far right, with the byte counts left-aligned under the bar, looked
        # exactly as stranded as it was.
        #
        # And they wrap rather than run off the end. This shape is a window the
        # user has sized, so the answer to a line too long for it is a second
        # line, not a sentence with its last few words missing - a folder path
        # cut off at the right-hand edge is the one thing here that cannot be
        # guessed at from what is left.
        self._wrap_floating(max(int(60 * sc), (self._gw or 0) - 2 * pad
                                - int(18 * sc)))
        self.headline.grid_configure(columnspan=4, padx=(0, pad))
        self.bars.grid(row=1, column=1, columnspan=3, sticky="we",
                       pady=(0, tight))
        self.pct.grid(row=1, column=4, sticky="e", padx=(gap, pad),
                      pady=(0, tight))
        if not show["pct"]:
            self.pct.grid_remove()
        next_row = 2
        # THE BACKGROUND JOB'S BLOCK, DIRECTLY UNDER THE FOREGROUND'S BAR,
        # which is where the window puts it and where it was asked for. Two
        # rows, a line and a bar, and it takes them ahead of the stage list
        # and the readouts because it is the only thing on the strip that is
        # about a second piece of work - everything below it is another detail
        # of the one above. See _shows for what "there is room" means: it is
        # measured against what the rest of this shape wants, not a constant.
        if show.get("bg"):
            self.bg_head.grid(row=next_row, column=1, columnspan=4,
                              sticky="we", padx=(0, pad))
            next_row += 1
            self.bg_bar.grid(row=next_row, column=1, columnspan=4,
                             sticky="we", pady=(0, tight))
            next_row += 1
        # ABOVE the readouts, directly under the bar it is describing: the list
        # is the shape of the job and the numbers under it are details of the
        # step it marks.
        if show.get("stages") and next_row < rows - (1 if show["log"] else 0):
            self.stages.grid(row=next_row, column=1, columnspan=4, sticky="w",
                             pady=(0, tight))
            next_row += 1
        for w, on in ((self.detail, show["detail"]), (self.extra, show["extra"]),
                      (self.info_lbl, show["info"])):
            if not on:
                w.grid_forget()
                continue
            if next_row >= rows - (1 if show["log"] else 0):
                # out of lines: the elapsed time joins the byte counts rather
                # than being hung on the end of a line it does not belong to
                self._merge_extra = w is self.extra
                if not self._merge_extra:
                    w.grid_forget()
                continue
            w.grid(row=next_row, column=1, columnspan=4, sticky="w",
                   padx=(0, pad), pady=(0, tight))
            next_row += 1
        if show["log"] and next_row < rows - 1:
            # the rule takes a line of its own, so the log gives one up for it
            self.rule.grid(row=next_row, column=1, columnspan=4, sticky="we",
                           pady=(int(3 * sc), int(4 * sc)))
            next_row += 1
        if show["log"] and next_row < rows:
            self.log.grid(row=next_row, column=0, columnspan=5, sticky="nswe",
                          padx=pad, pady=(0, tight))
            g.rowconfigure(next_row, weight=1)      # the spare height goes here
            self.log.configure(height=max(1, rows - next_row))
            self._log_row = next_row

    def set_docked(self, docked):
        self.docked = bool(docked)
        self.dock_var.set(self.docked)
        # Docked overrides the setting, so the z-order has to be re-decided
        # every time the shape changes rather than only when the box is ticked.
        self.apply_top()
        self._fill_detail_menu()        # the two shapes stop at different levels
        self._hidden_with_taskbar = False
        self._place_grips()             # the taskbar owns the height, not us
        self._relayout()                # so its requested size suits the mode
        self._place()                   # which is what decides the height
        self.reshape(force=True)        # and the height decides the shape

    # -- placement --------------------------------------------------------
    def _apply(self, w, h, x, y):
        self._gw, self._gh, self._gx, self._gy = int(w), int(h), int(x), int(y)
        self.wm_geometry(f"{self._gw}x{self._gh}+{self._gx}+{self._gy}")
        # THE BANNER IS MEASURED AGAINST THE WIDTH, so a resize has to
        # re-measure it. Asked for in those words: "make sure the text
        # actually expands if the user expands the progress strip."
        self._fit_free_banner()

    def _saved_width(self):
        try:
            w = int(self.app.dr.sget(self.app.settings,
                                     "general.mini_monitor_width", 0) or 0)
        except (TypeError, ValueError):
            w = 0
        return w if w >= int(self.MIN_W * self.app.sc) else 0

    def _saved_height(self):
        try:
            h = int(self.app.dr.sget(self.app.settings,
                                     "general.mini_monitor_height", 0) or 0)
        except (TypeError, ValueError):
            h = 0
        return h if h >= self.MIN_H else 0

    def _natural_width(self):
        """How wide to make it when no width has been remembered.

        Not simply the requested width: that is measured with 'No rip in
        progress' in it, and would leave the strip too narrow for the thing it
        exists to show the moment a rip started. Docked, it deliberately does
        not resize itself when a rip begins or ends - a strip parked in the
        taskbar that changes width twice per disc is worse than a little empty
        space - so the width it is given up front has to be the one a rip
        needs."""
        w = self.winfo_reqwidth()
        return max(w, int(self.DOCK_W * self.app.sc)) if self.docked else w

    @staticmethod
    def _ellipsize(text, font, budget):
        """Cut text down to a pixel budget, and show that it was cut.

        A label given more text than its column clips it mid-letter, which reads
        as a rendering fault rather than as a decision. An ellipsis reads as a
        decision."""
        if budget <= 0 or not text:
            return ""
        if font.measure(text) <= budget:
            return text
        dots = "\u2026"
        room = budget - font.measure(dots)
        if room <= 0:
            return ""
        cut = text
        while cut and font.measure(cut) > room:
            cut = cut[:-1]
        return cut.rstrip() + dots if cut.strip() else ""

    def _wrap_floating(self, width):
        """Let the wordy fields wrap to this width; 0 to stop them wrapping.

        Everything that can be long and cannot be guessed at from a fragment:
        the disc line, which carries a path, the byte counts, and the elapsed
        and rate. Asked for 31 Aug - a field that does not fit should cost a
        line, not be dropped.

        THE NAME IS STILL LEFT ALONE, and that is the one deliberate exception.
        It is the field that reads perfectly well clipped - "Zack Snyder's
        Justice Le..." is still the film - and it sits on the top line with the
        stage, so wrapping it pushes the bar and every number below it down a
        row to say something the first half already said."""
        for w in (self.info_lbl, self.detail, self.extra, self.bg_head):
            try:
                w.configure(wraplength=max(0, int(width)), justify="left")
            except tk.TclError:
                pass

    def _dock_spare(self, nums):
        """How wide the phrase's line is, docked, once the numbers have theirs.

        Less the border the whole strip is drawn inside, a point either side,
        and the pad in front of the phrase itself - and less everything that
        is not the phrase's: the stacked readouts, which sit in columns of
        their own to the right of the bar, the right-hand margin, and the grip
        drawn over the edge. The phrase shares the bar's column, so what it
        has to fit inside is the bar, not the strip.
        """
        sc = self.app.sc
        spare = max(0, (self._gw or 0) - 2 - int(self.PAD * sc))
        return max(int(self.DOCK_BAR_W * sc // 2),
                   spare - self._nums_width([t for t, _f in (nums or ())]))

    def _dock_note_room(self, title, stage, nums):
        """What is left of the phrase's line for the thing after the stage.

        The background note's room, and now the button's - which is why it is
        a function rather than three lines inside _dock_fit: set_bg_button has
        to pick a wording that fits before _dock_fit is called, and two copies
        of this subtraction that drift apart is a button drawn over a
        percentage.

        A legible title comes first, at its own floor, and so does the whole
        stage; what survives that is all this may have.
        """
        f_small, f_bold = self.app.f_small, self.app.f_bold
        spare = self._dock_spare(nums)
        sep = f_small.measure("  -  ") if stage else 0
        want = f_small.measure(stage) if stage else 0
        return max(0, spare - want - sep
                   - max(f_bold.measure("Ab…"), f_bold.measure(str(title))))

    def _dock_fit(self, title, stage, nums, note="", note_pad=0,
                  note_first=False):
        """Cut the background note, then the disc's name, then the stage.

        The two of them are one phrase on a line of their own now, so what they
        have to fit inside is the strip, not the strip less the numbers - those
        are on the line below and no longer take anything from this one.

        Between the two, the name gives way first: it is the thing that can be
        recognised half-written, where a stage clipped to "decrypti" says
        nothing at all. So the stage is measured whole and the name takes what
        is left, and only if the name is down to nothing does the stage get cut
        as well.

        AND THE BACKGROUND NOTE GOES BEFORE EITHER OF THEM. It is a
        footnote on a line about something else: a film encoding behind the
        rip used to be measured as part of the stage, which meant the stage
        was measured whole and the rip's own name came out as "..." to make
        room for a name nobody was waiting on. So the note is priced first,
        and dropped outright if it does not fit.

        Both cuts are measured in the font the field is actually drawn in, so
        this is what will fit rather than a guess at how wide a letter is.

        `nums` is what the readouts are ABOUT to say - see _nums_width, which
        cannot read it off the widgets because show() writes their text after
        this has already decided the layout.

        `note_pad` is chrome the note's text does not measure - the button has
        a border and padding, a plain label has neither.

        `note_first` reverses the one claim this function is otherwise built
        around, and there is exactly one state that wants it: the strip
        describing the background job. There the "note" is the button back to
        the rip, the stage beside it is a detail of a readout somebody asked
        for, and a control that shrinks to "Foreground" because a stage was
        measured whole is the wrong way round."""
        f_small, f_bold = self.app.f_small, self.app.f_bold
        spare = self._dock_spare(nums)
        sep = f_small.measure("  -  ") if stage else 0
        want = f_small.measure(stage) if stage else 0
        # A NAME HAS TO SAY SOMETHING, AND "...." WAS NOT ENOUGH TO SAY
        # ANYTHING. This floor was f_bold.measure("....") - 16 points - while
        # the ellipsis _ellipsize puts on the end measures 12 on its own. A
        # title handed exactly the floor therefore had four points for its
        # letters, no letter fits in four, and _ellipsize returned "". That is
        # how a 300 px strip came out with the film's name completely absent
        # and its stage written out in full: backlog 18(c), reported as "no
        # film name in the top header" and read as a missing feature rather
        # than as a floor that was 12 points of punctuation.
        #
        # Two letters and the ellipsis, measured in the font, because that is
        # the shortest string the ellipsizer can actually produce.
        floor = f_bold.measure("Ab…")
        # THE NOTE FIRST. What is left after the stage and a legible title is
        # all it may have - see _dock_note_room, which is that subtraction,
        # written once because set_bg_button has to make it too. The note is
        # the button now, and a button does not survive being cut, so the
        # wording was chosen to fit before this was called: this branch is
        # what happens when even the shortest one did not, and then the
        # button goes rather than half of it staying.
        note_gap = int(self.NOTE_GAP * self.app.sc)
        room = self._dock_note_room(title, "" if note_first else stage, nums)
        if note and note_gap + f_small.measure(note) + note_pad > room:
            note = ""
        nsep = note_gap if note else 0
        nwant = (f_small.measure(note) + note_pad) if note else 0
        if want + sep > max(0, spare - floor - nwant - nsep):
            stage = self._ellipsize(stage, f_small,
                                    max(0, spare - floor - nwant - nsep - sep))
            want = f_small.measure(stage) if stage else 0
        title = self._ellipsize(title, f_bold,
                                spare - want - sep - nwant - nsep)
        return title, stage, note

    def _grip_w(self):
        """The width of the resize edge, which is drawn OVER the contents.

        `grip` is `place`d at relx=1.0 with relheight=1.0 - see _place_grips -
        so it is not a column and takes nothing out of the grid; it simply
        sits on top of the right-hand end of both rows. Anything that believes
        it may draw out to the strip's own edge is therefore wrong by this
        much. Same expression as _place_grips, because a reservation that does
        not match the widget is the fault it is meant to fix."""
        return max(3, int(self.GRIP * self.app.sc))

    def _nums_width(self, pending=None):
        """How much of the docked strip is not the phrase's to use.

        The widest of each stacked PAIR, because they share a column, plus a
        gap in front of each - and then the right-hand margin and the resize
        grip, which are not columns but are just as unavailable.

        Measured in the font each is drawn in rather than guessed at from a
        character count - the byte counts are in f_small and the percentage in
        f_numb, and on a 150% display those are four points apart.

        `pending` is the (percentage, byte counts) THIS repaint is about to
        draw, and it has to be passed in because the labels do not have it
        yet: show() writes their text near its end, long after the layout has
        been decided, so reading the widgets here read the previous tick -
        "9.99%" while "10.00%" was going in.

        MEASURED 5 Sep, and this function was wrong by 20 points at every
        width: the three clock readouts were the only labels on the strip
        built without `bare`, so each asked for about seven points of default
        Label chrome that no font measurement here could see, and the
        right-hand margin and the grip were not counted at all. The phrase was
        therefore cut to fit a column 20 points wider than the one it got, and
        tk clipped the remainder - backlog 18(a), (d) and (e), which are all
        this one number. See measurements/measure_dock.py."""
        if not self.docked:
            return 0
        sc = self.app.sc
        gap = int(8 * sc)
        f_small, f_num = self.app.f_small, self.app.f_numb
        _pct, _detail = (pending or (None, None))[0:2]
        cols = []
        # ONLY THE COLUMNS THAT WILL ACTUALLY BE THERE. Every one of these
        # labels carries text whether or not this width has room to grid it -
        # set_dock_numbers fills all three on every tick - so counting them by
        # their text reserved room for readouts that were not on the strip.
        # That is the other half of backlog 18(a): at 320 px the phrase was
        # cut 62 points early to make space for a rate and an elapsed time
        # that the "normal" tier does not draw, which is precisely "fields
        # dropped while there is visible empty width".
        #
        # AND ONLY IN THE SHAPE THAT HAS THEM BESIDE THE PHRASE. Stacked in
        # pairs, the phrase is columnspan=1 and stops where the bar stops, so
        # every readout column is width it cannot have. In the other two
        # shapes - one row, or two rows with a single number - the phrase
        # SPANS those columns, and subtracting them there cut it a hundred
        # points early on a 280 px strip.
        _show = self._shows()
        _pairs = []
        if getattr(self, "_stacked", False):
            if _show.get("detail") or _show.get("pct"):
                _pairs.append(
                    (self.detail.cget("text") if _detail is None else _detail,
                     f_small,
                     self.pct.cget("text") if _pct is None else _pct, f_num))
            if _show.get("extra"):
                _pairs.append((self.dock_elapsed.cget("text"), f_small,
                               self.dock_eta.cget("text"), f_small))
            if _show.get("clock"):
                _pairs.append((self.dock_rate.cget("text"), f_small,
                               "", f_small))
        for t0, f0, t1, f1 in _pairs:
            w = max(f0.measure(str(t0 or "")), f1.measure(str(t1 or "")))
            # THE GAP IS DUE WHETHER OR NOT THE PAIR HAS ANYTHING TO SAY. A
            # column decided by _shows is gridded, and a gridded label with no
            # text still takes its gap and the one point tk gives an empty
            # widget - measured 9 points on a rate that had not arrived yet,
            # which came straight off the phrase and clipped the stage.
            cols.append(max(1, w) + gap)
        # THE MARGIN AND THE GRIP, whether or not there are any numbers: with
        # none of them the BAR is the thing that reaches the right-hand end,
        # and it is drawn under the grip exactly as a byte count would be.
        return sum(cols) + int(self.PAD * sc) + self._grip_w()

    def _style_numbers(self):
        """How the readouts are drawn, which is not the same in both shapes.

        Docked they are one row of figures beside the bar and are drawn as one:
        the byte counts as bright as the percentage, because they are the same
        kind of fact and were the field being squinted at, and the time
        remaining a step behind. Floating they are lines of their own, with room
        to be quieter and a hierarchy worth keeping."""
        self.pct.configure(foreground=CLR["text"])
        self.detail.configure(
            foreground=CLR["text"] if self.docked else CLR["muted"])
        self.extra.configure(
            foreground=CLR["muted"] if self.docked else CLR["faint"])
        # THE FOUR STACKED READOUTS ARE ONE KIND OF THING, so they are drawn
        # as one. Asked for by name; before this the time remaining was a step
        # quieter than the byte counts, which made a two-row grid of numbers
        # read as two unrelated grids.
        for w in (self.dock_elapsed, self.dock_eta, self.dock_rate):
            w.configure(foreground=CLR["text"] if self.docked
                        else CLR["faint"],
                        background=CLR["card"], font=self.app.f_small)
        # The stage too, and for the same reason as `detail`. Docked it is the
        # ONLY description on the strip - "CRF 37, sample 2/3 - step 4 of 4" is
        # the whole answer to "what is it doing" - drawn small, over a 94%
        # translucent window, on a taskbar-height row. Floating it sits under a
        # full headline and can go back to being quieter.
        self.stage_lbl.configure(
            foreground=CLR["text"] if self.docked else CLR["muted"])

    def _dock_layout(self, rows, show, pad, gap, tight):
        """The taskbar's shape: six things and never a seventh.

        The disc's name and the stage on top, the bar and its numbers below,
        with the numbers arriving in a fixed order as width is dragged out of
        the strip - the percentage, then the byte counts, then the time
        remaining. What each one is is in _shows; this is where they go.

        Both lines use the same columns, so the edge the name ends at is
        literally the edge the bar ends at, and the two lines line up because
        they cannot do anything else. The name and the bar are the two that can
        afford to give: a name reads perfectly well half-clipped, and the bar is
        there to be eyeballed rather than measured, so between them they take
        the whole of column one and all of the slack in it. The numbers cannot
        be clipped at all - half a byte count is a wrong byte count - so they
        keep their own width.

        That leaves the stage, which spans the numbers' columns and is usually
        wider than they are. Tk hands the extra width a spanning widget forces
        out evenly across the columns it spans (measured: three columns needing
        37, 68 and 43 points, under a label needing 208, came out 57, 88 and
        63). With every number right-aligned in its own column that surplus
        becomes an even gap in front of each of them, rather than one hole
        sitting between the bar and the first number.

        THAT LAST PARAGRAPH IS ABOUT THE ONE-ROW SHAPE ONLY, and it is why
        the stacked pairs below are LEFT-aligned instead. In the two-row shape
        the phrase is `columnspan=1` and cannot reach across the numbers'
        columns, so there is no forced surplus to spread and nothing for the
        right alignment to be doing; what it did instead was push the second
        line of each pair away from the first - "2.12%" ending where
        "0.6 / 32.7 GiB" ends rather than starting where it starts. Asked for
        explicitly, twice: the stacked groups are left-aligned."""
        nums = [w for w, on in ((self.pct, show["pct"]),
                                (self.detail, show["detail"]),
                                (self.extra, show["extra"])) if on]
        g, sc = self.grid_host, self.app.sc
        # THE RIGHT-HAND MARGIN IS THE PAD PLUS THE GRIP, everywhere in this
        # function. The grip is drawn over the strip's right edge and is not a
        # column - see _grip_w - so a widget given only `pad` there is a
        # widget with the resize bar over the end of it.
        edge = pad + self._grip_w()
        for w in (self.dot, self.info_lbl, self.log):
            w.grid_forget()
        for w in (self.dock_elapsed, self.dock_eta, self.dock_rate):
            w.grid_forget()
        last = 1 + max(1, len(nums))            # the right-hand column
        g.columnconfigure(1, weight=1, minsize=int(self.DOCK_BAR_W * sc))
        for c in range(2, last + 1):
            g.columnconfigure(c, weight=0, minsize=0)

        if rows <= 1:
            # A taskbar too short for two lines. Same six things, same order,
            # all on the one line there is - including `extra`, which is one
            # of the `nums` here rather than a pair to stack.
            self._stacked = False
            self.headline.grid(row=0, column=1, sticky="we", padx=(pad, 0))
            self.bars.grid(row=0, column=3, sticky="we", padx=(gap, 0))
            g.columnconfigure(1, weight=2, minsize=int(90 * sc))
            g.columnconfigure(3, weight=3, minsize=int(self.DOCK_BAR_W * sc))
            for i, w in enumerate(nums):
                w.grid(row=0, column=4 + i, sticky="e", padx=(gap, 0))
            (nums[-1] if nums else self.bars).grid_configure(padx=(gap, edge))
            return

        # ---- TWO ROWS, IN PAIRS, EITHER SIDE OF THE BAR ------------------
        #
        # ASKED FOR 3 Sep, and the reason given was the right one: "there's a
        # lot of information in the taskbar progress strip that's gated on
        # horizontal space. Make it so that the top row cannot be longer than
        # the progress bar - that way, you can use both rows."
        #
        # The old shape spent the whole top line on the phrase and put every
        # number on the bottom one, in a single row of four fields that ran
        # out of width at about 640 px - so the elapsed time and the read rate
        # were the first things dropped, and they are two of the three numbers
        # somebody watching a two-hour salvage actually wants.
        #
        #   row 0   [ film - sweeping - 2/6 ]  GiB/GiB   elapsed
        #   row 1   [ ===== bar ========== ]   percent   left      MiB/s
        #
        # The phrase is now capped at the bar's own column, which is what
        # makes the room: it cannot reach across the numbers' columns, so
        # those columns are as wide as their widest number rather than as wide
        # as whatever the phrase forced. _dock_fit measures against that cap.
        # WHOSE READOUTS THESE ARE. `extra` is the floating shapes' joined
        # sentence and has no place in the stacked pairs; the tail of show()
        # re-grids it from its last configuration unless something says not
        # to, which is what this flag is for.
        self._stacked = True
        self.extra.grid_forget()
        # THE BANNER USED TO BE BUILT HERE, by hand, as a fifth docked shape:
        # ungrid the top row, then re-grid the bar and three of the readouts
        # into row 1 at columns this function had already decided once. It is
        # in _free_banner_row now, which takes the phrase's place in whatever
        # shape was just laid out - one place instead of two, and the two had
        # already drifted (this copy right-aligned the readouts after the
        # stacked pairs went left-aligned).
        _stack = show["detail"] or show["extra"]
        if not _stack:
            self._stacked = False
            # Too narrow for anything but the bar and its percentage: the old
            # shape, which is the right one when there is one number to place.
            self.headline.grid(row=0, column=1, columnspan=max(1, last),
                               sticky="we", padx=(pad, edge), pady=(tight, 0))
            self.bars.grid(row=1, column=1, columnspan=1 if nums else last,
                           sticky="we", padx=(pad, 0 if nums else edge),
                           pady=(0, tight))
            for i, w in enumerate(nums):
                w.grid(row=1, column=2 + i, sticky="we", pady=(0, tight),
                       padx=(gap, edge if i == len(nums) - 1 else 0))
            return
        # THE PHRASE STOPS WHERE THE BAR STOPS. columnspan=1, so column 1 is
        # the only one it can claim - and column 1 is the bar's.
        self.headline.grid(row=0, column=1, columnspan=1, sticky="we",
                           padx=(pad, 0), pady=(tight, 0))
        self.bars.grid(row=1, column=1, columnspan=1, sticky="we",
                       padx=(pad, 0), pady=(0, tight))
        # Column 2 borders the bar: the byte counts over the percentage.
        # LEFT-ALIGNED, both of them, so the two lines of the pair start at
        # the same point - see the docstring.
        _col = 2
        self.detail.grid(row=0, column=_col, sticky="w", pady=(tight, 0),
                         padx=(gap, 0))
        self.pct.grid(row=1, column=_col, sticky="w", pady=(0, tight),
                      padx=(gap, 0))
        _col += 1
        if show["extra"]:
            # Then the clocks: how long it has been going over how long is
            # left. Elapsed on top because it is the one that is certainly
            # true.
            self.dock_elapsed.grid(row=0, column=_col, sticky="w",
                                   pady=(tight, 0), padx=(gap, 0))
            self.dock_eta.grid(row=1, column=_col, sticky="w",
                               pady=(0, tight), padx=(gap, 0))
            _col += 1
            if show.get("clock"):
                # And the rate, on the bottom row only - there is nothing it
                # pairs with, and a lone figure reads better beside the
                # numbers than above them.
                self.dock_rate.grid(row=1, column=_col, sticky="w",
                                    pady=(0, tight), padx=(gap, edge))
                _col += 1
        # THE RIGHT-HAND MARGIN GOES TO THE LAST THING ON EACH ROW, AND IT
        # INCLUDES THE GRIP. Both rows, because the grip is placed over the
        # full height of the strip: with the rate hidden, the elapsed time was
        # the rightmost thing on the top row with no right pad at all, and the
        # last digit of it was drawn under the resize bar. Observed 3 Sep in a
        # screenshot; backlog 18(d), and the fix asked for was to reserve the
        # bar's width rather than remove a control that is how the strip is
        # resized at all.
        for row in (0, 1):
            for w in (self.dock_rate, self.dock_eta, self.dock_elapsed,
                      self.pct, self.detail):
                info = w.grid_info()
                if info and int(info["row"]) == row:
                    w.grid_configure(padx=(gap, edge))
                    break

    def _natural_height(self):
        """How tall to make a floating strip when no height has been remembered.

        Three lines, which is the shape it was designed around and the one that
        gives the byte counts a line of their own. Worked out from the height of
        a line rather than from what the layout currently asks for, which would
        be circular: a two-line layout asks for two lines' worth of height,
        which then keeps it at two lines for ever."""
        return max(self.winfo_reqheight(),
                   3 * self._line() + int(6 * self.app.sc))

    def _place(self):
        self.update_idletasks()
        w = self._saved_width() or self._natural_width()
        if self.docked:
            self._dock(w)
            return
        h = self._saved_height() or self._natural_height()
        pos = self._saved_pos()
        if pos is None:                 # above the taskbar, right-hand side
            pos = (self.winfo_screenwidth() - w - int(24 * self.app.sc),
                   self.winfo_screenheight() - h - int(68 * self.app.sc))
        self._apply(w, h, *self._clamp(*pos, w, h))

    def _saved_pos(self):
        saved = str(self.app.dr.sget(self.app.settings,
                                     "general.mini_monitor_pos", "") or "")
        if "," not in saved:
            return None
        try:
            return tuple(int(v.strip()) for v in saved.split(",", 1))
        except ValueError:
            return None

    def _dock(self, w):
        """Sit inside the taskbar, exactly as tall as it is, before the clock.

        The height is the taskbar's and is not negotiable while docked - a
        remembered one is ignored rather than applied, so a height dragged while
        floating cannot follow the strip into the taskbar and leave it standing
        proud of the bar."""
        tb = taskbar_bounds()
        if tb is None:                  # no taskbar found: lie on the desktop
            self.docked = False
            self.dock_var.set(False)
            self._relayout()
            self._place()
            return
        left, top, right, bottom, clock = tb
        inset = max(1, int(2 * self.app.sc))
        h = max(self.MIN_H, bottom - top - inset * 2)
        w = min(w, max(int(self.MIN_W * self.app.sc), right - left - inset * 2))
        x = clock - w - int(8 * self.app.sc)
        saved = self._saved_pos()
        if saved is not None:           # dragged along the bar; keep the slot
            x = saved[0]
        x = max(left + inset, min(x, right - w - inset))
        self._apply(w, h, x, top + inset)
        # A taskbar set to hide itself slides off screen but keeps its window, so
        # follow it out of sight rather than leaving a strip floating over
        # nothing at the edge of the display.
        gone = self._offscreen(top, bottom)
        if gone and not self._hidden_with_taskbar:
            self._hidden_with_taskbar = True
            self.withdraw()
        elif not gone and self._hidden_with_taskbar:
            self._hidden_with_taskbar = False
            self.deiconify()
            self._eclipsed = False      # the deiconify above put it back up
            self._check_eclipse()       # ...which may not have been wanted

    def _offscreen(self, top, bottom):
        try:
            import ctypes
            m = ctypes.windll.user32.GetSystemMetrics
            vy, vh = m(77), m(79)
        except Exception:
            return False
        if vh <= 0:
            return False
        mid = (top + bottom) / 2
        return mid < vy or mid > vy + vh

    def retrack(self):
        """Follow the taskbar if it moved, was resized, or hid itself."""
        if self.docked and self.winfo_exists() and not self._sizing \
                and not self._drag:
            self._dock(self._gw or self._saved_width() or self._natural_width())

    def _clamp(self, x, y, w, h):
        """Keep the strip somewhere that actually exists.

        Against the whole virtual desktop, not the primary screen: the position
        is remembered between runs, and a strip left on a second monitor that is
        no longer plugged in would otherwise come back invisible with no way to
        reach it."""
        try:
            import ctypes
            m = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = (m(76), m(77), m(78), m(79))
        except Exception:
            vx, vy = 0, 0
            vw, vh = self.winfo_screenwidth(), self.winfo_screenheight()
        if vw <= 0 or vh <= 0:
            return x, y
        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))
        return x, y

    # -- dragging ---------------------------------------------------------
    def _grab(self, e):
        self._drag = (e.x_root - self._gx, e.y_root - self._gy)

    def _move(self, e):
        if not self._drag:
            return
        w, h = self._gw, self._gh
        x, y = e.x_root - self._drag[0], e.y_root - self._drag[1]
        if self.docked:
            # docked, it slides along the bar and no further: dragging it out of
            # the taskbar by accident would undo the mode without being asked
            tb = taskbar_bounds()
            if tb:
                inset = max(1, int(2 * self.app.sc))
                x = max(tb[0] + inset, min(x, tb[2] - w - inset))
                y = tb[1] + inset
        else:
            x, y = self._clamp(x, y, w, h)
        self._apply(w, h, x, y)

    def _drop(self, _e):
        if not self._drag:
            return
        self._drag = None
        self.app.remember_mini_pos(self._gx, self._gy)

    # -- resizing ---------------------------------------------------------
    def _grab_size(self, e, axes="w"):
        if self.docked:
            # the height is the taskbar's; only the width is ours to drag
            axes = axes.replace("h", "") or "w"
        self._sizing = (e.x_root, e.y_root, self._gw, self._gh, axes)
        return "break"

    def _resize(self, e):
        if not self._sizing:
            return "break"
        x0, y0, w0, h0, axes = self._sizing
        w, h = w0, h0
        if "w" in axes:
            w = max(int(self.MIN_W * self.app.sc), w0 + (e.x_root - x0))
            w = min(w, self.winfo_screenwidth())
        if "h" in axes:
            h = max(self.MIN_H, h0 + (e.y_root - y0))
            h = min(h, self.winfo_screenheight() // 2)
        if self.docked:
            tb = taskbar_bounds()
            if tb:
                w = min(w, tb[2] - self._gx - max(1, int(2 * self.app.sc)))
        self._apply(w, h, self._gx, self._gy)
        self.reshape()
        return "break"

    def _drop_size(self, _e):
        if not self._sizing:
            return "break"
        self._sizing = None
        # nothing to remember about a height the taskbar decides
        self.app.remember_mini_size(self._gw,
                                    None if self.docked else self._gh)
        self.reshape()
        return "break"

    def _popup(self, e):
        """The menu, told to take the focus first.

        Same reason the tray menu does it: this strip does not take focus when
        it is clicked - that is rather the point of it - so a menu posted over it
        never gets focus either, and Tk's unpost-when-you-pick-something does not
        happen. It sat there after the choice until the next click somewhere
        else, which was most obvious on 'Sit in the taskbar', where the strip is
        rebuilt underneath it and the menu is left belonging to nothing."""
        try:
            self.tk.call("focus", "-force", self._w)
        except tk.TclError:
            pass
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _act(self, fn):
        """A menu command that takes the menu down before it runs.

        Belt as well as braces for the ones that rebuild or reshape the strip:
        by the time they are finished there may be no window under the menu for
        Tk to tidy it away from."""
        def go():
            try:
                self.menu.unpost()
                self.update_idletasks()
            except tk.TclError:
                pass
            fn()
        return go

    # -- readout ----------------------------------------------------------
    def set_total(self, frac):
        """The whole-run bar: a fraction to show it, None to take it away.

        Packed above the per-item bar in its cell rather than gridded beside it,
        so a strip with no total to show is exactly the strip it was before any
        of this - one bar, at its full weight, in the same place."""
        want = frac is not None
        if want != self._has_total:
            self._has_total = want
            sc = self.app.sc
            gap = max(1, int(2 * sc))
            if want:
                # The whole-run bar carries the weight - on a batch it is the
                # number being waited on - and the per-item one thins right
                # down under it.
                self.bar.set_height(self.ITEM_H * sc)
                self.total_bar.set_height(self.TOT_H * sc)
                # columnspan spelled out both times: grid merges new options
                # with whatever the widget was gridded with before, so the
                # columnspan=2 of the single-bar layout would have carried over
                # and left the per-item bar reaching past the other one.
                self.total_bar.grid(row=0, column=0, columnspan=1, sticky="we")
                self.barrow.grid(row=1, column=0, columnspan=1, sticky="we",
                                 pady=(gap, 0))
            else:
                self.total_bar.grid_remove()
                self.total_pct.grid_remove()
                self.item_pct.grid_remove()
                self.bar.set_height(self.SOLO_H * sc)
                self.barrow.grid(row=0, column=0, columnspan=2, sticky="we",
                                 pady=0)
        if want:
            self.total_bar.set_done(float(frac) >= 0.999)
            self.total_bar.set(frac)

    def _paint_pcts(self, pct, total, show):
        """A percentage per bar, or one, depending on the room.

        Tight, the one kept is the whole run's - it is the slower of the two and
        the one being waited on, and the per-item figure is a detail of it. The
        strip's own percentage field stands down whenever these are up, or the
        same number appears twice on one line."""
        if not self._has_total:
            self._two_pcts = False
            return
        gap = max(1, int(6 * self.app.sc))
        self.total_pct.configure(text="" if total is None
                                 else f"{max(0.0, min(1.0, total)) * 100:.0f}%")
        self.total_pct.grid(row=0, column=1, sticky="e", padx=(gap, 0))
        # Room for both is width and height. Width the tiers already answer -
        # it is the same question as room for the byte counts. Height is the
        # part that bites: two lines of text in the bar cell, plus the title
        # line above it, wants about 49 points and a taskbar is about 44, so
        # docked on two rows the per-item figure is the one that gives way. It
        # is the faster-moving of the two and a detail of the other, and the bar
        # is still there to be eyeballed.
        room = (not self.docked) or self._rows_that_fit() >= 3
        self._two_pcts = room and bool(show.get("detail")) and bool(pct)
        if self._two_pcts:
            self.item_pct.configure(text=str(pct))
            self.item_pct.grid(row=1, column=1, sticky="e", padx=(gap, 0))
        else:
            self.item_pct.grid_remove()

    def set_done(self, done):
        """Green and full when the disc is finished, back to normal when the
        next one starts."""
        done = bool(done)
        if done == self._done:
            return False
        self._done = done
        self.bar.set_done(done)
        if done:
            self.bar.set(1.0)
        return True

    # -- the log tail -----------------------------------------------------
    def shows_log(self):
        try:
            return bool(self._shows()["log"]) and self.winfo_exists()
        except tk.TclError:
            return False

    # WHAT A LINE IS, in the same four kinds the window's own log uses, so a
    # strip dragged open shows the same colours as the log it is a tail of. The
    # engine's console markers are the only classifier there is - see
    # DiscRipperApp._log_line, which reads them the same way.
    LOG_TAGS = (("[OK]", "l_ok"), ("[!]", "l_warn"), ("[X]", "l_err"),
                ("* ", "l_info"))

    def _log_style(self):
        self.log.configure(background=CLR["card"], foreground=CLR["muted"])
        # Continuations hang under the message rather than under the clock, the
        # same as the window's own log. Measured off the font so it lands right
        # at any DPI.
        try:
            self.log.configure(
                lmargin2=self.app.f_small.measure("00:00:00  "))
        except tk.TclError:
            pass
        for name, key in (("l_ok", "ok"), ("l_warn", "warn"),
                          ("l_err", "err"), ("l_info", "muted"),
                          ("l_head", "text"), ("l_bg", "bg_job")):
            self.log.tag_configure(name, foreground=CLR[key])

    @classmethod
    def _log_tag(cls, text, src="fg"):
        """Which tag one raw log line draws in."""
        body = str(text).strip()
        if src == "bg":
            # The other film, and that outranks the marker for everything
            # except a failure - which is worth more than knowing whose it is.
            for mark, tag in (("[X]", "l_err"), ("[!]", "l_warn")):
                if body.startswith(mark):
                    return tag
            return "l_bg"
        for mark, tag in cls.LOG_TAGS:
            if body.startswith(mark):
                return tag
        if body.startswith("==") and body.endswith("=="):
            return "l_head"
        return ""

    def log_fill(self, rows):
        """Put the tail of the log in, from the history the window keeps.

        Called when the strip changes shape, because the log is the one field
        that cannot be reconstructed from the rip's current state - everything
        else is a number that arrives again a second later."""
        if not self.shows_log():
            return False
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for entry in list(rows)[-self.LOG_LINES:]:
            ts, text = entry[0], entry[1]
            src = entry[2] if len(entry) > 2 else "fg"
            self.log.insert("end", f"{ts}  {text}\n",
                            (self._log_tag(text, src),))
        self.log.see("end")
        self.log.configure(state="disabled")
        return True

    def log_push(self, ts, text, src="fg"):
        """One more line, if there is anywhere to put it."""
        if not self.shows_log():
            return False
        self.log.configure(state="normal")
        self.log.insert("end", f"{ts}  {text}\n",
                        (self._log_tag(text, src),))
        over = int(self.log.index("end-1c").split(".")[0]) - self.LOG_LINES
        if over > 0:
            self.log.delete("1.0", f"{over + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        return True

    # -- finishing --------------------------------------------------------
    def _flash_labels(self):
        """Everything with text on it. All of it blinks, or the strip looks
        like one label misbehaving rather than a strip catching your eye.

        EVERYTHING, and this list is the only thing that puts the colours back
        afterwards - _paint_flash restores from the snapshot taken over exactly
        these widgets. A label that blinks without being in here is painted the
        background colour once and left that way. The two per-bar percentages
        were, and after the first finished item of an auto-encode they were
        #252525 text on a #2e2e2e card for the rest of the run."""
        return (self.dot, self.title_lbl, self.stage_lbl, self.pct,
                self.detail, self.extra, self.info_lbl, self.stall_lbl,
                self.total_pct, self.item_pct, self.bg_head)

    def flash(self, failed=False):
        """Blink the strip a few times when a rip finishes.

        A rip runs for an hour, so nobody is watching the moment it ends - and
        if the strip is on screen it is usually the only thing that knows.
        Green for a finished rip, red for a failed one, three blinks and done:
        long enough to catch out of the corner of an eye, short enough not to
        become something to sit through."""
        if not bool(self.app.dr.sget(self.app.settings,
                                     "general.mini_monitor_flash", True)):
            return False
        self._flash_colour = CLR["err"] if failed else CLR["ok"]
        if self._flash_left > 0:
            # Already blinking - a second disc landing inside the first one's
            # blink. Undo it before reading the colours back, or what gets kept
            # as "what this field was" is the dark the blink itself put there,
            # and it never comes off.
            self._paint_flash(None)
        # What each field was, so it can be put back. Read now rather than
        # assumed, because the title alone is one of four colours depending on
        # what has happened to the disc.
        self._fg = {w: w.cget("foreground") for w in self._flash_labels()}
        self._flash_left = self._flash_steps(failed)
        self._flash_step()
        return True

    # How long one on-or-off half of the blink lasts. Six of these was the
    # whole flash; now it is however many fit the sound.
    FLASH_MS = 170
    # A blink is only worth having if it is long enough to catch the eye and
    # short enough not to become the thing you are looking at. Both ends are
    # here so that a sound setting pointing at a ten-minute track cannot turn
    # the strip into a strobe for the rest of the evening.
    FLASH_MIN_S = 0.8
    FLASH_MAX_S = 6.0

    def _flash_steps(self, failed=False):
        """Blink for as long as the sound that goes with it plays.

        Asked for 31 Aug: the flash was 6 x 170 ms = 1.02 s against a finish
        sound of 3.12 s, so the strip had gone quiet with two thirds of the
        chime still playing.

        READ FROM THE SOUND, not written down beside it. Both are settings -
        alerts.sound_rip_finished is a path a person can change - and a number
        copied from one file's length is wrong the moment they do. A Windows
        scheme alias ("SystemAsterisk") is not a file and has no length this
        can read, so that falls back to what it always was.
        """
        steps = 6
        try:
            key = ("alerts.sound_failure" if failed
                   else "alerts.sound_rip_finished")
            dr = self.app.dr
            path = dr.sound_file(dr.sget(self.app.settings, key))
            if path:
                import wave
                with wave.open(str(path)) as w:
                    secs = w.getnframes() / float(w.getframerate() or 1)
                secs = max(self.FLASH_MIN_S, min(self.FLASH_MAX_S, secs))
                # Even, so the blink ends on OFF rather than leaving the strip
                # standing in the flash colour for repaint_mini to clear.
                steps = max(2, int(round(secs * 1000 / self.FLASH_MS / 2)) * 2)
        except Exception:                                        # noqa: BLE001
            pass
        return steps

    def _flash_step(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self._flash_left <= 0:
            self._paint_flash(None)
            self.app.repaint_mini()     # whatever it should really be showing
            return
        self._paint_flash(self._flash_colour if self._flash_left % 2 == 0
                          else None)
        self._flash_left -= 1
        try:
            self.after(self.FLASH_MS, self._flash_step)
        except tk.TclError:
            pass

    def _set_stage(self, text):
        """The state after the name, or nothing at all if there is none.

        BEFORE THE BUTTON, said rather than left to the order of the calls.
        The packer appends a widget it has not seen before, and the stage goes
        from packed to forgotten and back whenever a stage arrives without one
        - so on the way back it landed after a button that had been sitting
        there all along, which is the "footnote between the film's name and
        what is happening to it" fault of 5 Sep with the button in the
        footnote's place. See set_bg_button.
        """
        text = str(text or "").strip()
        try:
            if text:
                self.stage_lbl.configure(text="  -  " + text)
                if self._btn_text:
                    self.stage_lbl.pack(side="left", before=self.view_btn)
                else:
                    self.stage_lbl.pack(side="left")
            else:
                self.stage_lbl.configure(text="")
                self.stage_lbl.pack_forget()
        except tk.TclError:
            pass

    def _paint_flash(self, colour):
        """Repaint every surface at once, so the whole strip blinks rather than
        one label inside it.

        The dark text a blink needs has to be undone on the way out, and this is
        the only place that knows it was ever applied. It used not to be undone:
        show() puts the title and the percentage back on its way past, which is
        why those two looked fine, but nothing put back the byte counts, the
        stage, the time or the disc line - so after the first disc of an
        auto-rip they were drawn in the window's background colour, a grey a
        shade off the bar sitting next to them, for the rest of the run."""
        bg = colour or CLR["card"]
        # THE LOG IS NOT PART OF THE BLINK. Asked for 31 Aug and again 1 Sep: a
        # floating strip showing the log turned the whole panel yellow on a
        # stall, log and all, and the log's text keeps its own tag colours - so
        # green and red lines ended up on a yellow field. The log is the
        # window's history rather than this rip's state, and it is the one
        # thing on the strip that is still worth reading while the rest of it
        # is shouting.
        #
        # IT IS EXCLUDED BY BEING PUT BACK, not by being left out of a list.
        # Measured 1 Sep: it was already the right colour, but only because
        # nothing happened to name it - which is a fact about a list rather
        # than an intention, and the red state below goes through this same
        # routine. See _never_flashed.
        for w in (self.body, self.inner, self.grid_host, self.bars,
                  self.headline, self.stages):
            w.configure(background=bg)
        try:
            self.rule.configure(background=CLR["bg"] if colour
                                else CLR["border"])
        except tk.TclError:
            pass
        # the bars keep their own trough while the strip blinks, or the two
        # rectangles that make each one become one rectangle
        for b in (self.bar, self.total_bar):
            b.repaint(bg if colour else CLR["track"])
        for w in self._flash_labels():
            w.configure(background=bg)
            if colour:
                w.configure(foreground=CLR["bg"])
            elif w in self._fg:
                w.configure(foreground=self._fg[w])
        # THE STAGE CHIPS TOO. Measured 1 Sep on a strip wide enough to show
        # them: the attention face left the stages row a card-coloured island
        # with a #484848 chip in it, on an otherwise wholly yellow strip. They
        # are this rip's state, so they belong in the shout; _relayout redraws
        # them through _paint_stages on the way out, which is what puts the
        # marked one back.
        for lbl in self.stage_chips:
            try:
                lbl.configure(background=bg,
                              foreground=CLR["bg"] if colour else CLR["muted"])
            except tk.TclError:
                pass
        for w in self._never_flashed():
            try:
                w.configure(background=CLR["card"])
            except tk.TclError:
                pass
        # THE BUTTON KEEPS ITS OWN FACE, however loud the strip gets: it is the
        # one thing on the strip that can be pressed, and a control dyed the
        # background colour reads as a label. Done here rather than through
        # _never_flashed, which restores the card colour - a button is not
        # card-coloured, and the point of the exception is that it still looks
        # like a button.
        try:
            self.view_btn.configure(background=CLR["btn"],
                                    foreground=CLR["bg_job"])
        except tk.TclError:
            pass
        self.edge.configure(background=colour or CLR["border"])

    def _never_flashed(self):
        """Surfaces that keep their own colour however loud the strip gets.

        Just the log, and it is a list of one so that the reason is written
        down somewhere it will be read: the log is the window's history rather
        than this rip's state, its lines carry their own tag colours, and it is
        the one thing on a shouting strip still worth reading. Enforced after
        the paint rather than by omission - see _paint_flash."""
        return (self.log,)

    def _rowspan(self, n):
        """Let the title take the whole strip, or just its own line.

        Idle it takes all of it, so the strip reads as one line centred in the
        space rather than a title clinging to the top with an empty trough
        underneath. Safe because the size is decided up front now rather than
        measured from whatever it happens to be showing, so nothing moves."""
        rows = 1
        if n > 1:
            # stop above the log rather than over it: the log is not this rip's
            # and stays put while nothing is running
            rows = max(1, self._log_row if self._log_row is not None
                       else self._rows_that_fit())
        for w in (self.dot, self.headline):
            try:
                if not w.grid_info():
                    continue        # not on the grid at all - docked has no dot,
                                    # and grid_configure would put it back
                w.grid_configure(rowspan=rows)
            except tk.TclError:
                pass

    # -- the other task ---------------------------------------------------

    def _btn_pad(self):
        """The chrome inside the button, which measuring the text misses.

        padx either side and a point of border either side. It is a third of
        the narrowest wording, so leaving it out is not a rounding error: it
        is the difference between a button that fits and one drawn over the
        percentage next to it.
        """
        return int(self.BTN_PAD * self.app.sc) * 2 + 2

    def _btn_label(self, room):
        """The widest wording that fits in `room`, or "" for none of them.

        Which ladder depends on which task the strip is describing: the way
        in, or the way back. See BG_BTN_TEXTS for why this is a ladder rather
        than one label and an ellipsis.
        """
        texts = self.FG_BTN_TEXTS if self._bg_view else self.BG_BTN_TEXTS
        try:
            f = self.app.f_small
            pad = self._btn_pad() + int(self.NOTE_GAP * self.app.sc)
            for t in texts:
                if f.measure(t) + pad <= room:
                    return t
        except tk.TclError:
            return ""
        return ""

    def _btn_room(self, title, stage="", nums=None):
        """How much of the title's line the button may have.

        Docked that is the note's old arithmetic exactly - the phrase shares
        the bar's column and the stacked readouts have already taken theirs.
        Floating the whole width is the phrase's, less the margins and the
        grip drawn over the right-hand edge.
        """
        if self.docked:
            return self._dock_note_room(title, stage, nums)
        try:
            return max(0, int(self._gw or 0)
                       - int(self.PAD * self.app.sc) * 2 - self._grip_w()
                       - self.app.f_bold.measure(str(title or "")))
        except tk.TclError:
            return 0

    def set_bg_button(self, note="", label=None):
        """The button where the background note used to be. "" removes it.

        ASKED FOR 9 Sep: "replace the [background step] of [movie name] text
        in the taskbar progress strip with a button that reads Show background
        task progress; when pressed it gives the full progress information for
        the background task, and in that view there should be a button to view
        the foreground task".

        `note` is App._bg_note's sentence and it is used for one thing: is
        there any background work at all. The sentence itself is not drawn any
        more - see the note above view_btn for what was wrong with drawing it -
        and in the background view the button is the only way back, so it
        stays whatever the note says.

        `label` is the wording, already measured against the room the layout
        had left; None means measure it here against the whole line, which is
        what the shapes with no phrase to share it with do.
        """
        want = bool(str(note or "").strip()) or self._bg_view
        text = "" if not want else (label if label is not None
                                    else self._btn_label(self._btn_room("")))
        if text == self._btn_text:
            return text
        self._btn_text = text
        try:
            if text:
                self.view_btn.configure(text=text)
                # LAST ON THE LINE, after the title and the stage. The packer
                # puts the most recently packed widget last and _set_stage
                # re-packs the stage on every repaint, so a button packed
                # before it lands BETWEEN the film's name and what is
                # happening to it - which is what the note it replaces did
                # until 5 Sep.
                self.view_btn.pack(side="left",
                                   padx=(int(self.NOTE_GAP * self.app.sc), 0))
            else:
                self.view_btn.pack_forget()
        except tk.TclError:
            pass
        return text

    def _btn_face(self, colour):
        """The button's own background, under the pointer and off it."""
        try:
            self.view_btn.configure(background=colour)
        except tk.TclError:
            pass

    def _tap_view(self, _e=None):
        """Press the button: turn the strip over to the other task.

        The window is asked rather than told, because it is the one that has
        both tasks' numbers - and it is also the one that knows there is no
        foreground task to go back to, in which case what the strip should
        say is the waiting line. See App.strip_show_bg.
        """
        try:
            self.app.strip_show_bg(not self._bg_view)
        except (AttributeError, tk.TclError):
            pass
        return "break"

    def set_bg_block(self, on):
        """Is there background work for the detached block to describe?

        Only the shape decides whether it is actually drawn - see _shows - and
        this is the fact that shape is decided from, so a job starting or
        ending re-runs the layout the way the drive-is-free banner does. The
        alternative is parking the block in a row of its own high up out of
        everyone's way, which is what set_legend does, and that would put it
        under the log instead of under the bar it belongs to.
        """
        want = bool(on) and not self.docked
        if want == self._bg_on:
            return self.shows_bg()
        self._bg_on = want
        self._relayout()
        return self.shows_bg()

    def shows_bg(self):
        """Is the background block on the strip right now?"""
        try:
            return bool(self._bg_on) and bool(self._shows().get("bg")) \
                and self.winfo_exists()
        except tk.TclError:
            return False

    def set_bg_progress(self, head, frac):
        """Fill the block in. Guarded, because this runs on the 80 ms drain.

        An encode reports about once a second and the drain is twelve times
        that, so eleven of every twelve calls have nothing to say - and
        SlimBar.set redraws the canvas whatever it is handed. See set_paint
        for what unguarded redrawing on a clock cost the last time.
        """
        head = str(head or "")
        if head != self._bg_head_text:
            self._bg_head_text = head
            try:
                self.bg_head.configure(text=head)
            except tk.TclError:
                pass
        frac = None if frac is None else max(0.0, min(1.0, float(frac)))
        if frac != self._bg_bar_frac:
            self._bg_bar_frac = frac
            try:
                self.bg_bar.set(frac or 0.0)
            except tk.TclError:
                pass

    # THE KEY UNDER THE BAR, in the bar's own colours - one call, so a state
    # cannot be listed here in a colour the bar does not draw it in.
    LEGEND = tuple(MAP_LEGEND(ground=None))
    # HIGH ROWS ON PURPOSE. _relayout rebuilds rows 0..n for whichever shape
    # it picked; an empty grid row takes no height, so parking these at 20 and
    # 21 puts them under everything in every shape without either of them
    # having to know what the others chose.
    LEGEND_ROW, FACTS_ROW = 20, 21

    def set_legend(self, on):
        """A key for the bar's colours, detached only.

        A coloured bar needs one exactly once, and this is the only shape with
        room for it. "skipped" has no swatch on purpose: it is the track's own
        grey, which is the point being made about it - nothing there was
        measured.
        """
        want = bool(on) and not self.docked
        if want == getattr(self, "_legend_on", None):
            return
        self._legend_on = want
        try:
            if not want:
                self.legend_row.grid_remove()
                return
            self.legend_row.grid(row=self.LEGEND_ROW, column=0,
                                 columnspan=6, sticky="w",
                                 padx=int(self.PAD * self.app.sc))
        except (AttributeError, tk.TclError):
            pass

    def set_focus_facts(self, text):
        """What the zoom is doing, in words, detached only.

        The colours say where; this says what and how far through. Docked
        there is no line for it and the stage field is already carrying the
        endpoints.
        """
        want = "" if self.docked else str(text or "")
        if want == getattr(self, "_focus_facts", None):
            return
        self._focus_facts = want
        try:
            if want:
                self.facts_lbl.configure(text=want)
                self.facts_lbl.grid(row=self.FACTS_ROW, column=0,
                                    columnspan=6, sticky="w",
                                    padx=int(self.PAD * self.app.sc))
            else:
                self.facts_lbl.grid_remove()
        except (AttributeError, tk.TclError):
            pass

    def set_focus_ends(self, lo, hi):
        """The disc positions the zoomed bar runs between, or "" for neither.

        Remembered rather than drawn: show() is what puts text on the stage
        field, twelve times a second, so a write from here would last one
        frame.
        """
        pair = (str(lo or ""), str(hi or ""))
        if pair == getattr(self, "_focus_ends", ("", "")):
            return
        self._focus_ends = pair
        try:
            if lo or hi:
                self.dock_lo.configure(text=str(lo))
                self.dock_hi.configure(text=str(hi))
                if not self.dock_lo.winfo_ismapped():
                    self.dock_lo.grid(row=0, column=0, sticky="w",
                                      padx=(0, 6))
                    self.dock_hi.grid(row=0, column=2, sticky="e",
                                      padx=(6, 0))
            else:
                # Not gated on being mapped - see App.set_focus_ends for the
                # measurement. A docked strip behind another window clears
                # this row exactly once, and that once must land.
                for _w in (self.dock_lo, self.dock_hi):
                    _w.configure(text="")
                    _w.grid_remove()
        except (AttributeError, tk.TclError):
            pass

    def _stage_with_ends(self, stage):
        """The stage, or the zoom's caption when there is one.

        IT USED TO CARRY THE ENDPOINTS, because a taskbar row looked too short
        to flank the bar with them. They are on either side of the bar now -
        asked for twice - and they belong there: they describe the bar's two
        ends, and on a line of other figures there was nothing to say they
        were the ends of anything.

        The seam kept from that change is what the zoom caption uses now. It
        REPLACES the stage rather than joining it: during a zoom the bar has
        stopped describing the disc and started describing thirty megabytes of
        it, and the caption is the only thing that says so, where "sweeping"
        is recoverable from anywhere.
        """
        cap = getattr(self, "_focus_cap", ("", ""))[0]
        return cap or stage

    FREE_HEAD = "Drive not needed anymore - you can start another rip"
    FREE_TAIL = "(click to dismiss)"
    OVERVIEW_ROW = 19

    def set_drive_free(self, on, label=""):
        """Show or hide the drive-is-free banner over the top row."""
        want = bool(on)
        if want == getattr(self, "_free_on", None):
            if want:
                self._fit_free_banner()
            return
        self._free_on = want
        try:
            if not want:
                self.free_lbl.grid_remove()
                self._relayout()
                return
            # LARGE, BOLD, GREEN, AND ABOVE THE BAR, which is how it was
            # asked for. "Large" is as large as the shape can hold: docked,
            # the strip is exactly as tall as the taskbar and both of its
            # rows are spoken for, so a 13-point line in row 0 pushes the bar
            # out of the window - f_bold is the biggest that fits. Floating
            # there is height to spend and it gets f_big.
            self.free_lbl.configure(font=self._free_font(),
                                    foreground=CLR["ok"],
                                    background=CLR["card"])
            # AND THE LAYOUT IS RE-RUN, which is the whole of backlog 19. This
            # only ever happened on the way OFF: the banner was gridded across
            # row 0 while the row-0 widgets stayed exactly where they were, so
            # the strip drew the elapsed time on top of the banner and the
            # banner's own width was whatever one column had. _relayout ends
            # in _free_banner_row, which is what puts it on the strip.
            self._relayout()
        except (AttributeError, tk.TclError):
            pass

    def _free_font(self):
        """The banner's font, and the reason it is not one font.

        Large, bold and green is how it was asked for, and "large" is as
        large as the shape can hold: docked, the strip is exactly as tall as
        the taskbar and both rows are spoken for, so a 13-point line in the
        top row pushes the bar out of the window. f_bold is the biggest that
        fits there; floating there is height to spend."""
        return self.app.f_bold if self.docked else self.app.f_big

    def _free_banner_row(self):
        """Put the drive-is-free banner where the phrase was.

        THE PHRASE'S PLACE, not a row number of its own: the film's name and
        its stage are what the banner is louder than, and every shape this
        strip has already decided where they go. With a second row to hold
        them, everything else on the phrase's row stands down as well and the
        banner has the row - a footnote does not win a fight with live
        progress, but this is not a footnote, and the numbers are all on the
        bar's row anyway.

        On a strip with ONE row there is no such choice, and there the banner
        takes the phrase's cell only: the bar and its percentage are the
        things somebody is actually watching, and a banner that hid them would
        be trading the rip's progress for an announcement about the drive.

        Called at the end of every _relayout, so a banner that arrives
        mid-rip is laid out by the same code that lays out everything else -
        see set_drive_free for what happened when it was not."""
        if not getattr(self, "_free_on", False):
            return
        try:
            info = self.headline.grid_info()
            if not info:
                return              # no phrase on the strip: nothing to take
            row, col = int(info["row"]), int(info["column"])
            sc = self.app.sc
            pad, tight = int(self.PAD * sc), max(1, int(sc))
            whole = self._rows_that_fit() > 1
            if whole:
                # everything that shares the phrase's row gives way
                for w in (self.detail, self.pct, self.extra, self.info_lbl,
                          self.dock_elapsed, self.dock_eta, self.dock_rate,
                          self.stages):
                    at = w.grid_info()
                    if at and int(at["row"]) == row:
                        w.grid_forget()
            span = 9 if whole else max(1, int(info["columnspan"]))
            self.headline.grid_forget()
            self.free_lbl.grid(row=row, column=col, columnspan=span,
                               sticky="we", padx=(pad, 0),
                               pady=(tight, 0) if self.docked else 0)
            self._fit_free_banner()
        except (AttributeError, tk.TclError, KeyError, ValueError):
            pass

    def _free_room(self):
        """The width the banner has, which is not the width of the strip.

        MEASURED FROM WHAT IS ACTUALLY BESIDE IT: the border, the status dot's
        column when there is one, its own left pad, and the grip drawn over
        the right-hand edge. _fit_free_banner used to cut the text to
        `self._gw` - the whole grid, dot and border and grip included - and
        then tk clipped what was left over, which is how a banner that reads
        "Drive not needed anymore - you can start another rip (click to
        dismiss)" came out as the middle of its own tail.

        NOT the label's own winfo_width, tempting as that is: the banner spans
        the weighted column, so a request wider than the strip GROWS that
        column, and measuring the result would let a long banner talk itself
        into more room one repaint at a time."""
        sc = self.app.sc
        pad = int(self.PAD * sc)
        left = 0
        if self.dot.grid_info():
            left = self.dot.winfo_reqwidth() + pad + int(5 * sc)
        wide = int(self._gw or self.winfo_width() or 0)
        return max(0, wide - 2 - left - pad - self._grip_w())

    def dismiss_drive_free(self):
        """What a click on the strip does while the banner is up.

        Returns True if it consumed the click, so the strip's ordinary
        click handling can leave it alone: the first click is for the
        banner and every click after that is for whatever it usually does.
        """
        if not getattr(self, "_free_on", False):
            return False
        self._free_dismissed = True
        self.set_drive_free(False)
        return True

    def _fit_free_banner(self):
        """The banner, cut in the middle if the strip is too narrow for it.

        The tail is what makes it dismissible, so the tail is what is kept.
        Measured in the font it is drawn in rather than counted in characters:
        it is bold and the rest of the strip is not.
        """
        if not getattr(self, "_free_on", False):
            return
        try:
            # THE FONT IT IS ACTUALLY DRAWN IN, which is not the same in
            # both shapes any more - see _free_font. Measuring a 13-point line
            # with a 10-point font is how a banner that "fits" gets clipped.
            f = self._free_font()
            room = self._free_room()
            whole = f"{self.FREE_HEAD} {self.FREE_TAIL}"
            if f.measure(whole) <= room:
                self.free_lbl.configure(text=whole)
                return
            # Keep the tail, cut the head down to what is left.
            tail = self.FREE_TAIL
            if f.measure(tail) > room:
                # NOT EVEN THE TAIL FITS, and the old code handed tk the whole
                # tail anyway and let it clip - which is what was reported on
                # 3 Sep: a green banner reading the middle of its own tail.
                # Cutting it here keeps the rule the rest of the strip works
                # by: an ellipsis is a decision, a cut-off word is a
                # rendering fault. See _ellipsize.
                self.free_lbl.configure(text=self._ellipsize(tail, f, room))
                return
            keep = max(0, room - f.measure("..." + tail))
            head = self.FREE_HEAD
            while head and f.measure(head) > keep:
                head = head[:-1]
            self.free_lbl.configure(
                text=(f"{head.rstrip()}...{tail}" if head else tail))
        except (AttributeError, tk.TclError):
            pass

    def set_zoom_overview(self, lo=None, hi=None, colour=""):
        """The whole-film bar under the zoomed one. Detached only.

        Docked there is no row to give: the strip is two lines tall and both
        are spoken for. Detached it goes above the legend, which is the row
        that explains the colours it is drawn in.
        """
        want = (None if (lo is None or self.docked)
                else (round(float(lo), 5), round(float(hi), 5), colour))
        if want == getattr(self, "_zoom_over", "x"):
            return
        self._zoom_over = want
        try:
            if want is None:
                self.whole_bar.grid_remove()
                return
            a, b, col = want
            self.whole_bar.set_cells(self.bar.cells())
            self.whole_bar.set_paint(
                [(a, max(b, a + 0.004), col)] if col else None)
            if not self.whole_bar.winfo_ismapped():
                self.whole_bar.grid(row=self.OVERVIEW_ROW, column=0,
                                    columnspan=6, sticky="we",
                                    padx=int(self.PAD * self.app.sc),
                                    pady=(3, 0))
        except (AttributeError, tk.TclError):
            pass

    def set_focus_caption(self, text="", colour=""):
        """Take over the line above the bar for the length of a zoom.

        ASKED FOR 3 Sep by name, in sequence: "Error segment detected" in red
        while the section flashes and the bar zooms in, then "finding left
        boundary" / "finding right boundary" once there is a bar to look at,
        then "Error isolated - continuing" through the zoom-out and for two
        seconds after it.
        """
        want = (str(text or "").strip(), str(colour or ""))
        if want == getattr(self, "_focus_cap", None):
            return
        self._focus_cap = want
        try:
            # The title label is what draws the stage on both shapes, so the
            # colour goes there. err is the theme's red; "" is ordinary text.
            self._set_stage(self._stage_with_ends(
                getattr(self, "_plain_stage", "") or ""))
            self.stage_lbl.configure(
                foreground=CLR["err"] if want[1] == "err" else CLR["muted"])
        except (AttributeError, tk.TclError):
            pass

    # The gap in front of the view button, docked. _dock_fit measures the
    # button against the room there is and this is the room the packer then
    # takes in front of it, so the two have to be the same number.
    NOTE_GAP = 6

    def idle(self, text="No rip in progress", failed=False, background=""):
        if self._bg_view:
            # THE STRIP IS DESCRIBING THE OTHER TASK. This is the waiting line
            # - "Auto-rip - waiting for next item..." - and it is exactly what
            # was on screen when somebody pressed the button to see something
            # else. It is not lost: App.repaint_mini rebuilds it from the run's
            # own state the moment the view goes back. See show().
            return
        if self._stalled:
            # whatever the yellow face was about, it is not about this. show()
            # has always done this; idle() relied on its callers to, which held
            # only for as long as every caller remembered.
            self.unstall()
        colour = CLR["err"] if failed else CLR["faint"]
        self.dot.configure(foreground=colour)
        self.bar.set(0)
        self.set_total(None)        # nothing is running; there is no run left
        for w in (self.total_pct, self.item_pct):
            w.configure(text="")
        self.pct.configure(text="", foreground=CLR["text"])
        self.detail.configure(text="")
        self.extra.configure(text="")
        self.info_lbl.configure(text="")
        self.title_lbl.configure(text=text,
                                 foreground=CLR["err"] if failed else CLR["text"])
        # THE BUTTON, IN BOTH SHAPES, AND NO SENTENCE IN EITHER.
        #
        # OBSERVED 31 Aug from a screenshot: the window headline carried
        # "in the background: encoding Rush Hour2 33%" and the taskbar strip
        # beneath it said only "Auto-rip - 2 done - waiting for next item".
        # What that got was the sentence folded into the stage field docked and
        # a line above the title floating - and REPORTED AGAIN 9 Sep, from a
        # screenshot of the docked one, cut off at "searching for the quality
        # of How To Train Y". The film's name is at the end of that sentence
        # and the percentage is not in it at all.
        #
        # `background` is now only asked whether there IS any: the answer to
        # "how far along" is a whole strip's worth of readout, and the button
        # is how it is asked for. The stage field goes back to being the run's
        # own, which is empty while it is waiting.
        self._set_stage("")
        self.set_bg_button(background,
                           self._btn_label(self._btn_room(text)))
        for w in (self.bars, self.pct, self.detail, self.extra, self.info_lbl,
                  self.stage_lbl):
            w.grid_remove()
        # the log stays: it is the window's log, not this rip's, and a large
        # strip with nothing running is a perfectly good thing to watch
        self._rowspan(2)

    def banner(self, text, failed=False):
        """One sentence across the whole strip, until the next repaint.

        For an announcement that is not about the rip the strip is showing - a
        background encode finishing while a disc is still being read. It takes
        the whole width because a footnote does not win a fight with live
        progress: somebody watching a docked strip has no other way to learn
        that the film they were waiting for exists, and "DONE - <film>
        [AV1].mkv was created" squeezed into the one field that also carries
        the read state is not an announcement.

        NOT CLEARED ON A TIMER, which is the other half of what was asked for.
        _flash_step calls repaint_mini() when the blink runs out, and that
        redraws whatever is actually happening - so this lasts exactly as long
        as the flash and the sound it arrives with, then goes. It used to sit
        there for BG_FLASH_SECONDS, which was 25 against a chime of about
        three.
        """
        if self._stalled:
            self.unstall()
        # THE BUTTON STAYS IF IT IS THE WAY BACK. Everything else on the strip
        # gives way to the sentence, but a banner arriving while the strip is
        # turned over to the background job would otherwise take away the only
        # thing on it that can be pressed - and this banner is announcing that
        # very job finishing, which is the moment somebody looks for it.
        self.set_bg_button("")
        self.dot.configure(foreground=CLR["err"] if failed else CLR["ok"])
        self.title_lbl.configure(
            text=text, foreground=CLR["err"] if failed else CLR["ok"])
        self._set_stage("")
        for w in (self.bars, self.pct, self.detail, self.extra,
                  self.info_lbl, self.stage_lbl):
            w.grid_remove()
        self._rowspan(2)

    def flash_seconds(self, failed=False):
        """How long flash() will blink for, in seconds."""
        try:
            return max(0.5, self._flash_steps(failed) * self.FLASH_MS / 1000.0)
        except Exception:                                        # noqa: BLE001
            return 1.0

    def note_only(self, title, stage, message):
        """Say it, and leave the strip alone.

        Between show() and attention(): the strip keeps its colours, keeps its
        bar - which is drawn from the map and still true - and gets one
        sentence where the numbers were. For a state worth reporting that
        nobody has to act on.

        `_stalled` is still set, because that is what stops the next ordinary
        tick from painting over the sentence with a percentage; unstall() puts
        the readouts back.

        AND IT LEAVES THE BACKGROUND VIEW ALONE, which is the one thing here
        that is not about the rip. This is the "reading hard" sentence, the
        state the record says usually gets through on its own - it is not a
        reason to take away the readout somebody deliberately turned the strip
        over to. A wedged drive is; see attention.
        """
        if self.mini_dead() or self._bg_view:
            return False
        self._stalled = True
        self.title_lbl.configure(text=title, foreground=CLR["text"])
        self._set_stage(message if self.docked
                        else (stage or self._stage_text()))
        if not self.docked:
            self.info_lbl.configure(text=message, foreground=CLR["warn"])
            self.info_lbl.grid()
        for w in (self.pct, self.detail, self.extra):
            w.grid_remove()
        return True

    def mini_dead(self):
        try:
            return not self.winfo_exists()
        except tk.TclError:
            return True

    def _stage_text(self):
        try:
            return str(self.stage_lbl.cget("text") or "")
        except tk.TclError:
            return ""

    def attention(self, title, stage, message, colour="warn"):
        """Solid colour, and a sentence where the bar was.

        Two things want this face and they want the same one: a rip that has
        stopped advancing, and a rip that has stopped to ask the user something.
        Both mean come and look, and in both the bar is a lie - so it is not
        dimmed or paused, it is taken away. A progress bar that has stopped
        moving still reads as a rip in progress, and the percentage frozen on it
        is whatever the last thing to finish happened to reach. The disc and the
        stage stay, because they are what says which rip it is.

        Painted through the same routine the finish blink uses, so one piece of
        code knows how to make every surface of the strip one colour - and it
        snapshots the text colours first, so coming out of this puts them back
        rather than guessing at them.

        `colour` is a CLR key. Yellow for everything that means "come and
        look"; red only for the wedge, which means "come and do something".
        One routine for both, so a state that needs the whole strip cannot
        forget one surface of it.

        AND IT TAKES THE BACKGROUND VIEW BACK. This face is only ever a wedged
        drive, a question the run is blocked on, or a shutdown counting down,
        and all three are about the rip: a strip left showing an encode's
        percentage while the drive is off the bus is the five-and-a-half-hour
        wedge again, with the reassuring readout coming from a different film.
        The button comes back with the foreground, so the way over is never
        more than one press away."""
        self._bg_view = False
        self._stalled = True
        # Read where the bar is before taking it away. grid_remove keeps a
        # widget's options for when it comes back, but grid_info stops
        # reporting them - so asked afterwards this returns nothing and the
        # message lands in whatever cell the grid had going spare, which is
        # column zero, under the dot.
        cell = self._bar_cell()
        for w in (self.bars, self.pct, self.detail, self.extra, self.info_lbl):
            w.grid_remove()
        self.title_lbl.configure(text=title)
        if self.docked:
            self._set_stage(stage)
        else:
            # Floating, the stage joins the title - so the field it has of its
            # own when docked has to be emptied on the way past. Left alone it
            # keeps whatever it was last given, and the strip then says the
            # stage twice: once inside the title phrase and once in the label
            # still carrying it. Only visible after undocking mid-rip, which is
            # why it survived: docked and floating each read correctly on their
            # own, and the fault is in the crossing.
            self._set_stage("")
            if stage:
                self.title_lbl.configure(text=f"{title} · {stage}"
                                         if title else stage)
        # cut to the room there is rather than pushing the strip wider: a
        # prompt is written for a dialog box, and some of them are a sentence
        sc = self.app.sc
        room = max(0, (self._gw or 0) - 2 - 2 * int(self.PAD * sc)
                   - int(8 * sc) - (int(90 * sc) if self.docked else 0))
        self.stall_lbl.configure(
            text=self._ellipsize(message, self.app.f_bold, room) or message)
        self.stall_lbl.grid(**cell)
        self._rowspan(1)
        if not self._fg:
            self._fg = {w: w.cget("foreground") for w in self._flash_labels()}
        self._paint_flash(CLR.get(colour, CLR["warn"]))
        return True

    def reading_hard(self, title, stage, minutes):
        """The drive is working a rough patch and nothing has arrived for a while.

        Renamed from `stalled`, and the wording with it, because "No progress
        detected in 4 min" describes the symptom and implies the wrong thing.
        On these discs the drive re-reading a rough patch is usually how it gets
        THROUGH one - measured on this machine, the harder and slower attempts
        are what have been succeeding - so a message that reads as "this is
        broken" invites a Ctrl+C at exactly the wrong moment.

        NO LONGER AMBER, AND NO LONGER WITHOUT A BAR, since 2 Sep. The
        argument for taking the bar away was that a frozen bar reads as a rip
        in progress and the percentage stuck on it is whatever the last thing
        to finish reached - true of a bar that could only be a fraction.

        A bar drawn from the map is not a lie while the drive is stuck. It
        shows what is read, what is dead, what was skipped and where the head
        is, and none of that stops being true because the read in hand is
        taking four minutes. Painting the whole strip yellow for a state the
        drive gets THROUGH more often than not also said "come and look" about
        something nobody needs to do anything about - and the record here is
        that grinding usually pays.

        What is left to say it: this sentence, the "4:20 into a 2.1 MB read"
        note beside it, and the log. What still takes the whole strip is a
        wedged drive, which wants the opposite response.
        """
        # Short, and front-loaded, because the strip has about twenty-one
        # characters of room and ellipsizes past that. The wording it replaces
        # was being cut too - "No progress detected ..." - which is why nobody
        # noticed: the word that mattered happened to fall inside the cut.
        # Everything here has to survive the truncation, so the reassurance
        # that this usually gets through lives in the console line and the
        # spoken one, where there is room for a sentence.
        m = max(1, int(minutes))
        return self.note_only(title, stage, f"Reading hard - {m} min")

    def waiting(self, title, stage, question):
        """The rip has stopped to ask something, and is waiting on an answer."""
        return self.attention(title, stage, f"Waiting for you: {question}")

    def drive_wedged(self, title, stage, minutes=0):
        """The drive has stopped serving data. RED, and the whole strip.

        Was `drive_lost` and was the same yellow as a rough patch and a
        question. Asked for 1 Sep, and the reason is the run of evidence
        behind it: a wedge is the only thing in this project that has ever
        genuinely stopped a rip, and the last one sat for five and a half
        hours saying "still waiting for the drive..." while the strip showed
        the same amber it shows when the drive is working through a scratch.
        Those two want opposite responses - wait, or get up - so they should
        not look alike.

        The full sentence goes on wherever there is room for it, and it is
        front-loaded so the two words that matter survive the docked strip's
        truncation: "Drive wedged" first, the instruction after. The minutes
        are dropped - how long it has been stuck changes nothing about what to
        do, and every character spent on a clock is a character taken off the
        instruction."""
        return self.attention(title, stage, self.app.dr.DRIVE_WEDGED_MSG,
                              colour="err")

    def unstall(self):
        """Back to a bar, and to the colours it had before."""
        if not self._stalled:
            return False
        self._stalled = False
        self.stall_lbl.grid_remove()
        self._paint_flash(None)
        self._fg = {}
        self._relayout()
        return True

    def _bar_cell(self):
        """Where the bar sits, so the stall message can sit there instead."""
        gi = dict(self.bars.grid_info())
        keep = ("row", "column", "columnspan", "rowspan", "sticky", "padx",
                "pady")
        return {k: gi[k] for k in keep if k in gi}

    # What each read state looks like. Deliberately not red: none of these is
    # an error - a skipped stretch and a long read are both the sweep working.
    #
    # "grinding" is deliberately absent, and the absence is the point. It is
    # what backtrack and the retries set: phases that ask sectors which have
    # already refused once, where a read taking eleven seconds is the phase
    # doing its job. Amber for each of those - for hours - said the drive was
    # in trouble when nothing was wrong. The note beside the bar still says
    # what is happening; only the alarm is withheld.
    # ...WITH ONE EXCEPTION, added 1 Sep. "wedged" is not the sweep working:
    # the drive has stopped serving data, nothing whatever will happen until
    # somebody unplugs the enclosure, and on the evidence of this project it is
    # the only failure that genuinely stops a rip. Red, and the whole strip
    # goes with it - see drive_wedged.
    STATE_TINT = {"waiting": "warn", "struggling": "warn", "skipped": "muted",
                  "wedged": "err"}

    def set_dock_numbers(self, elapsed="", eta="", rate=""):
        """The three readouts the docked strip stacks beside the bar.

        Kept apart from `extra` - which is the joined sentence the floating
        shapes have a line for - because a single label cannot be in two grid
        rows at once, and the whole point of the two-row shape is that these
        sit in pairs. See _dock_layout.
        """
        for w, t in ((self.dock_elapsed, elapsed), (self.dock_eta, eta),
                     (self.dock_rate, rate)):
            try:
                w.configure(text=str(t or ""))
            except tk.TclError:
                pass

    def show(self, title, frac, pct, detail, extra="", info="", stage="",
             total=None, state="", bg_note="", cells="", head=None,
             elapsed="", rate="", bg=False):
        """Fill in whatever this size has room for; the rest is simply not
        gridded, so nothing has to be truncated to fit.

        Docked, the stage is a field of its own. Floating it joins the title,
        which is where it has always been and where there is room for it.

        `bg_note` is asked one question - is there any background work - and
        the answer decides whether the view button is on the line. `bg` says
        these numbers ARE the background job's, which is the one caller
        allowed to paint while the strip is turned over to it."""
        if self._bg_view and not bg:
            # THE FOREGROUND'S OWN REPORTS ARE REFUSED while the strip is
            # describing the other task, which is the whole of what "shows the
            # background task progress" has to mean: a rip reports several
            # times a second and would paint over it before the button had
            # finished being pressed. Nothing is lost - App.repaint_mini
            # rebuilds the foreground from the run's own state on the way
            # back. What is NOT refused is a wedged drive or a question, both
            # of which take the strip and the view with it; see attention().
            return
        if self._stalled:
            self.unstall()
        # THE BAR IS VIOLET WHEN IT IS THE OTHER TASK'S, for the reason given
        # further down about the dot, the title and the percentage: the strip
        # has one bar, and a bar drawn from a film that is not in the drive
        # must not be the colour a rip is. A read state cannot apply to an
        # encode, so nothing is lost by taking the tint.
        self.bar.tint(CLR["bg_job"] if bg
                      else (CLR[self.STATE_TINT[state]]
                            if state in self.STATE_TINT else None))
        show = self._shows()
        # WHERE THE OTHER TASK IS ANNOUNCED, and it is a button rather than a
        # sentence - see set_bg_button, and view_btn for the screenshot that
        # asked for it. The sentence used to go in the stage field docked,
        # measured last and clipped first, and REPORTED TWICE for being
        # invisible (1 Sep, gated on the read state, which is "" for the whole
        # of a healthy rip) and once for being unreadable (9 Sep, cut off
        # before the film's name).
        #
        # It still has the lowest claim on the line: the phrase is the rip's
        # own, and _dock_fit prices the button first only so that it is the
        # thing that goes when the strip is too narrow for all three.
        _plain = stage                  # the stage, which is all of the stage
        # THE STACKED PAIRS, docked only. `extra` still carries the joined
        # sentence for the floating shapes.
        self.set_dock_numbers(elapsed if self.docked else "",
                              extra if self.docked else "",
                              rate if self.docked else "")
        if self.docked:
            _nums = ((pct if show["pct"] else "", self.app.f_numb),
                     (detail if show["detail"] else "", self.app.f_small),
                     (extra if show["extra"] else "", self.app.f_small))
            # THE WORDING IS CHOSEN BEFORE THE PHRASE IS CUT, and its width is
            # then reserved like the note's was: it sits on the phrase's line,
            # so the film's name gives way to it. A button is drawn whole or
            # not at all, so the choosing is a ladder rather than a clip.
            _btn = (self._btn_label(self._dock_note_room(
                        title, "" if self._bg_view else _plain, _nums))
                    if (bg_note or self._bg_view) else "")
            _title, _fitted, _note = self._dock_fit(
                title, _plain, _nums, _btn, self._btn_pad(),
                note_first=self._bg_view)
            title, stage = _title, _fitted
            self._plain_stage = stage
            self._set_stage(self._stage_with_ends(stage))
            self.set_bg_button(bg_note, _note)
        else:
            self._plain_stage = ""
            # FLOATING THE STAGE IS PART OF THE TITLE, so the endpoints go
            # there too rather than into a field that is not drawn.
            self._set_stage("")         # see unstall: floating, the stage is
            if stage:                   # part of the title, so its own field
                title = f"{title} · {stage}" if title else stage
            _ends = self._stage_with_ends("")
            if _ends:
                title = f"{_ends} · {title}" if title else _ends
            # AND NO BUTTON WHILE THE BLOCK IS UP. Detached and tall enough,
            # the background job has a line and a bar of its own further down
            # the strip - see set_bg_block - and a button offering to show
            # what is already on screen is a button that does nothing.
            #
            # Measured against the ASSEMBLED title, which floating is the
            # phrase entire: measuring it before the stage was joined on read
            # the room as whatever the film's name left, and the stage is the
            # longer half of that line.
            # `show["bg"]` rather than shows_bg(), which would re-run _shows -
            # font measurements and a stage-list width - a second time on a
            # path that runs on every progress tick.
            self.set_bg_button(
                "" if show.get("bg") else bg_note,
                self._btn_label(self._btn_room(title)))
        if frac is not None and frac < 0.999:
            self.set_done(False)        # moving again: the next disc, or a
                                        # later stage of this one
        # VIOLET WHEN THESE ARE THE OTHER TASK'S NUMBERS, on the dot, the
        # title and the percentage. Not decoration: the strip has one bar and
        # one set of readouts, and while they are describing a film that is
        # not in the drive the one thing they must not look like is a rip.
        # Violet is what this app already means by "the other process" - see
        # the note above view_btn.
        _ink = CLR["bg_job"] if bg else None
        self.dot.configure(foreground=_ink or CLR["accent"])
        self.pct.configure(foreground=_ink or CLR["text"])
        self.title_lbl.configure(text=title, foreground=_ink or CLR["text"])
        # A bar that has arrived stays arrived. Emptying it on the next tick
        # without a percentage - which is what the end of a stage sends - left a
        # green bar sitting at nothing, which reads as the opposite of finished.
        # THE PICTURE FIRST, because it overrides the fraction: a bar with a
        # map is drawn from the map, and set() only decides how far a plain
        # fill reaches. An encode has no map and keeps the plain fill.
        self.bar.set_cells(cells)
        # A KEY, but only for a bar that has colours to explain. An encode's
        # plain fill needs no legend and would be worse for having one.
        self.set_legend(bool(cells))
        # AND WHERE THE DRIVE IS, when that is not already obvious. See
        # head_frac: in the plain sweep it is the edge of the green and the
        # caret would only repeat it.
        self.bar.set_head(head)
        if frac is not None:
            self.bar.set(frac)
        elif not self._done:
            self.bar.set(0)
        self.set_total(total)
        self._paint_pcts(pct, total, show)
        self.pct.configure(text=pct)
        if self._merge_extra and extra:
            # no line spare for it, so it joins the one above rather than being
            # pinned to the right of a row of left-aligned text
            detail = f"{detail}  ·  {extra}" if detail else extra
        self.detail.configure(text=detail)
        self.extra.configure(text=extra)
        self.info_lbl.configure(text=info)
        self._rowspan(1)
        self.bars.grid()
        for w, on in ((self.pct, show["pct"] and pct and not self._has_total),
                      (self.detail, show["detail"] and detail),
                      (self.extra, show["extra"] and extra
                       and not self._merge_extra
                       and not getattr(self, "_stacked", False)),
                      (self.info_lbl, show["info"] and info)):
            w.grid() if on else w.grid_remove()

    def own_taskbar(self):
        """Have the window manager keep the strip above the taskbar, instead of
        watching for it to be buried and undoing it afterwards.

        Windows guarantees that an owned window is always above its owner, and
        maintains that itself on every z-order change - so with the taskbar as
        the strip's owner, being drawn over by it stops being possible rather
        than being something to notice and correct a fifth of a second later.
        That correction was visible, and being visible is the whole complaint:
        the strip blinked out and came back on every window switch.

        Measured both ways on the running app, by raising the taskbar to the top
        of the topmost band, which is what buries it: unowned, the strip lost the
        point for about 60ms every single time; owned, not once in thirty tries.

        Setting an owner through GWLP_HWNDPARENT is a hack - CreateWindowEx is
        the sanctioned route and Tk makes that call, not us - so nothing depends
        on it. If it does not take, _buried below carries on healing the strip
        several times a second, exactly as before."""
        self._owned = False
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            tray = u32.FindWindowW("Shell_TrayWnd", None)
            hwnd_id = self.hwnd()
            if not tray or not hwnd_id:
                return False
            get = getattr(u32, "GetWindowLongPtrW", None) or u32.GetWindowLongW
            setter = getattr(u32, "SetWindowLongPtrW", None) or u32.SetWindowLongW
            for fn in (get, setter):
                fn.restype = ctypes.c_longlong
            get.argtypes = [wintypes.HWND, ctypes.c_int]
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
            hwnd = wintypes.HWND(hwnd_id)
            if get(hwnd, -8) != tray:           # GWLP_HWNDPARENT: the owner
                setter(hwnd, -8, tray)
            self._owned = get(hwnd, -8) == tray
        except Exception:
            self._owned = False
        return self._owned

    def orphaned(self):
        """True once Windows has destroyed the strip out from under Tk.

        The other half of being owned: an owned window is destroyed along with
        its owner, so restarting Explorer takes the strip with the taskbar. Tk
        goes on believing the widget exists, so the app has to ask Windows."""
        try:
            if not self.winfo_exists():
                return False            # already gone as far as Tk knows
        except tk.TclError:
            return False
        if not self._owned:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            hwnd_id = self.hwnd()
            return not (hwnd_id and ctypes.windll.user32.IsWindow(
                wintypes.HWND(hwnd_id)))
        except Exception:
            return False

    def _buried(self, hwnd_id):
        """True when the taskbar is what Windows draws where the strip is.

        Asked by hit-testing the strip's own middle, because every other measure
        lies here. Watched live while the strip was invisible on screen, it
        reported itself visible, uncloaked, full alpha, on the current virtual
        desktop and ahead of Shell_TrayWnd in the z-order chain, the whole time.
        The one answer that changed was 'which window is at this point', which
        went from the strip to the taskbar - so that is the question this asks.

        Only the taskbar counts as burying it. Something that is legitimately in
        front - a full-screen video, say - is left where the user put it rather
        than fought with five times a second."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.WindowFromPoint.restype = wintypes.HWND
            u32.WindowFromPoint.argtypes = [wintypes.POINT]
            u32.GetAncestor.restype = wintypes.HWND
            u32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
            hit = u32.WindowFromPoint(
                wintypes.POINT(self._gx + self._gw // 2,
                               self._gy + self._gh // 2))
            if not hit or hit == hwnd_id:
                return False
            root = u32.GetAncestor(wintypes.HWND(hit), 2)    # GA_ROOT
            if not root or root == hwnd_id:
                return False
            return any(root == u32.FindWindowW(name, None)
                       for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"))
        except Exception:
            return False

    # -- getting out of the way of a full-screen app ----------------------
    @staticmethod
    def _covers(rc, mon):
        """Does a window's rectangle fill a display's, corner to corner.

        Exact rather than approximate, on purpose. A maximised window comes
        close - Windows lets its border hang off the top and the sides - but it
        stops at the work area, so the edge the taskbar is on falls short by the
        height of the taskbar and this says no. That is the line worth holding:
        maximised is not full screen, and a strip that lives in the taskbar has
        every right to sit over a maximised window."""
        return (rc[0] <= mon[0] and rc[1] <= mon[1]
                and rc[2] >= mon[2] and rc[3] >= mon[3])

    def _front_rect(self):
        """The window in front and the display the strip is on, as two
        rectangles and whether that window is merely maximised - or None when
        whatever is in front cannot be in the way.

        Our own windows never count, or the strip would take itself down every
        time the main window came forward. Nor does the shell: the desktop fills
        the display by definition, and so does the taskbar the strip is docked
        inside."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.GetForegroundWindow.restype = wintypes.HWND
            u32.MonitorFromWindow.restype = wintypes.HANDLE
            u32.MonitorFromWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
            fg = u32.GetForegroundWindow()
            mine = self._hwnd_id
            if not fg or not mine or fg == mine:
                return None
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(wintypes.HWND(fg), ctypes.byref(pid))
            if pid.value == os.getpid():
                return None
            name = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(wintypes.HWND(fg), name, 64)
            if name.value in ("Progman", "WorkerW", "Shell_TrayWnd",
                              "Shell_SecondaryTrayWnd"):
                return None
            rc = wintypes.RECT()
            if not u32.GetWindowRect(wintypes.HWND(fg), ctypes.byref(rc)):
                return None

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            mon = u32.MonitorFromWindow(wintypes.HWND(mine), 2)  # TONEAREST
            if not mon:
                return None
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not u32.GetMonitorInfoW(wintypes.HANDLE(mon), ctypes.byref(mi)):
                return None
            m = mi.rcMonitor
            return ((rc.left, rc.top, rc.right, rc.bottom),
                    (m.left, m.top, m.right, m.bottom),
                    bool(u32.IsZoomed(wintypes.HWND(fg))))
        except Exception:
            return None

    def _shell_busy(self):
        """What the shell says about interrupting right now, or 0 if it will
        not say. SHQueryUserNotificationState is the documented question, and
        the only one that can answer for a Direct3D app in exclusive mode: that
        one has taken the display itself, and there may be no ordinary window
        left to measure."""
        try:
            import ctypes
            state = ctypes.c_int(0)
            if ctypes.windll.shell32.SHQueryUserNotificationState(
                    ctypes.byref(state)) == 0:
                return int(state.value)
        except Exception:
            pass
        return 0

    def _fullscreen_over(self):
        """True when something is running full screen on the strip's display.

        Filling the display is not enough on its own, because a maximised window
        can fill it too. Measured here: maximised, Chrome sat at
        (-11, -11, 2571, 1539) on a 2560x1600 display - over the top and both
        sides, and short only along the bottom, where the taskbar is. Turn on an
        auto-hiding taskbar and that last edge goes as well, and maximised
        becomes indistinguishable from full screen by rectangle alone. So the
        window is asked whether it is merely maximised, which full screen is
        not: a browser drops out of the maximised state before it resizes itself
        to the display, and a game never enters it.

        The shell is asked as well, and can override that: QUNS_BUSY is its own
        answer to 'a full-screen application is running', which covers anything
        that goes full screen while keeping the maximised flag, and
        QUNS_RUNNING_D3D_FULL_SCREEN stands alone because in that case there may
        be nothing to measure at all.

        Asked per display deliberately. A film full screen on one monitor is no
        reason to take the strip off another."""
        busy = self._shell_busy()
        front = self._front_rect()
        if front is not None:
            rc, mon, zoomed = front
            if self._covers(rc, mon) and (busy in (2, 3) or not zoomed):
                return True
        return busy == 3

    def _eclipse(self, hide):
        """Take the strip off screen while something else owns the display, and
        put it back afterwards.

        Through Tk, not through ShowWindow, and that is not a stylistic
        preference. Hiding a Tk window from ctypes runs Tk's own window
        procedure inside the foreign call - and the window procedure dispatches
        the <Unmap> binding a few lines up this file, which is a Python callback
        being entered from Tcl on a thread whose interpreter state ctypes has
        just swapped out. Measured on a mapped strip: it did not raise, it
        killed the process outright with "PyEval_RestoreThread: the GIL is
        released", on the first hide, every time. Nothing about that is specific
        to this window - it is what happens to any Tk toplevel hidden behind
        Tk's back.

        Coming back it asks for the top of the z-order again, because a window
        hidden while a full-screen app was in front does not necessarily return
        above what arrived while it was away."""
        try:
            if hide:
                self.withdraw()
            else:
                self.deiconify()
                self.reassert()
        except tk.TclError:
            return False
        return True

    def _check_eclipse(self, *_a):
        """Match the strip to what is on the display now.

        Nothing happens unless the answer has changed, so this is safe to call
        as often as the hooks fire. It stands aside while the strip is already
        down with an auto-hiding taskbar: that is the same window hidden for a
        different reason, and two of them tugging at it would flicker."""
        if self.app._closing or self._hidden_with_taskbar:
            return False
        want = self._fullscreen_over()
        if want == self._eclipsed:
            return False
        self._eclipsed = want
        self._eclipse(want)
        return True

    def _hook_front(self):
        """Be told when the display changes hands, instead of asking.

        EVENT_SYSTEM_FOREGROUND covers switching to something full screen and
        away from it again. It does not cover the window already in front going
        full screen where it stands, which is exactly what pressing the button
        on a video does - so the thread that owns that window is watched too,
        for the moment its rectangle changes. Scoped to that one thread on
        purpose: the same event listened for across the machine is every window
        that moves anywhere, all day.

        Both are WINEVENT_OUTOFCONTEXT, so nothing of ours is loaded into anyone
        else's process - the events queue to this thread and are handed over by
        the message pump Tk is already running. If neither can be arranged, the
        clock tick in _watch finds it anyway, a second later."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            self._proto = proto = ctypes.WINFUNCTYPE(
                None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
            u32.SetWinEventHook.restype = wintypes.HANDLE
            u32.SetWinEventHook.argtypes = [
                wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proto,
                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            self._hook_cb = proto(self._on_front)
            _WIN_EVENT_CALLBACKS.append(self._hook_cb)
            self._hook = u32.SetWinEventHook(
                0x0003, 0x0003, None, self._hook_cb, 0, 0, 0x0002)
            self._watch_front()
        except Exception:
            self._hook = None
        return bool(self._hook)

    def _on_front(self, *_a):
        """Something else came forward. Note it; _glance acts on it."""
        self._look = self._refront = True

    def _on_front_moved(self, _hook, _event, _hwnd, obj, child, *_a):
        if obj or child:
            return              # the mouse pointer or a caret, not the window
        self._look = True

    def _glance(self):
        """The hand-off from the hooks to the main loop.

        A WinEvent callback arrives from inside the message pump, on a thread
        that is already part-way through a foreign call, and Microsoft's advice
        for one is to do as little as it possibly can. So the callbacks set a
        flag and this acts on it. Both halves of the acting are things a
        callback must not do: ShowWindow pumps messages of its own and would
        re-enter the callback underneath itself, and re-aiming a hook from
        inside a hook frees the trampoline the machine is standing on.

        Fifty milliseconds is the hand-off, not the detection. The hooks decide
        *when* something happened and this only decides how soon afterwards it is
        acted on - so the rate costs one boolean read, not a round of Win32
        questions, and asking more often would buy nothing but the difference
        between instant and instant."""
        try:
            if not self.winfo_exists() or self.app._closing:
                return
        except tk.TclError:
            return
        if self._look:
            self._look = False
            self._check_eclipse()
        try:
            self.after(50, self._glance)
        except tk.TclError:
            pass

    def _watch_front(self):
        """Point the size watch at whatever is in front now.

        Called from the clock tick and from nowhere else - see _glance."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            if self._front_hook:
                u32.UnhookWinEvent(wintypes.HANDLE(self._front_hook))
            self._front_hook = self._front_cb = None
            if self._proto is None:
                return False
            u32.GetForegroundWindow.restype = wintypes.HWND
            fg = u32.GetForegroundWindow()
            if not fg:
                return False
            pid = wintypes.DWORD()
            tid = u32.GetWindowThreadProcessId(wintypes.HWND(fg),
                                               ctypes.byref(pid))
            if not tid or pid.value == os.getpid():
                return False    # our own window is never what is in the way
            self._front_cb = self._proto(self._on_front_moved)
            _WIN_EVENT_CALLBACKS.append(self._front_cb)
            self._front_hook = u32.SetWinEventHook(
                0x800B, 0x800B, None, self._front_cb, pid.value, tid, 0x0002)
        except Exception:
            self._front_hook = None
        return bool(self._front_hook)

    def _unhook(self):
        """Give the hooks back. They outlive the window otherwise, and one that
        fires into a destroyed strip is a crash rather than a mistake."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            for h in (self._hook, self._front_hook):
                if h:
                    u32.UnhookWinEvent(wintypes.HANDLE(h))
        except Exception:
            pass
        self._hook = self._front_hook = None
        self._hook_cb = self._front_cb = None

    def destroy(self):
        self._unhook()
        super().destroy()

    def reassert(self):
        """Put the strip back if something took it off screen or buried it.

        Two different failures, and the first one on its own was not enough. A
        strip hidden outright needs showing; a strip the taskbar has been drawn
        over is by every Win32 measure perfectly fine, and needs lifting.

        On the wrapper, not winfo_id(): showing a child window that Windows has
        already hidden the parent of achieves nothing, and asking for topmost
        z-order on a child is meaningless. Aimed at the child, as it was, this
        did nothing whatsoever.

        Cheap on the common path - one IsWindowVisible, one hit test - and the
        z-order is only touched when there was something to fix, because lifting
        a window that is already up would put it over every other always-on-top
        window several times a second for no reason. SW_SHOWNOACTIVATE rather
        than deiconify, deliberately: it must never take focus off whatever the
        user is actually typing into."""
        try:
            if (not self.winfo_exists() or self._hidden_with_taskbar
                    or self._eclipsed):
                return False        # down on purpose; leave it down
            hwnd_id = self.hwnd()
        except tk.TclError:
            return False
        if not hwnd_id:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            hwnd = wintypes.HWND(hwnd_id)
            if not u32.IsWindowVisible(hwnd):
                u32.ShowWindow(hwnd, 4)             # SW_SHOWNOACTIVATE
            elif not self._buried(hwnd_id):
                self._raises = 0
                return False
            if not self._wants_top():
                # An ordinary window is ALLOWED to be behind something, and
                # lifting it here is precisely the behaviour that was switched
                # off. It is still un-hidden above: hidden is not covered.
                return False
            u32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT]
            u32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                             0x0001 | 0x0002 | 0x0010)   # NOSIZE|NOMOVE|NOACTIVATE
            self._raises += 1
            return True
        except Exception:
            return False

    def _watch(self):
        """Keep the ownership in place, and heal the strip if it is not.

        The rate follows how much there is to distrust. Owned by the taskbar,
        the z-order is the window manager's problem and this is a once-a-second
        formality - two calls that find nothing to do. Only where that could not
        be arranged does it fall back to looking several times a second, which
        is the arrangement that flickered.

        It backs off anyway if it ever finds itself lifting the strip over and
        over: losing to something persistent is a great deal better than
        strobing at it."""
        try:
            if not self.winfo_exists() or self.app._closing:
                return
        except tk.TclError:
            return
        self._hwnd_id = self.hwnd() or self._hwnd_id
        if self._wants_top():
            self.own_taskbar()      # cheap, and Tk resets it on some wm calls
        else:
            self._owned = False
        if self._refront:
            self._refront = False
            self._watch_front()     # something else is in front; watch that
        self._check_eclipse()       # in case the hooks were never arranged
        self.reassert()
        try:
            self.after(1000 if (self._owned or self._raises > 20)
                       else self.WATCH_MS, self._watch)
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------


def fit_to_screen(win, near=None, margin=90):
    """Place a window so it is entirely on the screen, however tall it wants.

    A Toplevel sizes itself to whatever it holds, and Tk will happily make that
    taller than the display - at which point the buttons at the bottom are
    simply unreachable. That is not hypothetical: the auto-rip window grew past
    the bottom of the screen and there was no way to start an auto-rip.

    So: cap the height at what the screen has, and pull the window back up if
    placing it near its parent would push it off. Whatever will not fit is
    content, and the footer is packed bottom-first so it is never the thing
    that goes.
    """
    try:
        win.update_idletasks()
        w = win.winfo_reqwidth()
        h = win.winfo_reqheight()
        sh = win.winfo_screenheight()
        top = (near.winfo_rooty() + 40) if near is not None else 40
        h = min(h, max(240, sh - margin))
        if top + h + margin // 2 > sh:
            top = max(0, sh - h - margin // 2)
        left = (near.winfo_rootx() + 70) if near is not None else 70
        left = max(0, min(left, max(0, win.winfo_screenwidth() - w)))
        win.geometry(f"{w}x{h}+{int(left)}+{int(top)}")
    except tk.TclError:
        pass


def head_frac(info, cells):
    """Where to draw the head caret, or None to draw none.

    ONLY WHERE THE HEAD AND THE FRONTIER ARE DIFFERENT PLACES. The plain
    forward sweep reads in order, so the edge of the green IS the head and a
    caret on it is the same fact twice. Everything else moves about: the
    second look marches BACKWARDS in 32 MB pieces from the far end of a
    skipped stretch, the probe fill jumps between the gaps a probe bracketed,
    and the retry phases go wherever the map says is still bad.

    Asked for by name on 2 Sep, and it is what the note beside the caret had
    claimed all along while the condition drew it everywhere.
    """
    frac = info.get("frac")
    if not cells or frac is None:
        # No map means a plain fill, whose own edge is the position - and an
        # encode has no head on a disc to speak of.
        return None
    if str(info.get("label") or "").strip().lower() == "sweep":
        return None
    return frac


def step_tail(info):
    """" · 4/6", or "" when the job is not made of numbered steps.

    A DOT, NOT A HYPHEN, since 2 Sep. The strip's line already spends a hyphen
    separating the disc from what is happening to it; a second one in front of
    the step read as though the two marks meant the same kind of break, when
    one divides the subject from the predicate and the other joins three
    equal facts about the predicate.

    AND A FRACTION, NOT A SENTENCE, since 3 Sep. "step 2 of 6" is eleven
    characters to say what "2/6" says in three, on the one line of the docked
    strip that also has to hold the film's name and what is being done to it -
    and the film's name is what was being dropped to make room. Nothing is
    lost: a fraction beside a stage name cannot be mistaken for anything else.
    """
    steps = info.get("steps") or 0
    if not steps:
        return ""
    return f" · {info.get('step') or 0}/{steps}"


# What each phase is called while it is happening. Asked for by name: the
# strip said "sweep", which is the phase's identifier rather than a
# description of anything, and beside a live percentage it reads like a
# heading. "sweeping" is what the drive is doing.
#
# The keys are the labels the engine passes to Progress.update, so they are
# the phase names in discripper.PHASE_STATUS. Anything not listed falls
# through unchanged - a label already in the -ing form, or one this table has
# not caught up with, is better said plainly than mangled by a rule.
PROGRESSIVE = {
    "sweep": "sweeping",
    "retrying skipped sections": "looking again",
    "islands": "hunting islands",
    "probe": "probing",
    "probe fill": "filling in",
    "backtrack": "backtracking",
    "retry": "retrying",
    "verify": "verifying",
    "scan": "scanning",
    "encode": "encoding",
    "remux": "remuxing",
    "checksum": "checksumming",
}


def progressive(label):
    """"sweep" -> "sweeping". Presentation only.

    NOT applied to the label the engine uses. `label` is what PHASE_STATUS is
    keyed on and what the riplog records, and renaming a phase to make a strip
    read better would rename it in the log too - where "sweep" is the right
    word because it is an identifier.
    """
    name = str(label or "").strip()
    if not name:
        return ""
    got = PROGRESSIVE.get(name.lower())
    if got:
        return got
    # "retry 1", "retry 2" - numbered phases keep their number
    low = name.lower()
    for key, word in PROGRESSIVE.items():
        if low.startswith(key + " "):
            return word + name[len(key):]
    if low.startswith("retry"):
        return "retrying" + name[5:]
    return name


class OptionRows:
    """A column of label-and-control rows, each bound straight to a setting.

    Every control writes through the window's _set_and_save, so what is on
    screen here and what is on the settings tab are the same value in the same
    variable - there is no apply button and nothing to get out of step."""

    def __init__(self, app, body, width=520):
        self.app, self.body, self.r = app, body, 0
        self.width = width
        body.columnconfigure(1, weight=1)

    def _label(self, text, tip):
        lab = ttk.Label(self.body, text=text, style="Card.TLabel")
        lab.grid(row=self.r, column=0, sticky="w", padx=(0, 12), pady=3)
        if tip:
            Tip(lab, tip)
        return lab

    def _note(self, text):
        if not text:
            return
        ttk.Label(self.body, text=text, style="Muted.TLabel", justify="left",
                  wraplength=int(self.width * self.app.sc)).grid(
            row=self.r, column=1, sticky="w", padx=(10, 0), pady=3)

    def choice(self, text, dotted, values=None, tip="", note=""):
        var = self.app.shared_var(dotted, tk.StringVar)
        self._label(text, tip or self.app.dr.SETTING_HELP.get(dotted, ""))
        box = ttk.Combobox(self.body, textvariable=var, state="readonly",
                           width=12,
                           values=[str(v) for v in
                                   (values
                                    or choices_for(self.app.dr, dotted)
                                    or [])])
        box.grid(row=self.r, column=1, sticky="w")
        box.bind("<<ComboboxSelected>>",
                 lambda e: self.app._set_and_save(dotted, var.get()))
        self.r += 1
        if note:
            self._note(note)
            self.r += 1
        return box

    def number(self, text, dotted, suffix="", tip="", width=6):
        var = self.app.shared_var(dotted, tk.StringVar)
        self._label(text, tip or self.app.dr.SETTING_HELP.get(dotted, ""))
        holder = ttk.Frame(self.body, style="Card.TFrame")
        holder.grid(row=self.r, column=1, sticky="w")
        ent = ttk.Entry(holder, textvariable=var, width=width)
        ent.pack(side="left")
        for seq in ("<FocusOut>", "<Return>"):
            ent.bind(seq, lambda e: self.app._set_and_save(dotted, var.get()))
        if suffix:
            ttk.Label(holder, text="  " + suffix,
                      style="Muted.TLabel").pack(side="left")
        self.r += 1
        return ent

    def flag(self, text, dotted, tip="", parent=None, side=None):
        var = self.app.shared_var(dotted, tk.BooleanVar)
        cb = ttk.Checkbutton(
            parent if parent is not None else self.body,
            text=text, variable=var, style=self.app.cb_style,
            command=lambda: self.app._set_and_save(dotted, var.get()))
        if parent is not None:
            cb.pack(side=side or "left", padx=(0, 0))
        else:
            cb.grid(row=self.r, column=0, columnspan=2, sticky="w", pady=2)
            self.r += 1
        Tip(cb, tip or self.app.dr.SETTING_HELP.get(dotted, ""))
        return cb

    def flag_pair(self, left, right, gap=18):
        """Two ticks on one row.

        ASKED FOR, 31 Aug, three times over: these pairs are each one decision
        asked twice - what to take off the disc, and when to hand the machine
        back - and a column of single ticks made the auto-rip window taller
        than the screen it has to fit on. See the note on `foot` in AutoWindow
        for what happened the last time this window outgrew its display.
        """
        holder = ttk.Frame(self.body, style="Card.TFrame")
        holder.grid(row=self.r, column=0, columnspan=2, sticky="w", pady=2)
        self.r += 1
        out = []
        for i, spec in enumerate((left, right)):
            if not spec:
                continue
            text, dotted = spec[0], spec[1]
            tip = spec[2] if len(spec) > 2 else ""
            cb = self.flag(text, dotted, tip, parent=holder)
            if i and out:
                cb.pack_configure(padx=(int(gap * self.app.sc), 0))
            out.append(cb)
        return out

    def order(self, text, dotted, stages, tip="", height=5):
        """A list of named stages that can be reordered and switched off.

        The thing this replaces was a fixed ladder with a five-minute human
        pause nailed to the front of it, which on an unattended run reads as a
        dead five minutes before anything useful happens. Which rungs are worth
        their cost is not a fact about the software - it depends on the disc
        and on whether anybody is in the room - so it belongs here, in the
        window that comes up before you walk away.

        Off rungs stay in the list, greyed and at the bottom, because "what
        else could this do?" is a question the list should answer.
        """
        dr = self.app.dr
        labels = {k: lab for k, lab, _d in stages}
        helps = {k: d for k, _lab, d in stages}
        self._label(text, tip)
        holder = ttk.Frame(self.body, style="Card.TFrame")
        holder.grid(row=self.r, column=1, sticky="we")
        lb = tk.Listbox(holder, height=height, activestyle="none",
                        exportselection=False,
                        width=42, borderwidth=1, relief="solid",
                        background=CLR["field"], foreground=CLR["text"],
                        selectbackground=CLR["accent"],
                        selectforeground=CLR["card"],
                        highlightthickness=0)
        lb.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(holder, style="Card.TFrame")
        side.pack(side="left", fill="y", padx=(8, 0))

        state = {"on": [], "off": []}

        def load():
            on = [k for k in dr.fallback_order(self.app.settings)
                  if k in labels]
            state["on"] = on
            state["off"] = [k for k, _l, _d in stages if k not in on]
            paint()

        def paint():
            keep = lb.curselection()
            lb.delete(0, "end")
            for i, k in enumerate(state["on"], start=1):
                lb.insert("end", f"{i}.  {labels[k]}")
            for k in state["off"]:
                lb.insert("end", f"-   {labels[k]}   (off)")
            for i in range(len(state["on"]), lb.size()):
                lb.itemconfigure(i, foreground=CLR["muted"])
            if keep:
                try:
                    lb.selection_set(min(keep[0], lb.size() - 1))
                except tk.TclError:
                    pass

        def picked():
            sel = lb.curselection()
            if not sel:
                return None, None
            i = sel[0]
            if i < len(state["on"]):
                return "on", i
            return "off", i - len(state["on"])

        def save():
            self.app._set_and_save(dotted, list(state["on"]))
            paint()

        def move(step):
            where, i = picked()
            if where != "on":
                return
            j = i + step
            if not 0 <= j < len(state["on"]):
                return
            state["on"][i], state["on"][j] = state["on"][j], state["on"][i]
            save()
            lb.selection_clear(0, "end")
            lb.selection_set(j)

        def toggle():
            where, i = picked()
            if where == "on":
                state["off"].insert(0, state["on"].pop(i))
            elif where == "off":
                state["on"].append(state["off"].pop(i))
            else:
                return
            save()

        for lab, cmd in (("Up", lambda: move(-1)), ("Down", lambda: move(1)),
                         ("On / off", toggle)):
            ttk.Button(side, text=lab, width=9, command=cmd).pack(
                anchor="w", pady=1)
        note = ttk.Label(self.body, text="", style="Muted.TLabel",
                         justify="left",
                         wraplength=int(self.width * self.app.sc))
        lb.bind("<<ListboxSelect>>",
                lambda e: note.configure(
                    text=helps.get((picked()[0] and
                                    (state[picked()[0]][picked()[1]])) or "",
                                   "")))
        lb.bind("<Double-Button-1>", lambda e: toggle())
        self.r += 1
        note.grid(row=self.r, column=1, sticky="w", padx=(10, 0), pady=(4, 2))
        self.r += 1
        load()
        return lb

    def text(self, text, dotted, tip="", note="", width=16, flag=None):
        """A free-text row for a setting that is a short list or pattern.

        `flag` is (text, dotted) for a tick that belongs to this control rather
        than to a row of its own - a master switch and the list it works
        through are one decision, and a tick three rows above the thing it
        governs reads as unrelated to it.
        """
        # THE SHARED VARIABLE, not one of its own. A setting can be on screen
        # in more than one place - this row and its row on the settings tab -
        # and two variables for one setting means typing in one and watching
        # the other go on showing the old value until a restart.
        var = self.app.shared_var(dotted, tk.StringVar)
        self._label(text, tip or self.app.dr.SETTING_HELP.get(dotted, ""))
        holder = ttk.Frame(self.body, style="Card.TFrame")
        holder.grid(row=self.r, column=1, sticky="w")
        ent = ttk.Entry(holder, textvariable=var, width=width)
        ent.pack(side="left")
        for seq in ("<FocusOut>", "<Return>"):
            ent.bind(seq, lambda e: self.app._set_and_save(dotted, var.get()))
        if flag:
            cb = self.flag(flag[0], flag[1],
                           flag[2] if len(flag) > 2 else "", parent=holder)
            cb.pack_configure(padx=(int(14 * self.app.sc), 0))
        self.r += 1
        if note:
            self._note(note)
            self.r += 1
        return ent

    def line(self, text, style="Muted.TLabel"):
        lab = ttk.Label(self.body, text=text, style=style, justify="left",
                        wraplength=int((self.width + 140) * self.app.sc))
        lab.grid(row=self.r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.r += 1
        return lab


def language_choices(dr, kind="audio"):
    """Dropdown entries for a language setting, best first.

    Each entry is "<value>  <what it means>", and the value is the first
    whitespace-separated token - so the code is what gets stored and the name is
    only there to be read. A comma-separated value keeps that property, which is
    what lets "everything on this disc" be one entry rather than a hint to go
    and type two codes.

    The disc's own languages come first when a scan has recorded them, because
    the question is always about the disc in the drive and never about the list
    of the world's languages.
    """
    seen = dr.JOB.get("langs") or {}
    here = [c for c in (seen.get(kind) or []) if c]
    out = ["all  every language the disc has"]
    if kind != "audio":
        out.append("none  no subtitles at all")
    if here:
        for c in here:
            out.append(f"{c}  {dr.lang_name(c)} - on the disc in the drive")
        if len(here) > 1:
            out.append(",".join(here) + "  "
                       + " + ".join(dr.lang_name(c) for c in here)
                       + " - everything on this disc")
    for code, name in dr.LANGUAGES:
        if code in here:
            continue            # already offered above, with better wording
        out.append(f"{code}  {name}")
    return out


# What is worth being able to say differently for one folder than for the
# rest. Deliberately not everything on the DVD tab: the ones here are the ones
# that actually differ film to film - a grainy live-action feature wants a
# different quality from a clean animated one, a box set of 4:3 episodes wants
# different framing from a 2.40:1 feature, and a folder you have already watched
# can give up its originals while the rest keep theirs.
def lang_codes(value, same=None):
    """The codes in a language setting, or [] if it is not a list of them.

    "all", "none" and the run's own setting are not languages and cannot be
    part of a list of them.
    """
    text = str(value or "").strip()
    if same and text == str(same):
        return []
    # THE CODE, NOT THE LABEL. Checking the whole string against "all"
    # let "all  every language the disc has" through as a language and
    # produced "eng,jpn,all", which parse_langs reads as EVERY language -
    # so picking `all` after two languages silently kept the two.
    head = text.split()[0].lower() if text.split() else ""
    if not head or head in ("all", "any", "*", "none"):
        return []
    out = []
    for part in text.lower().split(","):
        tok = part.split()
        code = tok[0] if tok else ""
        if code and code not in out:
            out.append(code)
    return out


def lang_toggle(current, picked, same=None):
    """One picked dropdown entry folded into a language list.

    A DROPDOWN THAT REPLACES CANNOT EXPRESS A LIST. Reported 2 Sep from the
    per-item dialog: "it appears that you aren\'t allowed to pick multiple
    audio tracks for encoding". The setting has always been a comma-separated
    list - `parse_langs` splits on commas - and the box has always accepted
    one, but every pick overwrote the box, so keeping two languages meant
    knowing both codes and typing them. Which is the exact thing the picker
    exists to stop anybody having to do.

    So A PICK TOGGLES: picking a language adds it, picking it again drops it.
    "all", "none" and the run\'s own setting are exclusive - they replace the
    list rather than joining it, because they are answers to a different
    question.

    Dropping the last language does not leave an empty list. Empty means
    "keep nothing", and a film with no audio is a fault, so it falls back to
    every language rather than to none.
    """
    ptxt = str(picked).strip()
    if not lang_codes(ptxt, same):
        return ptxt.split()[0] if (ptxt.split() and ptxt != str(same)) \
            else ptxt
    add = lang_codes(ptxt, same)
    cur = lang_codes(current, same)
    if all(c in cur for c in add):
        out = [c for c in cur if c not in add]
    else:
        out = cur + [c for c in add if c not in cur]
    return ",".join(out) if out else "all"


def language_values(dr, kind, current, same=None):
    """language_choices, with whatever is already kept marked as such.

    The marker goes AFTER the code, never in front of it: the stored value is
    the entry\'s first whitespace-separated token, and a tick on the left
    would become the setting.
    """
    have = lang_codes(current, same)
    out = []
    for entry in language_choices(dr, kind):
        tok = entry.split()
        picks = lang_codes(tok[0] if tok else "", same)
        if picks and all(c in have for c in picks):
            entry += "   [keeping - pick again to drop]"
        out.append(entry)
    return out


ITEM_KEYS = [
    # None for the three that are closed sets the engine already declares -
    # see SETTING_CHOICES. quality, hd_quality, max_height and preset stay
    # written out: those are curated shortlists for a small window (preset
    # offers three of nine on purpose), not the whole legal set.
    ("video.vcodec", "Codec", None, False),
    ("video.quality", "Quality (CRF)", ["16", "18", "19", "20", "22", "24"], True),
    ("video.hd_quality", "HD quality", ["18", "20", "22", "24"], True),
    ("video.preset", "Encoder effort", ["medium", "slow", "slower"], True),
    ("video.trim_bars", "Trim black borders", None, False),
    ("video.screen", "Fit to screen", None, True),
    ("video.fit", "How to reach it", None, False),
    ("video.max_height", "Scale down to", ["0", "720", "1080"], True),
    # None, so ItemWindow asks language_choices for them - the disc's own
    # languages belong in this list too, and a literal ["eng", "all"] is the
    # same undiscoverable guess in a smaller window.
    ("video.audio_langs", "Audio languages", None, True),
    ("video.sub_langs", "Subtitle languages", None, True),
]
SAME = "(same as the run)"


class ItemWindow(tk.Toplevel):
    """Settings for one thing on the list, and nothing else.

    Every row starts at "same as the run", and a row left there is not an
    override at all - so what comes out of this is only what somebody
    deliberately said, and the run's own settings go on meaning what they say
    for everything else.
    """

    def __init__(self, owner, target):
        super().__init__(owner)
        self.owner = owner
        self.app = owner.app
        self.dr = owner.dr
        self.target = str(target)
        self.title(f"{self.dr.APP} - settings for this item")
        self.configure(background=CLR["card"])
        self.transient(owner)
        self.protocol("WM_DELETE_WINDOW", self.close)
        cur = dict(owner.overrides.get(self.target) or {})

        card = Card(self, "Just for this one",
                    Path(self.target).name or self.target)
        card.pack(fill="x", padx=12, pady=(12, 8))
        b = card.body
        b.columnconfigure(1, weight=1)
        self.vars = {}
        for r, (dotted, label, values, free) in enumerate(ITEM_KEYS):
            ttk.Label(b, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=3)
            var = tk.StringVar(value=str(cur.get(dotted, SAME))
                               if dotted in cur else SAME)
            self.vars[dotted] = var
            kind = None
            if values is None and dotted in ("video.audio_langs",
                                             "video.sub_langs"):
                # The same named list the settings tab offers, and for the same
                # reason: a code is not guessable and a per-folder override is
                # exactly where somebody reaches for one language of many.
                kind = ("audio" if dotted.endswith("audio_langs")
                        else "subtitle")
                values = language_values(self.dr, kind, var.get(), SAME)
            elif values is None:
                # Whatever the engine says this setting accepts.
                values = choices_for(self.dr, dotted)
            box = ttk.Combobox(b, textvariable=var,
                               width=34 if dotted.endswith("_langs") else 16,
                               state="normal" if free else "readonly",
                               values=[SAME] + [str(x) for x in (values or [])])
            box.grid(row=r, column=1, sticky="w")
            if kind:
                # A PICK TOGGLES HERE TOO - this dialog is where it was
                # reported. Same held-value dance as the settings tab: the
                # selection overwrites the box before the handler sees it.
                held = [var.get()]

                def _open(_e=None, b2=box, k=kind, v=var):
                    held[0] = v.get()
                    b2.configure(values=[SAME] + language_values(
                        self.dr, k, held[0], SAME))

                def _pick(_e=None, v=var):
                    code = lang_toggle(held[0], v.get(), SAME)
                    held[0] = code
                    v.set(code)

                box.bind("<<ComboboxSelected>>", _pick)
                for seq in ("<Button-1>", "<FocusIn>"):
                    box.bind(seq, _open, add="+")
            Tip(box, self.dr.SETTING_HELP.get(dotted, "")
                + f"\n\nThe run uses {self.dr.sget(self.app.settings, dotted)!r}.")

        card = Card(self, "The originals of these ones")
        card.pack(fill="x", padx=12, pady=(0, 8))
        b = card.body
        self.rep_var = tk.StringVar(
            value=str(cur.get("replace")) if "replace" in cur else SAME)
        for text, val in ((SAME, SAME), ("Keep them", "none"),
                          ("Send them to the Recycle Bin", "recycle"),
                          ("Delete them outright", "delete")):
            ttk.Radiobutton(b, text=text, value=val, variable=self.rep_var,
                            style="Card.TRadiobutton").pack(anchor="w")

        foot = ttk.Frame(self, style="Card.TFrame")
        foot.pack(fill="x", padx=12, pady=(4, 14))
        ttk.Button(foot, text="Apply", command=self.apply).pack(side="left")
        ttk.Button(foot, text="Use the run's settings",
                   command=self.reset).pack(side="left", padx=8)
        ttk.Button(foot, text="Cancel", command=self.close).pack(side="right")
        self.update_idletasks()
        self.geometry(f"+{owner.winfo_rootx() + 40}+{owner.winfo_rooty() + 40}")

    def _cast(self, dotted, value):
        """The type the setting is, or None if what was typed is not it."""
        if dotted in ("video.audio_langs", "video.sub_langs"):
            # The dropdown shows "jpn  Japanese - on the disc in the drive" so
            # the language can be recognised; only the code in front of it is
            # the setting. PER COMMA-SEPARATED PART, not once for the whole
            # string - taking the first token of "eng, jpn" gave "eng,".
            txt = str(value).strip()
            if not txt:
                return txt
            codes = lang_codes(txt)
            if codes:
                return ",".join(codes)
            return txt.split()[0] if txt.split() else txt
        default = self.dr.sget(self.dr.DEFAULTS, dotted)
        try:
            if isinstance(default, bool):
                return str(value).strip().lower() in ("1", "true", "yes", "on")
            if isinstance(default, int):
                return int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return str(value).strip()

    def apply(self):
        over, bad = {}, []
        for dotted, _label, _values, _free in ITEM_KEYS:
            txt = str(self.vars[dotted].get()).strip()
            if not txt or txt == SAME:
                continue
            val = self._cast(dotted, txt)
            if val is None:
                bad.append(f"{dotted.split('.')[-1]}: '{txt}' is not a number")
                continue
            over[dotted] = val
        rep = str(self.rep_var.get())
        if rep != SAME:
            over["replace"] = None if rep == "none" else rep
        if bad:
            messagebox.showinfo(self.dr.APP, "\n".join(bad))
            return
        if over:
            self.owner.overrides[self.target] = over
        else:
            self.owner.overrides.pop(self.target, None)
        self.owner.refresh()
        self.close()

    def reset(self):
        self.owner.overrides.pop(self.target, None)
        self.owner.refresh()
        self.close()

    def close(self):
        self.owner.item_win = None
        try:
            self.destroy()
        except tk.TclError:
            pass


def salvage_effort_text(dr, settings, level):
    """What a named salvage level resolves to, as the card's two lines.

    Pure, so it can be checked without opening a window - which is the whole
    reason it is out here. The bug this replaced unpacked four names from a
    five-member row and raised ValueError on every level; the only test that
    covered it looked for the string "_say_effort" near its own def, so 1,756
    tests passed while the card never rendered at all. The same fault had
    already been found and fixed in cmd_salvage on 2 Sep - the guard written
    then scans discripper.py for unpacks of salvage_effort(), and this file
    indexes SALVAGE_EFFORT directly, so it was not looked at.

    Returns "" for an unknown level, which is what the caller already did.
    """
    row = dr.SALVAGE_EFFORT.get(str(level or "").lower())
    if not row:
        return ""
    secs, _grind, passes, mins, why = row
    # THE MINUTES AT EACH BAD PLACE ARE NOT row[3]. row[3] is the re-read
    # clock; the band budget comes from grind_effort_minutes, which honours an
    # explicit video.grind_effort over the level - and this line said
    # retry_effort_minutes(grind) instead, which ignores it. cmd_salvage has
    # always used grind_effort_minutes here, so the two disagreed by design.
    band = dr.grind_effort_minutes(settings)
    dot = f"  {chr(183)}  "
    return (
        f"{why}." + chr(10)
        # The clock still governs the sector walk; the walk is just opt-in now
        # (--passes 3), because the backtrack was removed on 1 Sep. Naming the
        # setting rather than the phase keeps the card true either way.
        + (f"Sector-walk clock {dr.human_time(secs)} a phase"
           if secs else "No sector-walk clock")
        + f"{dot}{band or 'no'} minute(s) at each bad place"
        + f"{dot}{passes} verified re-read pass(es) inside "
        + (dr.human_time(60.0 * mins) if mins else "no clock")
        + ".")


class DiscWindow(tk.Toplevel):
    """One disc, and which way to get it off. The options before the run.

    "Rip this disc" used to start immediately on whatever the tabs happened to
    say, and the one route it knew was: ask MakeMKV for the titles, and if that
    fails, work down the ladder. That is the right default and it is not the
    only thing anybody wants. A disc already known to be damaged should be able
    to go straight to the image, without spending forty minutes proving again
    that a title rip will not work; a disc being archived should be able to ask
    for the image alone; and a stream MakeMKV reads but refuses to write should
    be able to go straight to ffmpeg.

    Same shape as the auto-rip and auto-encode windows, and every control here
    is the same variable as the row on the settings tab, so neither can get out
    of step with the other.
    """

    ROUTES = (
        ("normal", "Rip it normally",
         "Ask MakeMKV for the titles and save them, with the fallback ladder "
         "below if that fails. What the button used to do, and the right "
         "answer for a disc in good condition."),
        ("salvage", "Image it and rebuild the film",
         "Read what the disc WILL give, skip what it will not, measure what "
         "is actually wrong with the result, repair it, and mux around "
         "anything that cannot be repaired. Slower to start and it does not "
         "throw away what it read - which is the whole difference on a "
         "damaged disc. Go straight here when a title rip has already failed "
         "once."),
        ("image", "Just make an image",
         "Read the disc into an .iso with a map beside it saying what was "
         "and was not got, and stop there. Resumable, and a second run - on "
         "another drive - attempts only what is still missing."),
        ("remux", "Mux it with ffmpeg",
         "Skip MakeMKV's muxer entirely and let ffmpeg read the disc "
         "structure. For a stream MakeMKV reads perfectly and then refuses to "
         "write, which it does over timecodes that run backwards."),
        ("backup", "Full decrypted backup",
         "Copy the whole disc as a decrypted BDMV/VIDEO_TS folder. Gets past "
         "BD+ and playlist trouble a title rip cannot; does NOT help with "
         "read errors, because it passes over the same sectors and copies "
         "every extra first."),
    )

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.dr = app.dr
        self.title(f"{self.dr.APP} - rip this disc")
        self.configure(background=CLR["card"])
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.route = tk.StringVar(value="normal")

        card = Card(self, "How to get it off")
        card.pack(fill="x", padx=12, pady=(12, 8))
        for key, label, blurb in self.ROUTES:
            rb = ttk.Radiobutton(card.body, text=label, value=key,
                                 variable=self.route,
                                 style="Card.TRadiobutton",
                                 command=self.refresh)
            rb.pack(anchor="w", pady=(4, 0))
            ttk.Label(card.body, text=blurb, style="Muted.TLabel",
                      justify="left",
                      wraplength=int(560 * app.sc)).pack(anchor="w",
                                                         padx=(24, 0))

        card = Card(self, "What to take off it")
        card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, card.body)
        rows.choice("Titles", "general.auto_titles")
        rows.choice("Naming", "general.auto_naming")
        rows.flag("Leave out extras and menus", "general.feature_only")
        rows.line("On a Blu-ray the film is about 70% of the disc; the rest "
                  "is menus, trailers, deleted scenes and dubs. Which sectors "
                  "those are is read out of the disc's own filesystem and "
                  "playlists, so it needs no scan and costs nothing to work "
                  "out.")
        rows.flag("Automatically balance file size with quality",
                  "video.crf_search")
        self.mode_lbl = rows.line("", "Disc.TLabel")

        # HOW HARD TO TRY, and only for the routes it governs.
        #
        # ASKED FOR 1 Sep: put the salvage effort levels into the "image it and
        # rebuild the film" option. It is shown for "just make an image" as
        # well, because the level drives the IMAGER - the backtrack clock, the
        # band budget and the re-read passes are all inside it - and a setting
        # that silently governs a route while being hidden on it is the kind of
        # thing that reads as not applying.
        self.eff_card = Card(self, "How hard to try")
        self.eff_card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, self.eff_card.body)
        rows.choice("Effort", "video.salvage_effort")
        self.eff_lbl = rows.line("")
        self.eff_var = app.shared_var("video.salvage_effort", tk.StringVar)
        try:
            self.eff_var.trace_add("write", lambda *_a: self._say_effort())
        except AttributeError:                                   # Tk 8.5
            self.eff_var.trace("w", lambda *_a: self._say_effort())

        self.fb_card = Card(self, "If it will not rip")
        self.fb_card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, self.fb_card.body)
        rows.order("In this order", "general.fallback_order",
                   self.dr.FALLBACK_STAGES)
        rows.text("Speeds to fall back through", "video.stall_slow_x",
                  width=14,
                  note="Multiples of 1x, in order. Empty means never cap.",
                  flag=("Read slower in difficult sections",
                        "video.slow_on_stall"))

        # Bottom first, for the same reason as the auto-rip window: a footer
        # packed last is the first thing off the end of a window that has
        # outgrown the screen.
        self.foot = ttk.Frame(self, style="Card.TFrame")
        self.foot.pack(side="bottom", fill="x", padx=12, pady=(4, 14))
        self.btn_start = ttk.Button(self.foot, text="Start",
                                    command=self.start)
        self.btn_start.pack(side="left")
        ttk.Button(self.foot, text="Close",
                   command=self.close).pack(side="right")

        self.update_idletasks()
        fit_to_screen(self, app)
        self.refresh()

    def _say_effort(self):
        """The three numbers the chosen level resolves to, spelled out.

        The same line the salvage prints into the log before it touches the
        disc - see cmd_salvage - because "thorough" on its own does not tell
        anybody what they are agreeing to, and the whole point of the dial is
        that it moves three things at once.
        """
        text = salvage_effort_text(self.dr, self.app.settings,
                                   self.eff_var.get())
        if not text:
            return
        try:
            self.eff_lbl.configure(text=text)
        except tk.TclError:
            pass

    def refresh(self):
        dr = self.dr
        letter = self.app.current_letter()
        v = dict(dr.sget(self.app.settings, "video"))
        route = self.route.get()
        self.mode_lbl.configure(
            text=(f"{letter}:  " if letter else "No drive selected.  ")
            + (f"[{v['vcodec']} q{v['quality']}]" if v.get("compress")
               else "[lossless remux, no re-encode]"))
        # The ladder only applies to the route that has one. Put back
        # BEFORE the buttons, by name - counting pack slaves to find the right
        # neighbour breaks the moment anything else is added to the window.
        try:
            if route == "normal":
                self.fb_card.pack(fill="x", padx=12, pady=(0, 8),
                                  before=self.foot)
            else:
                self.fb_card.pack_forget()
        except tk.TclError:
            pass
        # The effort levels drive the imager, so they belong to the two routes
        # that run it. "Rip it normally" can END here through the ladder, but
        # it is not what that route is FOR, and a window that shows every
        # setting any path might reach is the settings tab.
        try:
            if route in ("salvage", "image"):
                self.eff_card.pack(fill="x", padx=12, pady=(0, 8),
                                   before=self.fb_card if route == "normal"
                                   else self.foot)
                self._say_effort()
            else:
                self.eff_card.pack_forget()
        except tk.TclError:
            pass
        self.btn_start.configure(
            state="disabled" if (self.app.worker
                                 and self.app.worker.is_alive())
            else "normal")

    def start(self):
        app, dr = self.app, self.dr
        if not app.need_out_dir("Ripping a disc"):
            return
        letter = app.current_letter()
        if not letter:
            messagebox.showinfo(dr.APP, "No drive selected.")
            return
        route = self.route.get()
        dr.job_set(number=1, total=1)
        started = False
        if route == "normal":
            started = app._run_bg("Ripping disc", dr.do_rip, app.settings,
                                  letter, rip=True)
        elif route == "salvage":
            ns = dr._Args(drive=letter, out=None, skip="", wait_minutes=30,
                          no_wait=False, feature=None, no_verify=False,
                          no_langs=False, yes=True)
            started = app._run_bg("Salvaging disc", dr.cmd_salvage,
                                  app.settings, ns, rip=True)
        elif route == "image":
            ns = dr._Args(drive=letter, out=None, passes=3,
                          block=dr.read_block_sectors(app.settings),
                          tries=3, minutes=0, fail_minutes=15, skip_mb=512,
                          slow_x=2.0, skip="", wait_minutes=30, no_wait=False,
                          retry_skipped=False, forget_dead=False,
                          feature=None, no_verify=False, yes=True)
            started = app._run_bg("Imaging disc", dr.cmd_image, app.settings,
                                  ns, rip=True)
        elif route == "remux":
            # The ROOT of the drive, with the separator. "D:" alone means
            # "whatever the current directory on D: happens to be", which is
            # not what anybody means and not what libbluray wants - it looks
            # for BDMV/index.bdmv relative to the path it is given.
            ns = dr._Args(source=f"{letter}:" + os.sep, out=None,
                          playlist=None,
                          no_langs=False, yes=True)
            started = app._run_bg("Muxing disc", dr.cmd_remux, app.settings,
                                  ns, rip=True)
        elif route == "backup":
            # The mode is a setting, and this window is the place somebody
            # asked for it - so it is set, saved, and visible on the tab
            # afterwards rather than being a hidden one-off.
            app._set_and_save("video.mode", "backup")
            started = app._run_bg("Backing up disc", dr.do_rip, app.settings,
                                  letter, rip=True)
        if started:
            self.close()
        else:
            self.refresh()

    def close(self):
        self.app.disc_win = None
        try:
            self.destroy()
        except tk.TclError:
            pass


class AutoWindow(tk.Toplevel):
    """What auto-rip is about to do, before you walk away from it.

    The same idea as the auto-encode window and for the same reason: an
    unattended run is a set of decisions taken in advance, and the moment to
    take them is before it starts, not by hunting through settings tabs
    afterwards wondering which one it was that made it stop at disc four.

    Only the handful that decide the shape of the run. Everything else is still
    on the tabs, and everything here is the same variable as the row on the tab,
    so neither can get out of step with the other.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.dr = app.dr
        self.title(f"{self.dr.APP} - auto-rip")
        self.configure(background=CLR["card"])
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)

        card = Card(self, "What to take off each disc")
        card.pack(fill="x", padx=12, pady=(12, 8))
        rows = OptionRows(app, card.body)
        rows.choice("Titles", "general.auto_titles")
        rows.choice("Naming", "general.auto_naming")
        rows.flag_pair(("Leave out extras and menus", "general.feature_only"),
                       ("Automatically balance file size with quality",
                        "video.crf_search"))
        self.mode_lbl = rows.line("", "Disc.TLabel")

        card = Card(self, "When a disc gives trouble")
        card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, card.body)
        rows.number("Extra attempts per disc", "general.auto_retries",
                    "before it counts as failed")
        rows.choice("If one fails", "general.auto_on_failure")
        # The ladder, and NOT a separate "fall back to a full backup" tick
        # beside it: the backup is one of the rungs in this list, and two
        # controls for one decision is how they end up disagreeing.
        rows.order("When every title fails", "general.fallback_order",
                   self.dr.FALLBACK_STAGES,
                   tip="What happens after a disc has defeated an ordinary "
                       "rip, in the order it happens. Reorder it or switch "
                       "rungs off - each costs a very different amount of "
                       "time, and which are worth it depends on the disc and "
                       "on whether anybody is in the room.")
        # "ask" is one of the values rather than a tick beside a number,
        # because they are one decision - and a tick that only means anything
        # when the number is unset is how two controls come to disagree.
        # TWO DIFFERENT DECISIONS, in the order they happen. This one is the
        # first pass: how long to keep asking one bad band before stepping
        # past it. The pass now returns to whatever it stepped past, from the
        # far end, so this buys time at the damage rather than deciding how
        # much disc gets written off.
        rows.choice("Grinding at one bad band", "video.grind_effort",
                    note="how long the first pass keeps asking before it "
                         "steps past and comes back for it from the far end")
        rows.choice("Chasing missing data", "video.repair_effort",
                    note="ask = stop and ask once the first pass knows the "
                         "cost; it alerts when it does")
        # "Read size while imaging" IS NOT HERE ANY MORE, and it is not an
        # oversight. video.read_block_mb started life as a speed dial and the
        # cluster measurement turned it round: a 1024-sector block was
        # condemned while sectors inside it read fine, so the size is a
        # GRANULARITY OF LOSS, and raising it costs recovered film. The speed
        # side is handled without asking - read_block_fast_mb widens the reads
        # while nothing has failed and drops back to this on the first error -
        # so the only thing this control can still do in a window somebody
        # skims before walking away is make a damaged disc give up more of
        # itself. It is still on the Video tab, where its help text says so.
        rows.text("Speeds to fall back through", "video.stall_slow_x",
                  width=14, note="Multiples of 1x. Empty means never cap.",
                  flag=("Read slower in difficult sections",
                        "video.slow_on_stall"))

        card = Card(self, "When to stop")
        card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, card.body)
        rows.number("Give up waiting after", "general.auto_wait_minutes",
                    "minutes (0 = wait for ever)")
        rows.number("Stop after", "general.auto_stop_after",
                    "discs (0 = no limit)")
        rows.flag_pair(("Open the tray when each disc is done",
                        "general.eject_after"),
                       ("Shut the computer down when the run ends",
                        "general.shutdown_after"))

        card = Card(self, "Being told about it")
        card.pack(fill="x", padx=12, pady=(0, 8))
        rows = OptionRows(app, card.body)
        rows.flag("Alerts on", "alerts.enabled")
        # The two that matter to somebody who has walked away: it finished,
        # and it is stuck. Per-disc chimes and every other alert setting are
        # on the Alerts tab.
        rows.choice("The whole run finishing", "alerts.on_all_done",
                    values=self.dr.ALERT_MODES)
        rows.line("Everything else about alerts - per-disc chimes, the stall "
                  "alarm, sounds, volume, voice - is on the Alerts tab, with "
                  "a Test button.")

        # PACKED FIRST, AND AT THE BOTTOM. Tk gives space in packing order, so
        # a footer packed last is the first thing to fall off the end of a
        # window that has grown - which is exactly what happened: the window
        # ran past the bottom of the screen and Start went with it, leaving no
        # way to start an auto-rip at all. Bottom-first reserves it, so
        # content is what gets squeezed.
        foot = ttk.Frame(self, style="Card.TFrame")
        foot.pack(side="bottom", fill="x", padx=12, pady=(4, 14))
        self.btn_start = ttk.Button(foot, text="Start auto-rip",
                                    command=self.start)
        self.btn_start.pack(side="left")
        ttk.Button(foot, text="Close", command=self.close).pack(side="right")

        self.update_idletasks()
        fit_to_screen(self, app)
        self.refresh()

    def refresh(self):
        dr = self.dr
        mode = dr.mode_of(self.app.settings)
        spec = dr.MODES.get(mode)
        v = dict(dr.sget(self.app.settings, "video"))
        self.mode_lbl.configure(
            text=(f"{spec['label']} mode: {spec['blurb']}" if spec
                  else "Custom settings - whatever the tabs currently say.")
            + (f"   [{v['vcodec']} q{v['quality']}]" if v.get("compress")
               else "   [lossless remux, no re-encode]"))
        self.btn_start.configure(
            state="disabled" if (self.app.worker
                                 and self.app.worker.is_alive()) else "normal")

    def start(self):
        if not self.app.need_out_dir("Auto-rip"):
            return
        if not self.app.speed_ok("Auto-rip"):
            return
        if self.app._run_bg("Auto-rip", self.dr.auto_rip_loop,
                            self.app.settings, rip=True):
            self.close()
        else:
            self.refresh()

    def close(self):
        self.app.auto_win = None
        try:
            self.destroy()
        except tk.TclError:
            pass


class BatchWindow(tk.Toplevel):
    """Auto-rip, with a hard drive where the optical drive usually is.

    The unattended part of auto-rip is not the disc - it is "here is a pile of
    work, get through it, tell me how it went, and let me walk away". A library
    of finished rips is that pile too, and at ninety times realtime the whole
    thing is an evening rather than a fortnight.

    What this window is for is the bit a command line does badly: seeing what
    is about to happen. Folders resolve to a file list, already-finished work is
    shown as skipped and why, and the total is on screen before anything starts.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.dr = app.dr
        self.title(f"{self.dr.APP} - auto-encode files")
        self.configure(background=CLR["card"])
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        sc = app.sc
        # Kept on the app rather than here, because the window is closed the
        # moment a run starts and a second batch over the same folder should not
        # mean finding it again. What is deliberately NOT remembered is the
        # removal option: that one starts off every time the window opens, so it
        # can never be on by inheritance from a run somebody has forgotten.
        self.targets = list(getattr(app, "batch_targets", []))
        self.overrides = dict(getattr(app, "batch_overrides", {}) or {})
        self.item_win = None
        self.todo = []
        self.skipped = []
        self.todo_bytes = 0
        # The folder walk runs on a worker; an answer from a superseded walk
        # must be dropped, not drawn.
        self._resolve_gen = 0

        card = Card(self, "What to work through",
                    "Films, or folders of them. A folder is searched all the "
                    "way down.")
        card.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        b = card.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(
            b, height=8, activestyle="none", relief="sunken", borderwidth=2,
            background=CLR["field"], foreground=CLR["text"], font=app.f_base,
            selectbackground=CLR["accent"], selectforeground=CLR["bg"],
            highlightthickness=0, exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(b, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=ys.set)
        ys.grid(row=0, column=1, sticky="ns")
        btns = ttk.Frame(b, style="Card.TFrame")
        btns.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(btns, text="Add folder...",
                   command=self.add_folder).pack(side="left")
        ttk.Button(btns, text="Add files...",
                   command=self.add_files).pack(side="left", padx=6)
        self.btn_item = ttk.Button(btns, text="Settings for this one...",
                                   command=self.item_settings)
        self.btn_item.pack(side="left", padx=(18, 0))
        Tip(self.btn_item, "Pick a line above and give it settings of its own - "
                           "a different quality, different framing, or "
                           "different treatment of its originals. Everything "
                           "else in the run keeps the settings on the tabs.")
        ttk.Button(btns, text="Remove",
                   command=self.remove).pack(side="left", padx=(18, 0))
        ttk.Button(btns, text="Clear",
                   command=self.clear).pack(side="left", padx=6)
        self.listbox.bind("<Double-Button-1>",
                          lambda e: self.item_settings())
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._enable_item())

        card = Card(self, "What that comes to")
        card.pack(fill="x", padx=12, pady=(0, 8))
        b = card.body
        b.columnconfigure(0, weight=1)
        self.sum_lbl = ttk.Label(b, text="", style="Disc.TLabel",
                                 justify="left",
                                 wraplength=int(640 * sc))
        self.sum_lbl.grid(row=0, column=0, sticky="w")
        self.skip_lbl = ttk.Label(b, text="", style="Muted.TLabel",
                                  justify="left",
                                  wraplength=int(640 * sc))
        self.skip_lbl.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.own_lbl = ttk.Label(b, text="", style="Card.TLabel",
                                 justify="left", foreground=CLR["accent"],
                                 wraplength=int(640 * sc))
        self.own_lbl.grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.mode_lbl = ttk.Label(b, text="", style="Card.TLabel",
                                  justify="left",
                                  wraplength=int(640 * sc))
        self.mode_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.rep_lbl = ttk.Label(b, text="", style="Card.TLabel",
                                 justify="left", foreground=CLR["warn"],
                                 wraplength=int(640 * sc))
        self.rep_lbl.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.warn_lbl = ttk.Label(b, text="", style="Card.TLabel",
                                  justify="left", foreground=CLR["warn"],
                                  wraplength=int(640 * sc))
        self.warn_lbl.grid(row=4, column=0, sticky="w", pady=(6, 0))

        opts = ttk.Frame(self, style="Card.TFrame")
        opts.pack(fill="x", padx=12, pady=(0, 4))
        opt = dict(getattr(app, "batch_opts", {}) or {})
        self.recurse_var = tk.BooleanVar(value=bool(opt.get("recurse", True)))
        self.force_var = tk.BooleanVar(value=bool(opt.get("force", False)))
        cb = ttk.Checkbutton(opts, text="Search sub-folders",
                             variable=self.recurse_var, style=app.cb_style,
                             command=self.refresh)
        cb.pack(side="left")
        cb2 = ttk.Checkbutton(opts, text="Redo ones already done",
                              variable=self.force_var, style=app.cb_style,
                              command=self.refresh)
        cb2.pack(side="left", padx=16)
        self.search_var = app.shared_var("video.crf_search", tk.BooleanVar)
        cb4 = ttk.Checkbutton(
            opts, text="Automatically balance file size with quality",
            variable=self.search_var, style=app.cb_style,
            command=lambda: (app._set_and_save("video.crf_search",
                                               self.search_var.get()),
                             self.refresh()))
        cb4.pack(side="left", padx=16)
        Tip(cb4, "Per film, rather than one quality setting for all of them: "
                 "sample a few passages, measure them, and use the smallest "
                 "size that still measures as good as the target. Costs a "
                 "minute or two a film and the quality setting on the tabs is "
                 "the ceiling, so it can only ever make a film smaller than "
                 "that or leave it alone.")
        Tip(cb2, "Off, a film whose encode is already there is skipped - which "
                 "is what lets this be stopped and started again without "
                 "redoing hours of work.")

        # On its own line, because it is not the same kind of option as those
        # two: the others change what gets done, this one changes what is left
        # afterwards. Off every time the window opens - never remembered, never
        # written to the config, so it cannot be on by inheritance from a run
        # somebody has forgotten about.
        rep = ttk.Frame(self, style="Card.TFrame")
        rep.pack(fill="x", padx=12, pady=(0, 4))
        self.replace_var = tk.BooleanVar(value=False)
        cb3 = ttk.Checkbutton(
            rep, text="Remove the original once its encode checks out",
            variable=self.replace_var, style=app.cb_style,
            command=self._replace_toggled)
        cb3.pack(side="left")
        Tip(cb3, "Off. On, each original goes once its own encode has been "
                 "read back and found to be the same length, with video in it "
                 "and with sound. An encode that comes out short, silent or "
                 "unreadable leaves its original exactly where it is - and "
                 "the log says which and why.")
        self.how_var = tk.StringVar(value="recycle")
        self.how_box = ttk.Frame(rep, style="Card.TFrame")
        rb1 = ttk.Radiobutton(self.how_box, text="to the Recycle Bin",
                              value="recycle", variable=self.how_var,
                              style="Card.TRadiobutton", command=self.refresh)
        rb1.pack(side="left")
        Tip(rb1, "Recoverable, but the space does not come back until the bin "
                 "is emptied - so a whole library of six-gigabyte originals "
                 "still fills the drive.")
        rb2 = ttk.Radiobutton(self.how_box, text="delete outright",
                              value="delete", variable=self.how_var,
                              style="Card.TRadiobutton", command=self.refresh)
        rb2.pack(side="left", padx=12)
        Tip(rb2, "The space comes back immediately and there is no copy "
                 "anywhere afterwards.")

        foot = ttk.Frame(self, style="Card.TFrame")
        foot.pack(fill="x", padx=12, pady=(4, 14))
        self.btn_start = ttk.Button(foot, text="Start", command=self.start)
        self.btn_start.pack(side="left")
        ttk.Button(foot, text="Close", command=self.close).pack(side="right")

        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 70}+{app.winfo_rooty() + 60}")
        self.refresh()

    def _replace_toggled(self):
        """Show how it goes only once it is going - two questions, in order."""
        if self.replace_var.get():
            self.how_box.pack(side="left", padx=(16, 0))
        else:
            self.how_box.pack_forget()
        self.refresh()

    def replace_mode(self):
        """None, "recycle" or "delete" - what the two controls add up to."""
        return self.how_var.get() if self.replace_var.get() else None

    # -- the list ---------------------------------------------------------
    def add_folder(self):
        got = filedialog.askdirectory(
            title="Which folder?",
            initialdir=os.path.expanduser(
                str(self.dr.sget(self.app.settings, "video.out_dir", "~"))))
        if got:
            self._add([got])

    def add_files(self):
        got = filedialog.askopenfilenames(
            title="Which films?",
            initialdir=os.path.expanduser(
                str(self.dr.sget(self.app.settings, "video.out_dir", "~"))),
            filetypes=[("Video", "*.mkv *.mp4 *.m2ts *.ts *.m4v *.avi *.mov"),
                       ("All files", "*.*")])
        if got:
            self._add(list(got))

    def _add(self, paths):
        for p in paths:
            if p not in self.targets:
                self.targets.append(p)
        self.refresh()

    def remove(self):
        for i in sorted(self.listbox.curselection(), reverse=True):
            self.overrides.pop(str(self.targets[i]), None)
            del self.targets[i]
        self.refresh()

    def clear(self):
        self.targets = []
        self.overrides = {}
        self.refresh()

    def _enable_item(self):
        try:
            self.btn_item.configure(
                state="normal" if self.listbox.curselection() else "disabled")
        except tk.TclError:
            pass

    def item_settings(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        if self.item_win is not None and self.item_win.winfo_exists():
            self.item_win.close()
        self.item_win = ItemWindow(self, self.targets[sel[0]])

    # -- what it adds up to ----------------------------------------------
    def refresh(self):
        """Redraw the cheap parts now; count the work list off the thread.

        The list, the overrides and the mode line are string work and happen
        here. The resolve itself is a directory walk and a stat per file,
        which on a big library over a network share is real time - done on
        the main thread it froze the window on every keystroke, so it runs on
        a worker and lands through _resolved with a generation guard."""
        dr = self.dr
        self.app.batch_targets = list(self.targets)
        self.app.batch_opts = {"recurse": self.recurse_var.get(),
                               "force": self.force_var.get()}
        self.app.batch_overrides = dict(self.overrides)
        keep = list(self.listbox.curselection())
        self.listbox.delete(0, "end")
        for t in self.targets:
            ov = self.overrides.get(str(t))
            self.listbox.insert(
                "end", str(t) + (f"      [{len(ov)} of its own]" if ov else ""))
        for i in keep:
            if i < len(self.targets):
                self.listbox.selection_set(i)
        self._enable_item()
        if self.overrides:
            named = ", ".join(Path(t).name or str(t)
                              for t in list(self.overrides)[:3])
            more = (f" and {len(self.overrides) - 3} more"
                    if len(self.overrides) > 3 else "")
            self.own_lbl.configure(
                text=f"{len(self.overrides)} of them have settings of their "
                     f"own: {named}{more}")
        else:
            self.own_lbl.configure(text="")
        self._resolve_gen += 1
        gen = self._resolve_gen
        if not self.targets:
            self._resolved(gen, [], [], 0, None)
            return
        self.sum_lbl.configure(text="Counting...")
        self.skip_lbl.configure(text="")
        self.btn_start.configure(state="disabled")
        v = dict(dr.sget(self.app.settings, "video"))
        targets = list(self.targets)
        rec, force = self.recurse_var.get(), self.force_var.get()
        over = dict(self.overrides)

        def work():
            try:
                todo, skipped = dr.encode_targets(targets, v, rec, force,
                                                  over)
                err = None
            except OSError as e:
                todo, skipped, err = [], [], str(e)
            size = 0
            for p in todo:
                try:
                    size += p.stat().st_size
                except OSError:
                    pass
            self.app.ui_q.put(("worklist",
                               (self, gen, todo, skipped, size, err)))
        threading.Thread(target=work, daemon=True).start()

    def _resolved(self, gen, todo, skipped, size, err):
        """A finished count arriving on the main thread. Stale ones dropped."""
        if gen != self._resolve_gen:
            return
        dr = self.dr
        if err is not None:
            self.todo, self.skipped, self.todo_bytes = [], [], 0
            self.sum_lbl.configure(text=f"Could not read that: {err}")
            self.btn_start.configure(state="disabled")
            return
        self.todo, self.skipped, self.todo_bytes = todo, skipped, size
        v = dict(dr.sget(self.app.settings, "video"))
        if not self.targets:
            self.sum_lbl.configure(text="Nothing chosen yet.")
        elif not self.todo:
            self.sum_lbl.configure(
                text="Nothing left to do - everything here is already encoded.")
        else:
            self.sum_lbl.configure(
                text=f"{len(self.todo)} film(s), {dr.human_size(size)} to read")
        if self.skipped:
            shown = "; ".join(f"{p.name} ({why})" for p, why in self.skipped[:3])
            more = (f" and {len(self.skipped) - 3} more"
                    if len(self.skipped) > 3 else "")
            self.skip_lbl.configure(
                text=f"{len(self.skipped)} skipped: {shown}{more}")
        else:
            self.skip_lbl.configure(text="")
        mode = dr.mode_of(self.app.settings)
        spec = dr.MODES.get(mode)
        self.mode_lbl.configure(
            text=(f"{spec['label']} mode: {spec['blurb']}" if spec
                  else "Custom settings - whatever the tabs currently say.")
            + f"   [{v['vcodec']} q{v['quality']}, HD q{v['hd_quality']}]"
            + ("   quality search on" if v.get("crf_search") else ""))
        how = self.replace_mode()
        n = len(self.todo)
        if not how or not n:
            self.rep_lbl.configure(text="")
        elif how == "recycle":
            self.rep_lbl.configure(
                foreground=CLR["warn"],
                text="As each film finishes, its own original goes to the "
                     f"Recycle Bin - {n} of them, {dr.human_size(size)} in "
                     "all. They stay on the disk and keep taking up its space "
                     "until you empty the bin, so this frees nothing by "
                     "itself. If space is what you are short of, delete "
                     "outright instead.")
        else:
            self.rep_lbl.configure(
                foreground=CLR["err"],
                text="As each film finishes, its own original is deleted - "
                     f"{n} of them, {dr.human_size(size)} freed, and no copy "
                     "anywhere afterwards. One film at a time, so the space "
                     "comes back as the batch goes rather than at the end.")
        notes = dr.MODE_NOTES.get(mode) or ()
        # The mode note that matters here is the one about deleting originals,
        # and whether it applies is exactly what the option above decides - so
        # lead with which of the two is true rather than letting a warning
        # written for the rip flow frighten somebody off their own library.
        if how:
            lead = ("Each original goes only after its own encode has been "
                    "read back and found sound - full length, with video and "
                    "with sound. Anything short or silent keeps its original.")
            langs = str(v.get("audio_langs") or "all")
            if langs not in ("", "all"):
                # The trade that is right for a viewing copy and wrong for the
                # only copy: the French dub and the commentary are in the
                # original, and the original is what is about to go.
                lead += (f"\nThe encode keeps only {langs} audio and "
                         f"{v.get('sub_langs')} subtitles - any other "
                         "language, and any commentary track, exists only in "
                         "the original.")
        else:
            lead = ("Your files are read, never written to or deleted - each "
                    "encode is written beside its source.")
        self.warn_lbl.configure(
            text=(lead + "\n" + notes[0]) if notes else lead)
        # NOT GATED ON THE WORKER any more. An auto-encode goes into the
        # background queue rather than onto the window's one worker thread, so
        # a rip in progress is no longer a reason it cannot start - see
        # run_in_queue. What still stops two encodes happening at once is that
        # the queue has one thread.
        self.btn_start.configure(
            state="normal" if self.todo else "disabled")

    # -- and off it goes --------------------------------------------------
    def start(self):
        if not self.todo:
            return
        n = len(self.todo)
        how = self.replace_mode()
        size = self.dr.human_size(getattr(self, "todo_bytes", 0))
        if how == "delete":
            tail = (f"Each of the {n} original(s) is DELETED as soon as its "
                    "own encode has been checked - one at a time as the batch "
                    f"goes, not all at the end. {size} freed in all, with no "
                    "Recycle Bin copy. Only an original whose encode reads "
                    "back sound is removed.")
        elif how == "recycle":
            tail = (f"Each of the {n} original(s) goes to the Recycle Bin as "
                    "soon as its own encode has been checked. "
                    f"{size} in all, recoverable - but still using the space "
                    "until you empty the bin. Only an original whose encode "
                    "reads back sound is removed.")
        else:
            tail = "Originals are left alone."
        # WHERE IT RUNS, because that changed on 2 Sep and it changes what
        # somebody should expect to see. It goes in the background queue
        # rather than onto the window's worker, so it does not need the drive
        # and does not block a rip - and a rip that finishes while it is going
        # will have its own encode jump the queue ahead of the films left in
        # this batch.
        _busy = bool(self.app.worker and self.app.worker.is_alive())
        where = ("\n\nIt runs in the background queue, so "
                 + ("the rip in progress carries on and this waits its turn "
                    "behind it. " if _busy
                    else "you can start a rip while it goes. ")
                 + "Only one film is ever encoded at a time, and a disc "
                   "finishing mid-batch gets its own encode done first.")
        if not messagebox.askyesno(
                self.dr.APP,
                f"Encode {n} film(s)?\n\n{self.sum_lbl.cget('text')}\n\n"
                + tail + where
                + "\n\nYou can stop it at any point and start again "
                "later - finished films are skipped."):
            return
        if not self.app.speed_ok(f"Encoding {n} film(s)"):
            return
        targets = list(self.targets)
        rec, force = self.recurse_var.get(), self.force_var.get()
        # Out of the way once it has handed the work over. Everything it was
        # for - what is queued, what is skipped, what happens to the originals -
        # was a decision to be made before starting, and none of it is worth
        # covering up the progress and the log that come next.
        if self.app.run_in_queue(
                f"Auto-encode ({n} file{'s' if n != 1 else ''})",
                self.dr.cmd_encode_batch, self.app.settings,
                targets, rec, force, how, dict(self.overrides)):
            self.close()
        else:
            self.refresh()

    def close(self):
        self.app.batch_win = None
        if self.item_win is not None and self.item_win.winfo_exists():
            self.item_win.close()
        try:
            self.destroy()
        except tk.TclError:
            pass


class CalibrateWindow(tk.Toplevel):
    """Encode one passage several ways and let somebody pick with their eyes.

    crf_search already finds the CRF that scores VMAF 90 or better, and that
    target is a number from a paper - not this person's eyes, on this screen,
    with these films. This is how they replace it with their own.

    The passage matters as much as the ladder. A quiet dialogue scene looks
    identical at CRF 22 and CRF 38, so calibrating on one flatters every
    setting equally and teaches nothing; busiest_passage() picks the place the
    studio encoder spent the most bits, which is where a lossy pass has the
    most to throw away.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app, self.dr = app, app.dr
        self.title(f"{self.dr.APP} - calibrate quality")
        self.transient(app)
        self.configure(background=CLR["bg"])
        self.clips, self.src = [], None

        pick = Card(self, "A film to calibrate on")
        pick.pack(fill="x", padx=12, pady=(12, 8))
        row = ttk.Frame(pick.body, style="Card.TFrame")
        row.pack(fill="x")
        self.src_lbl = ttk.Label(row, style="Disc.TLabel", wraplength=430,
                                 text="Pick a lossless remux - one of your "
                                      "own rips, before compression.")
        self.src_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose...", command=self._choose).pack(
            side="right")

        where = Card(self, "Which passage")
        where.pack(fill="x", padx=12, pady=(0, 8))
        self.at_var = tk.StringVar(value="")
        r2 = ttk.Frame(where.body, style="Card.TFrame")
        r2.pack(fill="x")
        ttk.Label(r2, style="Disc.TLabel",
                  text="Start at (m:ss), or blank for the busiest passage:"
                  ).pack(side="left")
        ttk.Entry(r2, textvariable=self.at_var, width=10).pack(side="left",
                                                              padx=(8, 0))
        ttk.Label(where.body, style="Disc.TLabel", wraplength=560,
                  justify="left",
                  text="Choosing your own is better if you know a scene that "
                       "gives encoders trouble - grain, smoke, fast motion, "
                       "dark gradients. Left blank, the busiest passage in "
                       "the film is used, because a quiet scene looks the "
                       "same at every setting and would flatter all of them."
                  ).pack(anchor="w", pady=(6, 0))

        howmany = Card(self, "Which settings to compare")
        howmany.pack(fill="x", padx=12, pady=(0, 8))
        r3 = ttk.Frame(howmany.body, style="Card.TFrame")
        r3.pack(fill="x")
        ttk.Label(r3, style="Disc.TLabel",
                  text="CRFs, or blank for the recommended set:").pack(
            side="left")
        self.crf_var = tk.StringVar(
            value=str(self.dr.sget(self.app.settings,
                                   "video.calibrate_crfs") or ""))
        ttk.Entry(r3, textvariable=self.crf_var, width=22).pack(
            side="left", padx=(8, 0))
        ttk.Label(howmany.body, style="Disc.TLabel", wraplength=560,
                  justify="left",
                  text="Blank gives five: 22, 28, 34, 40, 46. Measured on "
                       "animation those land near VMAF 99, 98, 95, 91 and 85 - "
                       "so the last one is past the usual target, which is "
                       "there so you can see what you are refusing as well as "
                       "what you are accepting. More clips cost more encoding "
                       "time and nothing else."
                  ).pack(anchor="w", pady=(6, 0))

        out = Card(self, "The comparison")
        out.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.listbox = tk.Listbox(
            out.body, height=5, activestyle="none", exportselection=False,
            background=CLR["field"], foreground=CLR["text"],
            selectbackground=CLR["accent"], selectforeground=CLR["bg"],
            highlightthickness=0, bd=0)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self._open())
        self.note = ttk.Label(out.body, style="Disc.TLabel", wraplength=560,
                              justify="left", text="")
        self.note.pack(anchor="w", pady=(8, 0))

        foot = ttk.Frame(self, style="TFrame")
        foot.pack(fill="x", padx=12, pady=(0, 12))
        self.btn_make = ttk.Button(foot, text="Make the clips",
                                   command=self._make, state="disabled")
        self.btn_make.pack(side="left")
        self.btn_use = ttk.Button(foot, text="Use this for every encode",
                                  command=self._use, state="disabled")
        self.btn_use.pack(side="left", padx=(8, 0))
        ttk.Button(foot, text="Close", command=self.destroy).pack(side="right")
        Tip(self.btn_use,
            "Sets video.calibrate to 'once' and remembers the CRF, so every "
            "encode from now on uses it - portable rips included - instead "
            "of searching for a metric target.")

    def _choose(self):
        base = str(self.dr.sget(self.app.settings, "video.out_dir")
                   or "~/Videos/Rips")
        got = filedialog.askopenfilename(
            parent=self, title="A lossless remux to calibrate on",
            initialdir=str(Path(base).expanduser()),
            filetypes=[("Video", "*.mkv *.m2ts *.mp4 *.ts"), ("All", "*.*")])
        if not got:
            return
        self.src = Path(got)
        self.src_lbl.configure(text=str(self.src))
        self.btn_make.configure(state="normal")

    def _at_seconds(self):
        """The typed start, in seconds. None for blank; False for nonsense."""
        raw = self.at_var.get().strip()
        if not raw:
            return None
        try:
            bits = [float(x) for x in raw.split(":")]
        except (TypeError, ValueError):
            return False
        return bits[0] * 60 + bits[1] if len(bits) > 1 else bits[0]

    def _make(self):
        if not self.src:
            return
        at = self._at_seconds()
        if at is False:
            messagebox.showinfo(self.dr.APP,
                                "Give the start as m:ss, or leave it blank.")
            return
        self.listbox.delete(0, "end")
        self.clips = []
        self.btn_use.configure(state="disabled")
        self.note.configure(text="Encoding the comparison clips...")
        self.btn_make.configure(state="disabled")
        self.app._run_bg("Calibrating", self._work, at, quiet=True)

    def _work(self, at):
        secs = float(self.dr.sget(self.app.settings,
                                  "video.calibrate_seconds", 12) or 12)
        want = self.crf_var.get().strip()
        crfs = self.dr.calibration_crfs(
            self.dr.sget(self.app.settings, "video.quality") or 22, want=want)
        clips, ref = self.dr.calibration_clips(
            self.app.settings, self.src, at=at, seconds=secs, crfs=crfs,
            dest=self.src.parent / "calibration")
        # Back onto the UI thread; _run_bg's worker must not touch widgets.
        self.after(0, lambda: self._done(clips, ref))

    def _done(self, clips, ref):
        if not self.winfo_exists():
            return
        self.btn_make.configure(state="normal")
        self.clips = clips or []
        if not self.clips:
            self.note.configure(
                text="Could not make the clips. The source has to be a video "
                     "file this ffmpeg can decode, and there has to be room "
                     "beside it for a lossless reference.")
            return
        # The whole film's size beside the clip's, because that is the number
        # the choice is actually about - see calibration_clips for why it is a
        # ratio rather than the clip's bitrate multiplied out, and for why it
        # counts the video only.
        for c in self.clips:
            sc = ("" if c["score"] is None
                  else f"   {c['metric']} {c['score']:>5.1f}")
            film = ("" if not c.get("film_bytes")
                    else f"   ~{c['film_bytes'] / 1e9:>5.2f} GB film")
            self.listbox.insert("end", f"  CRF {c['crf']:>2}   "
                                       f"{c['bytes'] / 1e6:>6.1f} MB{sc}{film}")
        self.listbox.selection_set(len(self.clips) // 2)
        self.btn_use.configure(state="normal")
        self.note.configure(
            text=f"Double-click one to open it. Reference: {ref}"
                 + chr(10) + "Bigger CRF means a smaller file. Pick the "
                 "largest number you cannot tell from the reference."
                 + (chr(10) + "The film figures are the video only, estimated "
                    "from this passage - the audio tracks are on top."
                    if any(c.get("film_bytes") for c in self.clips) else ""))

    def _open(self):
        i = (self.listbox.curselection() or [None])[0]
        if i is None or i >= len(self.clips):
            return
        try:
            os.startfile(self.clips[i]["path"])
        except Exception as e:
            messagebox.showinfo(self.dr.APP, f"Could not open it: {e}")

    def _use(self):
        i = (self.listbox.curselection() or [None])[0]
        if i is None or i >= len(self.clips):
            return
        crf = int(self.clips[i]["crf"])
        self.app._set_and_save("video.calibrated_crf", crf)
        self.app._set_and_save("video.calibrate", "once")
        # Remember the ladder too, so "each" mode and the next visit use the
        # set this person actually found useful.
        self.app._set_and_save("video.calibrate_crfs",
                               self.crf_var.get().strip())
        messagebox.showinfo(
            self.dr.APP,
            f"CRF {crf} will be used for every encode from now on, portable "
            f"rips included." + chr(10) * 2 +
            "Change it under video.calibrate - 'each' offers this comparison "
            "per film instead, and 'off' goes back to the metric search.")
        self.destroy()


class RetryWindow(tk.Toplevel):
    """Re-read only the parts of an image that are still missing.

    The map beside every salvage already records exactly which sectors were
    never read, and `image --feature --retry-skipped` already attempts only
    those - 146 MB of Crazy Rich Asians' 41 GB image, against the 5.2 GB a
    bare --retry-skipped would also pull in. All of that was reachable only by
    knowing it existed, typing it, and getting --feature right.

    The number that makes somebody want to is how much FILM is missing, and
    that is sitting in the cuts file beside the image. So this lists the
    images, says what each is missing in minutes and seconds, and runs it.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.dr = app.dr
        self.title(f"{self.dr.APP} - retry the missing parts")
        self.transient(app)
        self.configure(background=CLR["bg"])
        self.rows = []

        head = Card(self, "Images with film still missing")
        head.pack(fill="x", padx=12, pady=(12, 8))
        self.lbl = ttk.Label(head.body, style="Disc.TLabel", wraplength=560,
                             text="Looking...")
        self.lbl.pack(anchor="w")

        box = Card(self, "Pick one")
        box.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.listbox = tk.Listbox(
            box.body, height=6, activestyle="none", exportselection=False,
            background=CLR["field"], foreground=CLR["text"],
            selectbackground=CLR["accent"], selectforeground=CLR["bg"],
            highlightthickness=0, bd=0)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._show())
        self.detail = ttk.Label(box.body, style="Disc.TLabel", wraplength=560,
                                justify="left", text="")
        self.detail.pack(anchor="w", pady=(8, 0))

        eff = ttk.Frame(box.body, style="Card.TFrame")
        eff.pack(fill="x", pady=(8, 0))
        ttk.Label(eff, style="Disc.TLabel",
                  text="How long to grind at each bad place:").pack(
            side="left")
        self.effort_var = tk.StringVar(value="normal")
        self.effort_box = ttk.Combobox(
            eff, textvariable=self.effort_var, state="readonly", width=12,
            values=list(self.dr.RETRY_EFFORT_ORDER))
        self.effort_box.pack(side="left", padx=(8, 0))
        self.effort_lbl = ttk.Label(box.body, style="Disc.TLabel",
                                    wraplength=560, justify="left", text="")
        self.effort_lbl.pack(anchor="w", pady=(4, 0))
        self.effort_box.bind("<<ComboboxSelected>>",
                             lambda e: self._effort_note())
        self._effort_note()

        self.rebuild_var = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(
            # The style has to be built from cb_style, which carries the
            # per-theme sequence number ("DR1", "DR2" after a theme switch).
            # A literal names a style nothing configures, and ttk silently
            # falls back to clam's diagonal cross - which is the exact defect
            # _install_check_indicator documents itself as fixing. Both my
            # first guess, "Card.TCheckbutton", and my second, the literal
            # "DR.TCheckbutton", were that same mistake.
            box.body, style=self.app.cb_style,
            text="Rebuild the film afterwards, if anything is recovered",
            variable=self.rebuild_var)
        cb.pack(anchor="w", pady=(8, 0))
        Tip(cb,
            "Muxes the film again from the enlarged image, and compresses it "
            "if that is what your mode does. Skipped when the re-read got "
            "nothing, because the output would be identical and it costs an "
            "hour to find that out.")

        foot = ttk.Frame(self, style="TFrame")
        foot.pack(fill="x", padx=12, pady=(0, 12))
        self.btn = ttk.Button(foot, text="Re-read the missing parts",
                              command=self.start, state="disabled")
        self.btn.pack(side="left")
        ttk.Button(foot, text="Close", command=self.destroy).pack(side="right")
        Tip(self.btn,
            "Reads only the sectors the map says were never got, straight into "
            "the image that is already there. Nothing already read is read "
            "again, so a partial success still shortens the cuts.")
        self.after(50, self._load)

    def _load(self):
        try:
            base = (self.dr.sget(self.app.settings, "video.out_dir")
                    or self.dr.sget(self.app.settings, "general.out_dir")
                    or "~/Videos/Rips")
            # It ships as "~/Videos/Rips", so this has to expand.
            self.rows = self.dr.retryable_salvages(
                Path(str(base)).expanduser()) or []
        except Exception as e:                                    # noqa: BLE001
            self.rows = []
            self.lbl.configure(text=f"Could not look: {e}")
            return
        if not self.rows:
            self.lbl.configure(
                text="Nothing to retry - every image beside your rips has all "
                     "the film its map asked for. This fills in gaps left by a "
                     "disc that would not read; if a disc has since been "
                     "cleaned, that is exactly when it is worth a second go.")
            return
        self.lbl.configure(
            text=f"{len(self.rows)} image(s) still missing film. Cleaning the "
                 f"disc first is the single cheapest thing that changes the "
                 f"answer - centre outward, never in circles.")
        for r in self.rows:
            self.listbox.insert(
                "end", f"  {r['name']}  -  {r['missing_mb']:,.0f} MB in "
                       f"{r['runs']} place(s)")
        self.listbox.selection_set(0)
        self._show()

    def _effort_note(self):
        mins, label = self.dr.RETRY_EFFORT.get(
            self.effort_var.get(), self.dr.RETRY_EFFORT["normal"])
        extra = ("" if not mins else
                 f" In practice {mins}-{mins + 5} minutes: the budget is only "
                 f"checked between reads, and a read this drive cannot do "
                 f"costs about 260 seconds that nothing can shorten.")
        self.effort_lbl.configure(text=label.capitalize() + "." + extra)

    def _show(self):
        i = (self.listbox.curselection() or [None])[0]
        if i is None or i >= len(self.rows):
            return
        r = self.rows[i]
        cuts = [ln.strip() for ln in (r.get("cuts") or "").splitlines()
                if "missing (" in ln]
        txt = [f"{r['missing_mb']:,.0f} MB to re-read, in {r['runs']} place(s)."]
        if cuts:
            txt.append("What is missing, in film time:")
            txt += [f"    {c}" for c in cuts[:4]]
        if r.get("feature_only"):
            txt.append("Feature-only image, so the extras stay skipped and "
                       "only the film is attempted.")
        self.detail.configure(text=chr(10).join(txt))
        self.btn.configure(state="normal")

    def start(self):
        i = (self.listbox.curselection() or [None])[0]
        if i is None:
            return
        r = self.rows[i]
        # CHECKED BEFORE THE CONFIRMATION, not after. _run_bg already refuses
        # to start a second job - correctly - but it does that AFTER this
        # dialog has already asked somebody to go put a disc back in the
        # drive, and its only feedback is a small info box that is easy to
        # miss right after dismissing this one. Observed: the confirmation
        # closed, nothing visibly happened, and it looked like the button
        # did nothing at all. Asking first means "no" is the only thing that
        # can happen from here on.
        if self.app.worker and self.app.worker.is_alive():
            messagebox.showinfo(
                self.dr.APP,
                f"'{self.app.action or 'Something'}' is still running, so "
                f"this can't start yet. Stop it or wait for it to finish, "
                f"then try again.")
            return
        # current_letter() is what every other action on this page uses. The
        # first draft read general.drive and passed it straight through, which
        # ships as "auto" - so the engine was handed --drive AUTO and stopped
        # with "Could not work out how big the disc in AUTO: is". A setting
        # whose documented value is "auto | a drive letter" is not a letter.
        drive = self.app.current_letter()
        if not messagebox.askyesno(
                self.dr.APP,
                f"Put {r['name']} back in the drive, then re-read "
                f"{r['missing_mb']:,.0f} MB into the image that is already "
                f"there?" + chr(10) * 2 +
                "Nothing already read is read again."):
            return
        # cmd_image DIRECTLY, with a namespace, exactly as the disc window's
        # "image" route does.
        #
        # The first version called dr.main(argv). That re-parses arguments,
        # reconfigures the engine for command-line use and opens its own
        # session log - so every line and every progress tick went to a FILE
        # instead of to this window, and the strip never moved. Worse, main()
        # can leave the thread via SystemExit, which _run_bg's `except
        # Exception` does not catch, so the run could end with no traceback
        # anywhere. Observed: silence from "Phase 1 of 4" onwards.
        ns = self.dr._Args(
            drive=drive, out=r["iso"], passes=3,
            block=self.dr.read_block_sectors(self.app.settings), tries=3,
            minutes=0,
            fail_minutes=self.dr.retry_effort_minutes(self.effort_var.get()),
            skip_mb=512, slow_x=2.0, skip="",
            wait_minutes=30, no_wait=False,
            retry_skipped=True,
            # A cleaned disc deserves a fresh opinion on ranges an earlier run
            # called fatal - that is the whole reason somebody presses this.
            forget_dead=True,
            feature=bool(r.get("feature_only")) or None,
            no_verify=False, yes=True)
        self.destroy()
        # retry_and_rebuild rather than cmd_image, so the decision about
        # rebuilding is made from the MAP - read sectors before against after -
        # rather than from a guess. A retry that recovered nothing produces a
        # byte-identical film, and finding that out costs an hour.
        self.app._run_bg(
            f"Re-reading {r['name']}", self.dr.retry_and_rebuild,
            self.app.settings, ns, r["iso"], r["map"],
            bool(self.rebuild_var.get()), rip=True)


class FitWindow(tk.Toplevel):
    """Fit a film to a screen: measure it, see it, then encode it.

    The three settings this drives live on the DVD/Blu-ray tab, where they are
    defaults for every compressed rip. They are the wrong shape for the thing
    people actually want to do, which is "make this one film fill my
    television" - that wants one file, one look at the result, and one button.

    It takes a list, because "make my films fill my television" is not a
    one-film job either. What is global is the screen shape and how to reach it;
    the borders are found per film by the encoder itself, which is the only way
    that can work - a 2.40:1 feature and a 4:3 episode have nothing in common to
    crop. So the stills and the numbers are measured on one film and labelled as
    such, and every film in the run still gets its own border detection.

    Nothing here is modal. Measuring and encoding both take time and both write
    to the activity log, and a window that covered the log while it worked
    would be hiding the only progress there is."""

    def __init__(self, app, path=None):
        super().__init__(app)
        self.app = app
        self.dr = app.dr
        self.title(f"{self.dr.APP} - fit to screen")
        self.configure(background=CLR["card"])
        self.transient(app)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)
        sc = app.sc
        self.path = None            # the one being measured and shown
        self.targets = []           # everything the run will go through
        self.todo = []
        self._resolve_gen = 0       # see BatchWindow: stale counts are dropped
        self.src = None             # (w, h, (sar_n, sar_d), duration, codec)
        self.bars = None            # the crop, once measured
        self.measured = False
        self.busy = False

        self.f_var = tk.StringVar()
        self.screen_var = tk.StringVar(
            value=str(self.dr.sget(app.settings, "video.screen", "source")))
        self.fit_var = tk.StringVar(
            value=str(self.dr.sget(app.settings, "video.fit", "pad")))
        self.trim_var = tk.StringVar(
            value=str(self.dr.sget(app.settings, "video.trim_bars", "auto")))

        # ---- the file
        card = Card(self, "Films", "One, or a whole shelf of them. Anything "
                                   "ffmpeg can read; the lossless remux is "
                                   "what you normally want to start from.")
        card.pack(fill="x", padx=12, pady=(12, 8))
        b = card.body
        b.columnconfigure(0, weight=1)
        row = ttk.Frame(b, style="Card.TFrame")
        row.grid(row=0, column=0, sticky="we")
        row.columnconfigure(0, weight=1)
        ent = ttk.Entry(row, textvariable=self.f_var, width=54)
        ent.grid(row=0, column=0, sticky="we")
        picks = ttk.Frame(b, style="Card.TFrame")
        picks.grid(row=1, column=0, sticky="we", pady=(6, 0))
        ttk.Button(picks, text="Choose a film",
                   command=self.pick).pack(side="left")
        ttk.Button(picks, text="Add folder",
                   command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(picks, text="Add films",
                   command=self.add_files).pack(side="left")
        ttk.Button(picks, text="Clear",
                   command=self.clear).pack(side="left", padx=(18, 0))
        self.src_lbl = ttk.Label(b, text="No film chosen yet.",
                                 style="Muted.TLabel", justify="left",
                                 wraplength=int(520 * sc))
        self.src_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.list_lbl = ttk.Label(b, text="", style="Disc.TLabel",
                                  justify="left",
                                  wraplength=int(520 * sc))
        self.list_lbl.grid(row=3, column=0, sticky="w", pady=(6, 0))

        # ---- the borders
        card = Card(self, "Black borders in the picture",
                    "A 2.40:1 film on a 16:9 disc stores a quarter of every "
                    "frame as black rows. They are real pixels, and no player "
                    "can zoom past them.")
        card.pack(fill="x", padx=12, pady=(0, 8))
        b = card.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Trim them", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        cb = ttk.Combobox(b, textvariable=self.trim_var, state="readonly",
                          width=8, values=["auto", "on", "off"])
        cb.grid(row=0, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self.recompute())
        self.btn_measure = ttk.Button(b, text="Measure", command=self.measure)
        self.btn_measure.grid(row=0, column=2, sticky="e")
        self.bars_lbl = ttk.Label(b, text="Not measured.", style="Muted.TLabel",
                                  justify="left", wraplength=int(520 * sc))
        self.bars_lbl.grid(row=1, column=0, columnspan=3, sticky="w",
                           pady=(8, 0))

        # ---- the screen
        card = Card(self, "Screen", "What shape to fit it to, and what gives "
                                    "when the film is not that shape.")
        card.pack(fill="x", padx=12, pady=(0, 8))
        b = card.body
        b.columnconfigure(3, weight=1)
        ttk.Label(b, text="Fit to", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        sb = ttk.Combobox(b, textvariable=self.screen_var, width=14,
                          values=[str(c) for c in self.dr.SCREEN_CHOICES])
        sb.grid(row=0, column=1, sticky="w")
        for seq in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            sb.bind(seq, lambda e: self.recompute())
        ttk.Label(b, text="   How", style="Card.TLabel").grid(
            row=0, column=2, sticky="w", padx=(10, 10))
        fb = ttk.Combobox(b, textvariable=self.fit_var, state="readonly",
                          width=10, values=list(self.dr.FIT_MODES))
        fb.grid(row=0, column=3, sticky="w")
        fb.bind("<<ComboboxSelected>>", lambda e: self.recompute())
        self.how_lbl = ttk.Label(b, text=FIT_BLURB["pad"], style="Muted.TLabel",
                                 justify="left", wraplength=int(520 * sc))
        self.how_lbl.grid(row=1, column=0, columnspan=4, sticky="w",
                          pady=(8, 0))

        # ---- the result
        card = Card(self, "Result")
        card.pack(fill="x", padx=12, pady=(0, 8))
        b = card.body
        b.columnconfigure(0, weight=1)
        self.out_lbl = ttk.Label(b, text="-", style="Disc.TLabel",
                                 justify="left", wraplength=int(520 * sc))
        self.out_lbl.grid(row=0, column=0, sticky="w")
        self.cost_lbl = ttk.Label(b, text="", style="Card.TLabel",
                                  justify="left", wraplength=int(520 * sc))
        self.cost_lbl.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.vf_lbl = ttk.Label(b, text="", style="Muted.TLabel",
                                justify="left", font=app.f_small,
                                wraplength=int(520 * sc))
        self.vf_lbl.grid(row=2, column=0, sticky="w", pady=(6, 0))

        # ---- and the two things to do with it
        foot = ttk.Frame(self, style="Card.TFrame")
        foot.pack(fill="x", padx=12, pady=(0, 14))
        self.btn_sample = ttk.Button(foot, text="Show me 3 stills",
                                     command=self.sample)
        self.btn_sample.pack(side="left")
        Tip(self.btn_sample, "Writes real frames through this exact filter "
                             "chain, next to the film. The only way to judge a "
                             "crop is to look at one.")
        self.btn_encode = ttk.Button(foot, text="Encode with this framing",
                                     command=self.encode)
        self.btn_encode.pack(side="left", padx=8)
        Tip(self.btn_encode, "A lossy re-encode at the quality set on the "
                             "DVD / Blu-ray tab, of every film on the list. "
                             "The screen shape and the fit are the same for "
                             "all of them; the black borders are found "
                             "separately for each, because they have to be. "
                             "Originals are untouched.")
        ttk.Button(foot, text="Close", command=self.close).pack(side="right")

        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 90}+{app.winfo_rooty() + 70}")
        self._enable()
        if path:
            self.load(path)

    # -- state ------------------------------------------------------------
    def _enable(self):
        got = self.path is not None and not self.busy
        for w in (self.btn_measure, self.btn_sample):
            try:
                w.configure(state="normal" if got else "disabled")
            except tk.TclError:
                pass
        try:
            self.btn_encode.configure(
                state="normal" if (got or (self.todo and not self.busy))
                else "disabled")
            self.btn_encode.configure(
                text=f"Encode {len(self.todo)} films with this framing"
                if len(self.todo) > 1 else "Encode with this framing")
        except (tk.TclError, AttributeError):
            pass

    def pick(self):
        got = filedialog.askopenfilename(
            title="Which film?",
            initialdir=os.path.expanduser(
                str(self.dr.sget(self.app.settings, "video.out_dir", "~"))),
            filetypes=[("Matroska", "*.mkv"),
                       ("Video", "*.mp4 *.m2ts *.ts *.avi *.mov"),
                       ("All files", "*.*")])
        if got:
            self.load(got)

    def add_folder(self):
        got = filedialog.askdirectory(
            title="Which folder?",
            initialdir=os.path.expanduser(
                str(self.dr.sget(self.app.settings, "video.out_dir", "~"))))
        if got:
            self._add([got])

    def add_files(self):
        got = filedialog.askopenfilenames(
            title="Which films?",
            initialdir=os.path.expanduser(
                str(self.dr.sget(self.app.settings, "video.out_dir", "~"))),
            filetypes=[("Video", "*.mkv *.mp4 *.m2ts *.ts *.m4v *.avi *.mov"),
                       ("All files", "*.*")])
        if got:
            self._add(list(got))

    def _add(self, paths):
        for q in paths:
            if q not in self.targets:
                self.targets.append(q)
        self.resolve()

    def clear(self):
        """Empties the preview as well as the list.

        Leaving the last film measured and on screen after its list has gone
        would put a set of numbers in front of somebody that describe a film the
        run is not going to touch, which is worse than losing the measurement."""
        self.targets, self.todo = [], []
        self.path, self.src, self.bars, self.measured = None, None, None, False
        self.f_var.set("")
        self.src_lbl.configure(text="No film chosen yet.")
        self.bars_lbl.configure(text="Not measured.")
        self.resolve()
        self.recompute()

    def resolve(self):
        """What the list comes to, counted off the main thread.

        The count walks folders and stats every file, and a large library on
        a network share must not freeze the window between clicks - same
        arrangement as the auto-encode window, same generation guard."""
        dr = self.dr
        self._resolve_gen += 1
        gen = self._resolve_gen
        if not self.targets:
            self._resolved(gen, [], [], 0, None)
            return
        self.list_lbl.configure(text="Counting...")
        v = dict(dr.sget(self.app.settings, "video"))
        targets = list(self.targets)

        def work():
            try:
                todo, skipped = dr.encode_targets(targets, v, True, False)
                err = None
            except OSError as e:
                todo, skipped, err = [], [], str(e)
            size = 0
            for q in todo:
                try:
                    size += q.stat().st_size
                except OSError:
                    pass
            self.app.ui_q.put(("worklist",
                               (self, gen, todo, skipped, size, err)))
        threading.Thread(target=work, daemon=True).start()

    def _resolved(self, gen, todo, skipped, size, err):
        if gen != self._resolve_gen:
            return
        dr = self.dr
        if err is not None:
            self.todo = []
            self.list_lbl.configure(text=f"Could not read that: {err}")
            self._enable()
            return
        self.todo = todo
        if not self.targets:
            self.list_lbl.configure(text="")
        else:
            more = f", {len(skipped)} skipped" if skipped else ""
            self.list_lbl.configure(
                text=f"{len(self.todo)} film(s) to encode, "
                     f"{dr.human_size(size)} to read{more}."
                if self.todo else
                f"Nothing left to do here{more}.")
        self._enable()
        # Nothing has been looked at yet, so show the first film found; it is
        # only here to judge the screen shape and the fit by.
        if self.path is None and self.todo:
            self.load(self.todo[0])

    def load(self, path):
        self.path = Path(path)
        if str(self.path) not in self.targets:
            self.targets.append(str(self.path))
            self.resolve()
        self.f_var.set(str(self.path))
        self.bars, self.measured = None, False
        self.bars_lbl.configure(text="Not measured.")
        self.src = None
        self.src_lbl.configure(text="Reading it...")
        self._enable()
        self._bg(self._probe)

    def _bg(self, fn):
        """Off the main thread: probing spawns ffprobe, measuring spawns six
        ffmpegs, and the window has to stay answerable while they run."""
        self.busy = True
        self._enable()
        threading.Thread(target=fn, daemon=True).start()

    def _probe(self):
        dr, out = self.dr, None
        try:
            fp = dr.find_tool("ffprobe", self.app.settings)
            st = dr.video_stream(dr.ffprobe_streams(fp, self.path) or {})
            if st and st.get("width"):
                out = (int(st["width"]), int(st["height"]), dr._sar_of(st),
                       dr.media_duration(fp, self.path),
                       str(st.get("codec_name") or "?"))
        except Exception:
            out = None
        self.app.ui_q.put(("fit", ("probed", self, out)))

    def measure(self):
        if not self.path:
            return
        self.bars_lbl.configure(text="Sampling six points across the film...")
        self._bg(self._measure)

    def _measure(self):
        dr, got = self.dr, (None, "could not measure")
        try:
            ff = dr.find_tool("ffmpeg", self.app.settings)
            dur = self.src[3] if self.src else 0.0
            raw = dr.detect_bars(ff, self.path, dur)
            w, h = (self.src[0], self.src[1]) if self.src else (0, 0)
            bars, why = dr.bars_worth_trimming(raw, w, h)
            if not bars and raw and str(self.trim_var.get()) == "on":
                bars = (min(raw[0], w - raw[2]), min(raw[1], h - raw[3]),
                        raw[2], raw[3])
                why = f"forced: keeping {bars[0]}x{bars[1]}"
            got = (bars, why)
        except Exception as e:
            got = (None, f"could not measure: {e}")
        self.app.ui_q.put(("fit", ("measured", self, got)))

    # -- called on the main thread, from the queue ------------------------
    def probed(self, out):
        self.busy = False
        self._enable()
        if not out:
            self.src = None
            self.src_lbl.configure(text="No video stream ffmpeg could read.")
            self.recompute()
            return
        w, h, sar, dur, codec = out
        self.src = out
        self.src_lbl.configure(
            text=f"{w}x{h}, pixel aspect {sar[0]}:{sar[1]}  ->  displays as "
                 f"{w * sar[0] / sar[1] / h:.3f}:1\n"
                 f"{self.dr.human_time(dur)}, {codec}")
        self.recompute()
        if str(self.trim_var.get()) != "off":
            self.measure()      # it is the interesting half; do not make them ask

    def measured_bars(self, got):
        self.busy = False
        self.measured = True
        self.bars, why = got
        self.bars_lbl.configure(text=str(why))
        self._enable()
        self.recompute()

    # -- the live readout -------------------------------------------------
    def plan(self):
        if not self.src:
            return None
        w, h, sar, _dur, _c = self.src
        bars = self.bars if str(self.trim_var.get()) != "off" else None
        return self.dr.fit_plan(w, h, sar, self.screen_var.get(),
                                self.fit_var.get(), bars,
                                self.dr.sget(self.app.settings,
                                             "video.max_height", 0))

    def recompute(self):
        """Every control recomputes rather than committing anything.

        The numbers are pure arithmetic on figures already in hand, so changing
        the screen re-answers instantly instead of re-measuring the film."""
        self.how_lbl.configure(
            text=FIT_BLURB.get(str(self.fit_var.get()), ""))
        p = self.plan()
        if p is None:
            self.out_lbl.configure(text="-")
            self.cost_lbl.configure(text="")
            self.vf_lbl.configure(text="")
            return
        ow, oh = p["out"]
        self.out_lbl.configure(text=f"{ow} x {oh}   ({p['out_dar']:.3f}:1)")
        bits = []
        if p["cropped"] != p["src"]:
            gone = 1.0 - (p["cropped"][0] * p["cropped"][1]) / float(
                p["src"][0] * p["src"][1] or 1)
            bits.append(f"{gone:.0%} of the frame was black and comes off")
        elif not self.measured and str(self.trim_var.get()) != "off":
            bits.append("borders not measured yet")
        if p["lost"] > 0.001:
            bits.append(f"{p['lost']:.0%} of the picture cropped away")
        elif p["fit"] == "pad" and (ow, oh) != tuple(p["cropped"]):
            bits.append("nothing lost - the bars are yours instead of the "
                        "player's")
        self.cost_lbl.configure(
            text="; ".join(bits) or "nothing to change",
            foreground=CLR["warn"] if p["lost"] > 0.001 else CLR["muted"])
        self.vf_lbl.configure(text="-vf " + (",".join(p["filters"])
                                            or "(nothing to do)"))

    # -- and the two things to do ----------------------------------------
    def _overrides(self):
        s = copy.deepcopy(self.app.settings)   # this run only, nothing saved
        self.dr.sset(s, "video.screen", self.screen_var.get())
        self.dr.sset(s, "video.fit", self.fit_var.get())
        self.dr.sset(s, "video.trim_bars", self.trim_var.get())
        return s

    def sample(self):
        p, path = self.plan(), self.path
        if p is None or path is None:
            return
        dr, dur = self.dr, (self.src[3] if self.src else 0.0)
        tag = f"{self.screen_var.get().replace(':', '-')} {self.fit_var.get()}"

        def work():
            ff = dr.find_tool("ffmpeg", self.app.settings)
            made = dr.fit_samples(ff, path, p, 3, None, dur, tag)
            if made:
                dr.info("Opening the first one; the rest are beside the film.")
                open_file(made[0])
        self.app._run_bg("framing stills", work)

    def encode(self):
        if not self.todo and self.path is None:
            return
        n = len(self.todo)
        shown = self.path.name if self.path is not None else "?"
        if n > 1:
            what = (f"Re-encode {n} films with this framing?\n\n"
                    f"{self.out_lbl.cget('text')}\n"
                    f"{self.cost_lbl.cget('text')}\n\n"
                    f"Those numbers are measured on {shown}. The screen shape "
                    "and the fit are the same for every film; the black "
                    "borders are found separately for each one, which is the "
                    "only way it can work.\n\nLossy copies. Originals are "
                    "left alone.")
        else:
            what = (f"Re-encode {shown} with this framing?\n\n"
                    f"{self.out_lbl.cget('text')}\n"
                    f"{self.cost_lbl.cget('text')}\n\n"
                    "This is a lossy copy. The original is left alone.")
        if not messagebox.askyesno(self.dr.APP, what):
            return
        s = self._overrides()
        if n > 1:
            started = self.app._run_bg(
                f"Fit to screen ({n} films)", self.dr.cmd_encode_batch, s,
                list(self.targets), True, False, None, rip=True)
        else:
            # One film goes straight through encode_file rather than through the
            # batch: the batch skips anything that looks like one of our own
            # outputs, which is right for a folder and wrong for a file somebody
            # has just chosen by hand - re-fitting a portable copy is a
            # perfectly reasonable thing to ask for.
            path = self.todo[0] if self.todo else self.path
            started = self.app._run_bg(
                "Fit to screen", lambda: self.dr.encode_file(s, path),
                rip=True)
        if started:
            self.close()

    def close(self):
        self.app.fit_win = None
        try:
            self.destroy()
        except tk.TclError:
            pass


class App(tk.Tk):
    _ind_seq = 0                # see _install_check_indicator

    def __init__(self, dr, settings):
        super().__init__()
        self.dr = dr
        self.settings = settings
        self.vars = {}
        self.ui_q = queue.Queue()
        self.worker = None
        self.pending_prompts = []
        self._closing = False
        self.busy = False
        self.action = None
        self._auto = False          # is the run in hand an auto-rip
        self._between = False       # ...and is it between discs right now
        self._last_stage = None     # so a new stage can retract it
        self._stalled = False       # the engine says nothing is moving
        self._wedged = False        # ...and specifically that it has gone
        # A disc has arrived and the rip has not started reporting yet. See
        # _engine_event; cleared by the first progress of the rip it belongs
        # to, because by then the strip has something better to say.
        self._detected = ""
        self._bg_stop_shown = False  # is the background's own Stop on screen
        self._moved_at = 0.0        # when the last real progress arrived
        # What the last progress report actually SAID, so a repeat of it can be
        # told from movement. See _show_progress: this is the difference between
        # "the engine spoke" and "the rip advanced".
        self._last_key = None
        self._moved = True
        self._asking = ""           # the question a prompt is waiting on
        self._asking_win = None     # ...and the dialog box asking it
        self._blocked = ""          # what the run needs a hand on the tray for
        self._blocked_attn = False  # ...and whether that is a surprise
        self._status_hold = 0.0     # a typed-at message keeps the bar this long
        self.fit_win = None
        self.retry_win = None         # the retry-missing-parts window
        self.cal_win = None           # the calibrate-quality window
        self.batch_win = None       # ...and the auto-encode one
        self.auto_win = None        # ...and the auto-rip options one
        self.disc_win = None        # ...and the one-disc options one
        self.batch_targets = []     # what it was last pointed at
        self.batch_opts = {}        # ...and how, minus anything destructive
        self.batch_overrides = {}   # ...and anything given settings of its own
        self._voices = None         # the installed speech voices, once asked for
        self._voices_asked = False
        # How the run is going, tallied from the engine's own per-disc events.
        # The counts exist in auto_rip_loop as locals and never leave it, but
        # the events that produce them already arrive here one at a time.
        self._ran = {"done": 0, "failed": 0}
        self._last_action = ""      # what the run that just ended was
        self._off_at = 0.0          # when the machine is going to switch off
        self.action_started = 0.0    # the whole RUN
        # AND THIS ONE IS THE ITEM'S. action_started is set once when a run
        # begins, so on a stack it counts up across every disc - and
        # _extra_bits was dividing THIS disc's bytes by the WHOLE run's
        # elapsed, which gets progressively more wrong with each disc. Reset
        # whenever the thing being worked on changes.
        self.disc_started = 0.0
        self._disc_key = None
        self._filters = {}
        self._tabframes = {}
        self._filled = set()
        self._scanning = False
        self._scan_retries = 0
        self._last_stats = None
        # what the background flash has put on the strip's title line, so a
        # 12-a-second poll does not repaint it 12 times a second
        self._bg_headline = None
        self._bg_seen = False        # was there background work last poll
        self._log_history = []      # (hh:mm:ss, raw line, "fg" | "bg")
        self._log_filter = "all"    # see LOG_FILTERS
        self.mini = None            # the pinned strip, when it is showing
        self._bar_anim = None       # the window bar's zoom; see _start_anim
        self.taskbar = None
        self.tray = None            # notification-area icon, while collapsed
        self._had_error = False
        # When the drive last became free, so a second seam in the same
        # session raises its own banner - see the poll in
        # _echo_progress.
        self._free_seen = 0.0
        self._was_rip = False
        self._bar_done = False
        self._collapsed = False

        self._setup_scaling()
        self.theme = set_palette(
            str(dr.sget(settings, "general.theme", "dark")).lower())
        self._setup_theme()

        self.title(f"{dr.APP} {dr.VERSION}")
        self.configure(background=CLR["bg"])
        w, h = int(1180 * self.sc), int(880 * self.sc)
        w = min(w, int(self.winfo_screenwidth() * 0.94))
        h = min(h, int(self.winfo_screenheight() * 0.92))
        self.geometry(f"{w}x{h}")
        self.minsize(int(880 * self.sc), int(620 * self.sc))
        self._set_icon()
        self._apply_chrome()

        dr.USE_COLOR = False
        dr.OUTPUT_SINK = self._sink_output
        dr.PROGRESS_SINK = self._sink_progress
        dr.PROMPT_SINK = self._sink_prompt
        dr.EVENT_SINK = self._sink_event
        dr.SHUTDOWN_SINK = self._sink_shutdown
        dr.WAIT_SINK = self._sink_wait

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # not Escape: a combobox uses that to cancel, and losing the window every
        # time a dropdown is dismissed would be its own bug report
        self.bind_all("<Control-KeyPress-m>", lambda _e: self.collapse())
        if dr.sget(settings, "general.taskbar_progress", True):
            self.taskbar = TaskbarProgress(self)
        # THE WINDOW'S BAR ZOOMS TOO, on its own clock. Started here rather
        # than where the bar is built, because FocusAnimator needs `self.app`
        # and `self.dr` to be in place before its first frame.
        self._bar_anim = FocusAnimator(self)
        self._bar_anim.start()
        if dr.sget(settings, "general.mini_monitor", False):
            self.show_mini(remember=False)
        self.after(80, self._drain)
        self.after(1000, self._tick_clock)
        self.after(150, self._prewarm)
        self.after(2500, self._prewarm_hw)
        self.refresh_drives(announce=False)
        self._log_line(f"== {dr.APP} {dr.VERSION} ==")
        self._log_line(f"  * config: {dr.CONFIG_PATH}")
        if dr.portable_mode():
            self._log_line("  * portable mode: settings and logs live beside "
                           "the script")

    # -- scaling / theme --------------------------------------------------
    def _setup_scaling(self):
        """Make text crisp at any display scaling, then size everything in
        points so it follows the display rather than being stretched."""
        dpi = 96.0
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except Exception:
            pass
        self.dpi = dpi
        self.sc = max(1.0, dpi / 96.0)
        try:
            self.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        for name, size, weight in (("TkDefaultFont", 10, "normal"),
                                   ("TkTextFont", 10, "normal"),
                                   ("TkMenuFont", 10, "normal"),
                                   ("TkHeadingFont", 10, "bold"),
                                   ("TkTooltipFont", 9, "normal")):
            try:
                f = tkfont.nametofont(name)
                f.configure(family=FONT, size=size, weight=weight)
            except tk.TclError:
                pass
        self.f_base = tkfont.Font(family=FONT, size=10)
        self.f_small = tkfont.Font(family=FONT, size=9)
        self.f_bold = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_h1 = tkfont.Font(family=FONT, size=15, weight="bold")
        self.f_h2 = tkfont.Font(family=FONT, size=11, weight="bold")
        self.f_num = tkfont.Font(family=MONO, size=10)
        # THE PERCENTAGE, AND ONLY THE PERCENTAGE. Asked for 3 Sep: "the
        # percentage should be bold", in the taskbar strip and the detached
        # one. It is the one figure that survives every width - a strip
        # squeezed to nothing is still showing the percentage - so it is the
        # right thing to emphasise, and the byte counts and clocks beside it
        # stay plain so that emphasis means something.
        self.f_numb = tkfont.Font(family=MONO, size=10, weight="bold")
        self.f_big = tkfont.Font(family=FONT, size=13, weight="bold")

    def _setup_theme(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")        # the only built-in theme that restyles
        except tk.TclError:
            pass
        st.configure(".", background=CLR["bg"], foreground=CLR["text"],
                     font=self.f_base, borderwidth=0, focuscolor=CLR["accent"])
        st.configure("Bg.TFrame", background=CLR["bg"])
        st.configure("Card.TFrame", background=CLR["card"])
        st.configure("Card.TLabel", background=CLR["card"],
                     foreground=CLR["text"])
        st.configure("TLabel", background=CLR["bg"], foreground=CLR["text"])
        # the group-box border and its title
        st.configure("Card.TLabelframe", background=CLR["card"],
                     bordercolor=CLR["border"], lightcolor=CLR["edge_hi"],
                     darkcolor=CLR["edge_lo"], borderwidth=2, relief="groove")
        st.configure("Card.TLabelframe.Label", background=CLR["card"],
                     foreground=CLR["text"], font=self.f_bold)
        st.configure("Sub.TLabel", background=CLR["card"],
                     foreground=CLR["muted"], font=self.f_small)
        # A settings group heading, and the hairline that runs off it to the
        # right. Small, spaced-out capitals in the muted colour: a heading has
        # to be findable while scrolling without competing with the setting
        # names under it, which are what the eye is actually hunting for.
        st.configure("GroupHead.TLabel", background=CLR["card"],
                     foreground=CLR["muted"], font=self.f_small)
        st.configure("Rule.TFrame", background=CLR["edge_lo"])
        st.configure("Muted.TLabel", background=CLR["card"],
                     foreground=CLR["muted"], font=self.f_small)
        st.configure("H1.TLabel", background=CLR["bg"], foreground=CLR["text"],
                     font=self.f_h1)
        st.configure("Disc.TLabel", background=CLR["card"],
                     foreground=CLR["text"], font=self.f_big)
        st.configure("Stat.TLabel", background=CLR["card"], font=self.f_num,
                     foreground=CLR["text"])
        st.configure("Badge.TLabel", background=CLR["card"],
                     foreground=CLR["text"], font=self.f_bold)

        # rows in the settings tables, striped
        for tag, bg in (("Row", CLR["row"]), ("RowAlt", CLR["row_alt"])):
            st.configure(f"{tag}.TFrame", background=bg)
            st.configure(f"{tag}.TLabel", background=bg, foreground=CLR["text"])
            st.configure(f"{tag}Desc.TLabel", background=bg,
                         foreground=CLR["muted"], font=self.f_small)
            st.configure(f"{tag}.TCheckbutton", background=bg)
            st.map(f"{tag}.TCheckbutton", background=[("active", bg)])

        st.configure("TNotebook", background=CLR["bg"], borderwidth=1,
                     bordercolor=CLR["border"], lightcolor=CLR["edge_hi"],
                     darkcolor=CLR["edge_lo"], tabmargins=(2, 4, 2, 0))
        st.configure("TNotebook.Tab", background=CLR["btn"],
                     foreground=CLR["muted"], padding=(11, 4),
                     font=self.f_base, borderwidth=1,
                     bordercolor=CLR["border"], lightcolor=CLR["edge_hi"],
                     darkcolor=CLR["edge_lo"])
        st.map("TNotebook.Tab",
               background=[("selected", CLR["card"]), ("active", CLR["btn_act"])],
               foreground=[("selected", CLR["text"])],
               expand=[("selected", (1, 1, 1, 0))])

        # One conventional button, raised the way a button has always been -
        # no accent-coloured "primary", no flat slabs.
        # Compact on purpose: the padding saved around the controls is padding
        # the activity log gets instead, and the log is what you watch.
        st.configure("TButton", background=CLR["btn"], foreground=CLR["text"],
                     padding=(8, 3), relief="raised", borderwidth=2,
                     font=self.f_base, bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["edge_hi"], darkcolor=CLR["edge_lo"],
                     focusthickness=1, focuscolor=CLR["muted"])
        st.map("TButton",
               background=[("disabled", CLR["btn"]), ("pressed", CLR["btn_act"]),
                           ("active", CLR["btn_act"])],
               relief=[("pressed", "sunken")],
               # readable-but-clearly-off: a disabled label still has to be
               # legible enough to tell you what you cannot do right now
               foreground=[("disabled", CLR["disabled"])])

        st.configure("TEntry", fieldbackground=CLR["field"],
                     foreground=CLR["text"], insertcolor=CLR["text"],
                     bordercolor=CLR["edge_lo"], lightcolor=CLR["edge_lo"],
                     darkcolor=CLR["edge_hi"], borderwidth=2, relief="sunken",
                     padding=4)
        st.map("TEntry", fieldbackground=[("disabled", CLR["card"])],
               foreground=[("disabled", CLR["faint"])])
        st.configure("TCombobox", fieldbackground=CLR["field"],
                     foreground=CLR["text"], arrowsize=14,
                     background=CLR["btn"], arrowcolor=CLR["text"],
                     bordercolor=CLR["edge_lo"], lightcolor=CLR["edge_lo"],
                     darkcolor=CLR["edge_hi"], borderwidth=2, padding=3)
        st.map("TCombobox",
               fieldbackground=[("readonly", CLR["field"]),
                                ("disabled", CLR["card"])],
               foreground=[("disabled", CLR["faint"])],
               background=[("active", CLR["btn_act"])])
        # the dropdown list is a plain Tk listbox, styled separately
        self.option_add("*TCombobox*Listbox.background", CLR["field"])
        self.option_add("*TCombobox*Listbox.foreground", CLR["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", CLR["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", CLR["bg"])
        # clam draws a hollow box with a hard-to-read tick by default; give it a
        # filled accent box so on/off is obvious at a glance
        # indicatorsize does not follow the font, so on a scaled display the box
        # collapses into an unreadable smudge unless it is sized explicitly
        ind = int(15 * self.sc)
        for cbs in ("TCheckbutton", "Row.TCheckbutton", "RowAlt.TCheckbutton",
                    "TRadiobutton", "Row.TRadiobutton"):
            st.configure(cbs, indicatorforeground=CLR["text"],
                         indicatorbackground=CLR["field"], indicatorsize=ind,
                         indicatormargin=(0, 0, 8, 0), padding=(2, 4),
                         focuscolor=CLR["card"],
                         upperbordercolor=CLR["edge_lo"],
                         lowerbordercolor=CLR["edge_lo"],
                         bordercolor=CLR["border"])
            st.map(cbs,
                   indicatorbackground=[
                       ("selected", "!disabled", CLR["field"]),
                       ("active", "!selected", CLR["btn_act"]),
                       ("disabled", CLR["card"])],
                   indicatorforeground=[("selected", CLR["text"])],
                   foreground=[("disabled", CLR["faint"])])
        st.configure("Horizontal.TProgressbar", background=CLR["accent"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["accent"], darkcolor=CLR["accent"],
                     borderwidth=2, relief="sunken",
                     thickness=int(15 * self.sc))
        # the strip docked in the taskbar gets two lines out of about 44 points
        # of height, so its bar is slimmer and carries no border
        st.configure("Mini.Horizontal.TProgressbar", background=CLR["accent"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["accent"], darkcolor=CLR["accent"],
                     borderwidth=0, thickness=int(8 * self.sc))
        # A batch has two truths at once - how this film is doing, and how the
        # evening is doing - so it gets two bars. The lower one is slimmer and
        # labelled, because two bars of the same weight would leave the eye
        # working out which is which every time it looked.
        st.configure("Total.Horizontal.TProgressbar", background=CLR["accent"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["accent"], darkcolor=CLR["accent"],
                     borderwidth=2, relief="sunken",
                     thickness=int(9 * self.sc))
        # THE OTHER FILM'S BAR, in the violet the background worker's log
        # lines use, and slimmer than the rip's: the rip in the drive is what
        # the tray is being held for, and this is work that has already let go
        # of it. Same colour in both places so the bar and the lines under it
        # read as one thing.
        st.configure("Bg.Horizontal.TProgressbar", background=CLR["bg_job"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["bg_job"], darkcolor=CLR["bg_job"],
                     borderwidth=2, relief="sunken",
                     thickness=int(11 * self.sc))
        # ...and the same two again in green, for a bar that has got there. A
        # full bar and a nearly-full one are the same shape at a glance; a green
        # one and a blue one are not.
        for name, base in (("Done.Horizontal.TProgressbar",
                            "Horizontal.TProgressbar"),
                           ("TotalDone.Horizontal.TProgressbar",
                            "Total.Horizontal.TProgressbar"),
                           ("MiniDone.Horizontal.TProgressbar",
                            "Mini.Horizontal.TProgressbar")):
            st.configure(name, background=CLR["ok"], troughcolor=CLR["track"],
                         bordercolor=CLR["edge_lo"], lightcolor=CLR["ok"],
                         darkcolor=CLR["ok"],
                         borderwidth=st.lookup(base, "borderwidth") or 0,
                         thickness=st.lookup(base, "thickness")
                         or int(15 * self.sc))
        # A percentage is a thing you drag, not a number you type. Same
        # trough as the progress bar, so the two read as the same kind of
        # control rather than as two unrelated widgets that happen to be
        # horizontal.
        st.configure("Horizontal.TScale", background=CLR["card"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["btn"], darkcolor=CLR["edge_lo"])
        st.map("Horizontal.TScale", background=[("active", CLR["card"])])
        st.configure("TScrollbar", background=CLR["btn"],
                     troughcolor=CLR["track"], bordercolor=CLR["edge_lo"],
                     lightcolor=CLR["edge_hi"], darkcolor=CLR["edge_lo"],
                     arrowcolor=CLR["text"], borderwidth=2, relief="raised")
        st.map("TScrollbar", background=[("active", CLR["btn_act"])])
        st.configure("TSeparator", background=CLR["border"])
        st.configure("Status.TLabel", background=CLR["status"],
                     foreground=CLR["muted"], font=self.f_small,
                     padding=(8, 4), relief="sunken", borderwidth=1)
        for rb, bg in (("Card.TRadiobutton", CLR["card"]),
                       ("Row.TRadiobutton", CLR["row"])):
            # a sunken well with a dot in the text colour, to match the drawn
            # check boxes - a white indicator would be a bright hole in a dark
            # window
            st.configure(rb, background=bg, focuscolor=bg, padding=(2, 3),
                         indicatorsize=int(13 * self.sc),
                         indicatorbackground=CLR["field"],
                         indicatorforeground=CLR["text"],
                         upperbordercolor=CLR["edge_lo"],
                         lowerbordercolor=CLR["edge_lo"])
            st.map(rb, background=[("active", bg)],
                   indicatorbackground=[("selected", CLR["field"])])
        self.cb_style = self._install_check_indicator(st) or "TCheckbutton"

    # -- check indicators ------------------------------------------------
    def _draw_box(self, size, fill, border, tick=None):
        # Composed in a list and handed over in one put. A put per pixel is a
        # Tcl round trip per pixel, and the tick alone is several hundred of
        # them - which is most of the cost of switching theme.
        rows = [[fill] * size for _ in range(size)]
        for i in range(size):                      # 1px border
            rows[0][i] = rows[size - 1][i] = border
            rows[i][0] = rows[i][size - 1] = border
        if tick:
            # a real checkmark: two strokes, thickened so it survives scaling
            w = max(1, size // 8)
            pts = [(0.24, 0.54), (0.42, 0.72), (0.78, 0.30)]
            segs = [(pts[0], pts[1]), (pts[1], pts[2])]
            for (x0, y0), (x1, y1) in segs:
                x0, y0, x1, y1 = (x0 * size, y0 * size, x1 * size, y1 * size)
                steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
                for s in range(steps + 1):
                    x = x0 + (x1 - x0) * s / steps
                    y = y0 + (y1 - y0) * s / steps
                    for dx in range(-w // 2, w // 2 + 1):
                        for dy in range(-w // 2, w // 2 + 1):
                            px, py = int(x) + dx, int(y) + dy
                            if 1 <= px < size - 1 and 1 <= py < size - 1:
                                rows[py][px] = tick
        img = tk.PhotoImage(width=size, height=size)
        img.put(" ".join("{" + " ".join(r) + "}" for r in rows))
        return img

    def _install_check_indicator(self, st):
        """clam draws its 'checked' mark as a diagonal cross, so a ticked box
        reads as a rejection. Replace the indicator with drawn images."""
        # a sunken box with a plain tick, the way a checkbox has always looked -
        # no accent fill
        s = max(13, int(15 * self.sc))
        self._chk = {
            "off": self._draw_box(s, CLR["field"], CLR["edge_lo"]),
            "on": self._draw_box(s, CLR["field"], CLR["edge_lo"], CLR["text"]),
            "dis": self._draw_box(s, CLR["card"], CLR["border"]),
        }
        # A ttk element cannot be redefined, and the images are palette-coloured,
        # so each theme needs its own element. Without the counter the second
        # install fails and every checkbox silently reverts to clam's cross.
        App._ind_seq += 1
        elem = f"DR{App._ind_seq}.indicator"
        base = f"DR{App._ind_seq}.TCheckbutton"
        try:
            st.element_create(elem, "image", self._chk["off"],
                              ("disabled", self._chk["dis"]),
                              ("selected", self._chk["on"]),
                              sticky="", padding=0)
        except tk.TclError:
            return None
        st.layout(base, [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                (elem, {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w",
                                       "children": [
                                           ("Checkbutton.label",
                                            {"sticky": "nswe"})]})]})])
        for name, bg in ((base, CLR["card"]),
                         (f"Row.{base}", CLR["row"]),
                         (f"RowAlt.{base}", CLR["row_alt"])):
            st.configure(name, background=bg, padding=(2, 4),
                         focuscolor=bg, font=self.f_base)
            st.map(name, background=[("active", bg)],
                   foreground=[("disabled", CLR["faint"])])
        return base

    def _apply_chrome(self):
        apply_window_chrome(self, self.theme == "dark", caption=CLR["bg"],
                            text=CLR["text"], border=CLR["border"])

    # -- pinned strip / collapsing ----------------------------------------
    def show_mini(self, remember=True):
        if self.mini is not None and self.mini.winfo_exists():
            return
        self.mini = MiniMonitor(self)
        if remember:
            self._save_setting("general.mini_monitor", True)
        self.mini_var.set(True)
        self.dock_var.set(self.mini.docked)
        # whatever is on screen now, rather than a blank strip until the next
        # progress update arrives a second later
        if self.busy and self._last_stats:
            self._show_progress(self._last_stats)
        elif self.busy:
            self.mini.show(self.action or "Working", None, "", "",
                           stage=self._doing_line())
        else:
            self.mini.idle(failed=self._had_error)

    def hide_mini(self, remember=True):
        if self.mini is not None:
            try:
                self.mini.destroy()
            except tk.TclError:
                pass
            self.mini = None
        if remember:
            self._save_setting("general.mini_monitor", False)
        self.mini_var.set(False)
        if self._collapsed:
            # the strip was the only thing left to look at
            self.uncollapse()

    def toggle_mini(self):
        if self.mini is not None and self.mini.winfo_exists():
            self.hide_mini()
        else:
            self.show_mini()

    def remember_mini_pos(self, x, y):
        self._save_setting("general.mini_monitor_pos", f"{x},{y}")

    def remember_mini_size(self, w, h=None):
        self._save_setting("general.mini_monitor_width", int(w))
        if h is not None:
            self._save_setting("general.mini_monitor_height", int(h))

    def toggle_mini_dock(self):
        docked = not (self.mini is not None and self.mini.docked)
        self.set_mini_dock(docked)

    def set_mini_dock(self, docked):
        self._save_setting("general.mini_monitor_dock",
                           "taskbar" if docked else "float")
        self.dock_var.set(docked)
        # the two shapes are different widths, and a position on the taskbar
        # means nothing on the desktop - start each mode from its own default
        self.dr.sset(self.settings, "general.mini_monitor_pos", "")
        self.dr.sset(self.settings, "general.mini_monitor_width", 0)
        self.dr.sset(self.settings, "general.mini_monitor_height", 0)
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.set_docked(docked)

    def reset_mini_geometry(self):
        self.dr.sset(self.settings, "general.mini_monitor_pos", "")
        self.dr.sset(self.settings, "general.mini_monitor_height", 0)
        self._save_setting("general.mini_monitor_width", 0)
        if self.mini is not None and self.mini.winfo_exists():
            self.mini._place()
            self.mini.reshape(force=True)

    def set_mini_detail(self, level):
        """How much the strip shows, from its own menu."""
        self._save_setting("general.mini_monitor_detail", level)
        self.detail_var.set(level)
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.reshape(force=True)

    def repaint_mini(self):
        """Put the current state back on the strip after it changed shape."""
        if self.mini is None or not self.mini.winfo_exists():
            return
        # the one field that cannot be rebuilt from the rip's current state
        self.mini.log_fill(self._log_history)
        # THE OTHER TASK, IF THAT IS WHAT THE STRIP IS SHOWING. Every path
        # below this rebuilds the RIP's state, which is the right answer to
        # "put the current state back" and the wrong one while the strip has
        # been turned over to the background job - so the view is asked first.
        # This is also what catches a job that ended while the view was up:
        # _paint_bg_strip says there is nothing to describe, and the foreground
        # comes back through the same paths as always.
        if getattr(self.mini, "_bg_view", False):
            if self._paint_bg_strip():
                return
            self.mini._bg_view = False
        if self._asking or self._blocked_attn:
            self.mini.unstall()         # so the layout is rebuilt for this size
            self._paint_asking()
        elif self.busy and self._stalled:
            self.mini.unstall()
            self._paint_stall()
        elif self.busy and self._between:
            # Between discs in an auto-rip: the last disc's numbers are over and
            # the next one's have not begun, so there is nothing to draw a bar
            # from and nothing honest to put in the fields. This is also where
            # the blink lands when it ends - _flash_step repaints through here.
            # NOT "1 done" WHILE ONE IS STILL BEING MADE. Reported 1 Sep
            # from the taskbar: "Auto-rip - 1 done - waiting for next item..."
            # with the tracks of that same disc still being pruned behind it.
            # The disc is off the drive, which is what the count means, and
            # that is not what a reader takes from "done" - so the count waits
            # until there is nothing outstanding, and the room goes to saying
            # what is actually happening instead.
            summary = ("" if self.dr.BG.pending() else self._run_summary())
            self.mini.idle(f"{self.action} - "
                           + (f"{summary} - " if summary else "")
                           + (self._detected or "waiting for next item..."),
                           failed=self._had_error,
                           background=self._bg_idle_line())
        elif self.busy and self._last_stats:
            self._echo_progress(self._last_stats)
        elif self.busy:
            self.mini.show(self.action or "Working", None, "", "",
                           stage=self._doing_line())
        else:
            self._idle_line()

    def speed_ok(self, what="This run"):
        """True to carry on. Asked before an unattended run, not during it.

        A run left going overnight is the one case where a forty-fold slowdown
        matters most and gets noticed last, so the question is put while
        somebody is still sitting there to answer it."""
        try:
            why = self.dr.hw_warning(self.settings)
        except Exception:
            return True                 # never block a run on this check
        if not why:
            return True
        self._log_line("  [!] " + why)
        return messagebox.askyesno(
            self.dr.APP, f"{what} will be slow.\n\n{why}\n\nStart anyway?")

    def _freeze_inputs(self, busy):
        """Grey out what cannot be changed once a run has started."""
        state = "disabled" if busy else "normal"
        for w in list(getattr(self, "mode_btns", {}).values()) \
                + [getattr(self, "mode_custom", None),
                   getattr(self, "out_entry", None),
                   getattr(self, "out_browse", None)] \
                + list(getattr(self, "drive_btns", {}).values()):
            try:
                if w is not None:
                    w.configure(state=state)
            except tk.TclError:
                pass
        for w, ready in ((getattr(self, "drive_box", None), "readonly"),
                         (getattr(self, "mtype_box", None), "readonly")):
            try:
                if w is not None:
                    w.configure(state="disabled" if busy else ready)
            except tk.TclError:
                pass
        try:
            self.dry_cb.configure(state=state)
        except (AttributeError, tk.TclError):
            pass

    def _save_setting(self, dotted, value):
        self.dr.sset(self.settings, dotted, value)
        try:
            self.dr.save_config(self.settings, self.dr.CONFIG_PATH)
        except OSError as e:
            self._log_line(f"  [!] could not save {dotted}: {e}")

    def collapse(self):
        """Put the window in the notification area.

        withdraw rather than iconify: the point is to be out of the way, and an
        iconified window keeps its taskbar button. The strip is switched on first
        if it was off, because a window that has left both the screen and the
        taskbar would otherwise leave a running rip reporting to nothing but a
        tooltip nobody is hovering over."""
        if self.tray is None:
            self.tray = TrayIcon(self)
        if not self.tray.add():
            # no tray icon means no way back except the strip, so make sure of it
            self._log_line("  [!] the notification area would not take an icon; "
                           "minimising to the taskbar instead")
            if self.mini is None or not self.mini.winfo_exists():
                self.show_mini()
            self._collapsed = True
            try:
                self.iconify()
            except tk.TclError:
                pass
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.reassert()    # minimising the owner hides it too
            return
        if self.mini is None or not self.mini.winfo_exists():
            self.show_mini()
        self._collapsed = True
        self._update_tray_tip()
        try:
            self.withdraw()
        except tk.TclError:
            pass
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.reassert()    # withdrawing the owner took it down with it
        self._status("In the notification area - click the icon to come back")

    def uncollapse(self):
        """Bring the window back, and whatever it is asking with it.

        While a shutdown is counting down this stops it instead: that is what
        the strip is asking to be double-clicked for, and it is the only thing
        anyone watching a taskbar strip can do about it.

        This is what the strip's double-click and the tray icon both call, and
        while a question is up it is the answer to "the strip says it wants me,
        now what" - the dialog is what is actually wanted, and it can be behind
        anything. Raised after the window, because it is transient on it and the
        window coming forward would otherwise put it underneath."""
        if self._off_at:
            self.cancel_shutdown()
            return
        self._collapsed = False
        if self.tray is not None:
            self.tray.remove()
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        win = self._asking_win
        if win is not None:
            try:
                if win.winfo_exists():
                    win.deiconify()
                    win.lift()
                    win.focus_force()
            except tk.TclError:
                self._asking_win = None

    def _tray_menu(self):
        """Right-click on the tray icon. Built fresh each time so it reflects
        whether anything is running."""
        m = tk.Menu(self, tearoff=0, background=CLR["card"],
                    foreground=CLR["text"], activebackground=CLR["btn_act"],
                    activeforeground=CLR["text"], activeborderwidth=0,
                    borderwidth=1, relief="solid", font=self.f_base)
        m.add_command(label="Show DiscRipper", command=self.uncollapse)
        m.add_separator()
        m.add_checkbutton(label="Pinned progress strip", variable=self.mini_var,
                          command=self.toggle_mini)
        if self.busy:
            m.add_separator()
            m.add_command(label=f"Stop {(self.action or 'rip').split()[0]}",
                          command=self.action_stop)
        m.add_separator()
        m.add_command(label="Exit", command=self._on_close)
        try:
            # a tray menu has to be told to take focus, or it stays up after the
            # click that dismisses it
            self.tk.call("focus", "-force", self._w)
            x, y = self.winfo_pointerxy()
            m.tk_popup(x, y)
        except tk.TclError:
            pass
        finally:
            m.grab_release()

    def _update_tray_tip(self):
        if self.tray is None or not self.tray.ok:
            return
        app = self.dr.APP
        if not self.busy:
            self.tray.tip(f"{app} - last rip failed" if self._had_error
                          else f"{app} - no rip in progress")
            return
        info = self._last_stats or {}
        frac, eta = info.get("frac"), info.get("eta")
        bits = [self.action or "working"]
        if frac is not None:
            bits.append(f"{frac * 100:.1f}%")
        if eta:
            bits.append(f"{eta} left")
        label = str(self.dr.JOB.get("label") or "")
        self.tray.tip(f"{app} - " + "  ".join(bits) + (f"\n{label}" if label
                                                       else ""))

    def _set_icon(self):
        """A small procedural disc, so the taskbar entry isn't the Tk feather."""
        try:
            n = 32
            img = tk.PhotoImage(width=n, height=n)
            grid = self.dr.disc_icon_pixels(n)
            # One put per contiguous run rather than one per pixel, and nothing
            # put where the grid is clear - untouched pixels are what keeps the
            # corners, and the gaps between the trailing blocks, transparent.
            for y in range(n):
                x = 0
                while x < n:
                    if grid[y][x] is None:
                        x += 1
                        continue
                    x0, run = x, []
                    while x < n and grid[y][x] is not None:
                        run.append("#%02x%02x%02x" % grid[y][x])
                        x += 1
                    img.put("{" + " ".join(run) + "}", to=(x0, y))
            self._icon = img
            self.iconphoto(True, img)
        except Exception:
            pass
        self._set_window_icons()

    def _set_window_icons(self):
        """Hand the window real HICONs, at the two sizes Windows asks for.

        iconphoto dresses the title bar, but the taskbar button reads the
        window's ICON_BIG and Tk has no way to set one - which is the other half
        of why the button showed python.exe's icon. These are built from
        the engine's disc_icon_bits, the same bytes behind the tray icon, so all three stay
        in step; and each is drawn at the size Windows actually asked for rather
        than letting the shell stretch one 32px image to fit."""
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.CreateIconFromResourceEx.restype = wintypes.HICON
            self.update_idletasks()             # the HWND has to exist first
            hwnd = int(self.wm_frame(), 16)
            fresh = []
            for which, metric, fallback in ((1, 11, 32), (0, 49, 16)):
                px = u32.GetSystemMetrics(metric) or fallback   # CXICON, CXSMICON
                bits = self.dr.disc_icon_bits(px)
                buf = ctypes.create_string_buffer(bits, len(bits))
                hicon = u32.CreateIconFromResourceEx(
                    buf, len(bits), True, 0x00030000, px, px, 0)
                if not hicon:
                    continue
                fresh.append(hicon)
                u32.SendMessageW(hwnd, 0x0080, which, hicon)     # WM_SETICON
            # WM_SETICON does not copy, so these have to outlive the call - and
            # the ones from a previous theme rebuild are only now unreferenced.
            for stale in getattr(self, "_hicons", ()):
                u32.DestroyIcon(stale)
            self._hicons = fresh
        except Exception:
            pass

    # -- construction -----------------------------------------------------
    # What used to be four dropdowns across the top. Every one of these is
    # about one tab in particular, so it lives on that tab: a menubar is where
    # things go when nobody has decided where they belong.
    #
    # Four of them are not here at all, deliberately. File > Exit is the close
    # button. View > Dark/Light is general.theme, a row on the General tab that
    # has always been there and that the menu duplicated. Tools > Fit a film to
    # a screen is the button of the same name on the Rip tab. And the strip's
    # "reset size and position" stays on the strip's own right-click menu,
    # where the thing being reset is under the pointer.
    TAB_ACTIONS = {
        # Only the ones that had nowhere else. The Tools tab already had
        # doctor, setup, bundle and the MakeMKV key on it, and the Alerts tab
        # already had both sound tests - the menu had been offering a second
        # copy of each all along, which is what a menubar does to a program.
        "general": (("Open the config file", "_edit_config"),
                    ("Open the log folder", "_open_logs"),
                    ("About " + "DiscRipper", "_about")),
    }

    def _tab_actions(self, parent, row, section):
        """The one-off jobs that belong to this tab, as buttons on it."""
        items = self.TAB_ACTIONS.get(section)
        if not items:
            return row
        card = Card(parent, "Things you can do from here")
        card.grid(row=row, column=0, sticky="we", padx=2, pady=(0, 10))
        b = card.body
        holder = ttk.Frame(b, style="Card.TFrame")
        holder.grid(row=0, column=0, sticky="we")
        for label, target in items:
            fn = getattr(self, target, None)
            if fn is None:
                engine = getattr(self.dr, target, None)
                if engine is None:
                    continue
                fn = (lambda f=engine, t=label:
                      self._run_bg(t, f, self.settings))
            ttk.Button(holder, text=label, command=fn).pack(side="left",
                                                            padx=(0, 8))
        return row + 1

    def _about(self):
        dr = self.dr
        messagebox.showinfo(
            f"About {dr.APP}",
            f"{dr.APP} {dr.VERSION}\n\n"
            "Archival-grade optical disc ripping.\n"
            "Audio CD to FLAC, DVD/Blu-ray to lossless MKV, data disc to a\n"
            "bit-exact image.\n\n"
            f"Config: {dr.CONFIG_PATH}\n"
            f"Logs:   {(dr.SCRIPT_DIR if dr.portable_mode() else dr.APPDATA_DIR) / 'logs'}\n"
            f"Engines: {dr.TOOLS_DIR}")

    def _build(self):
        # These four outlived the menubar: the tray menu reads mini_var, the
        # strip reads dock_var and detail_var, and set_theme writes theme_var.
        self.theme_var = tk.StringVar(value=self.theme)
        self.mini_var = tk.BooleanVar(
            value=self.mini is not None and self.mini.winfo_exists())
        self.dock_var = tk.BooleanVar(
            value=str(self.dr.sget(self.settings, "general.mini_monitor_dock",
                                   "float")).lower() == "taskbar")
        self.detail_var = tk.StringVar(
            value=str(self.dr.sget(self.settings, "general.mini_monitor_detail",
                                   "auto")).lower())
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 4))
        self.nb = nb

        # The note sits on the tab strip's own line, out to the right where
        # there are no tabs - placed rather than packed, because a notebook
        # draws its own tab row and will not take a passenger in it. It is not
        # decoration: it is how many drives were found and which config file is
        # in use, which is what somebody looks for first when either is wrong.
        self.head_note = ttk.Label(self, text="", style="TLabel",
                                   foreground=CLR["muted"], font=self.f_small,
                                   background=CLR["bg"])
        self.head_note.place(in_=nb, relx=1.0, x=int(-10 * self.sc),
                             y=int(6 * self.sc), anchor="ne")
        self.head_note.lift()

        self.tab_rip = ttk.Frame(nb, style="Bg.TFrame")
        nb.add(self.tab_rip, text="  Rip  ")
        self._build_rip_tab(self.tab_rip)

        for section, label, blurb in SECTION_TABS:
            frame = ttk.Frame(nb, style="Bg.TFrame")
            nb.add(frame, text=f"  {label}  ")
            self._tabframes[section] = (frame, label, blurb)

        self.tab_profiles = ttk.Frame(nb, style="Bg.TFrame")
        nb.add(self.tab_profiles, text="  Profiles  ")
        self._build_profiles_tab(self.tab_profiles)

        self.status = ttk.Label(self, text="Ready", style="Status.TLabel",
                                anchor="w")
        self.status.pack(fill="x", side="bottom")
        self._set_busy(False)   # also hides the idle progress block

        # The settings tabs are built when they are first looked at. Between
        # them they are two-thirds of the widgets in the window, and every one
        # of them is behind the Rip tab when it opens. Bound only now that every
        # tab exists, so the adds above don't fire it on the way past.
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # -- rip tab ----------------------------------------------------------
    def _build_mode_card(self, b):
        """Two buttons and the truth about what the second one does.

        The modes write real settings rather than hiding them, so the tabs
        afterwards say what will actually happen - but a mode that deletes the
        lossless copy and drops three audio tracks has to say so where it is
        chosen, not in a log after the fact."""
        dr = self.dr
        b.columnconfigure(1, weight=1)
        row = ttk.Frame(b, style="Card.TFrame")
        row.grid(row=0, column=0, columnspan=2, sticky="we")
        self.mode_var = tk.StringVar(value=dr.mode_of(self.settings))
        self.mode_btns = {}
        for name, spec in dr.MODES.items():
            rb = ttk.Radiobutton(row, text=spec["label"], value=name,
                                 variable=self.mode_var,
                                 style="Card.TRadiobutton",
                                 command=lambda n=name: self.set_mode(n))
            rb.pack(side="left", padx=(0, 18))
            Tip(rb, spec["detail"])
            self.mode_btns[name] = rb
        self.mode_custom = ttk.Radiobutton(
            row, text="Custom", value="custom", variable=self.mode_var,
            style="Card.TRadiobutton", command=lambda: None)
        self.mode_custom.pack(side="left")
        Tip(self.mode_custom, "The settings no longer match either mode. "
                              "Nothing is wrong with that - pick a mode to go "
                              "back to a known one.")
        # On the same line as the buttons it belongs to, not under them: it is
        # one short sentence about the thing immediately to its left.
        self.mode_blurb = ttk.Label(row, text="", style="Muted.TLabel",
                                    justify="left", font=self.f_small,
                                    wraplength=int(520 * self.sc))
        self.mode_blurb.pack(side="left", padx=(18, 0))
        self.mode_detail = ttk.Label(b, text="", style="Muted.TLabel",
                                     justify="left",
                                     wraplength=int(820 * self.sc))
        self.mode_detail.grid(row=2, column=0, columnspan=2, sticky="w",
                              pady=(4, 0))
        self.mode_note = ttk.Label(b, text="", style="Card.TLabel",
                                   justify="left", foreground=CLR["warn"],
                                   wraplength=int(820 * self.sc))
        self.mode_note.grid(row=3, column=0, columnspan=2, sticky="w",
                            pady=(8, 0))
        self._paint_mode()

    def _paint_mode(self):
        """Say what the selected mode is and, if it costs something, what."""
        dr = self.dr
        name = dr.mode_of(self.settings)
        self.mode_var.set(name)
        spec = dr.MODES.get(name)
        if spec is None:
            self.mode_blurb.configure(
                text="Custom - the settings are whatever you last set them to.")
            self.mode_detail.configure(
                text="Pick a mode above to go back to a known set. Nothing is "
                     "lost by looking; it only changes settings.")
            self.mode_note.configure(text="")
            self.mode_note.grid_remove()
            return
        self.mode_blurb.configure(text=spec["blurb"])
        self.mode_detail.configure(text=spec["detail"])
        notes = dr.MODE_NOTES.get(name) or ()
        if notes:
            self.mode_note.configure(
                text="\n".join("\u2022  " + n for n in notes))
            self.mode_note.grid()
        else:
            self.mode_note.configure(text="")
            self.mode_note.grid_remove()

    def set_mode(self, name):
        """Apply a mode, save it, and rebuild whatever settings tabs are up."""
        dr = self.dr
        changed = dr.apply_mode(self.settings, name)
        if changed is None:
            return
        try:
            dr.save_config(self.settings, dr.CONFIG_PATH)
        except OSError as e:
            self._log_line(f"  [X] could not save config: {e}")
        spec = dr.MODES[name]
        self._log_line(f"  * Mode: {spec['label']} - {spec['blurb']}")
        for line in changed:
            self._log_line(f"      {line}")
        for line in dr.MODE_NOTES.get(name) or ():
            self._log_line(f"  [!] {line}")
        self._status(f"Mode: {spec['label']}"
                     + (f"  ({len(changed)} setting(s) changed)"
                        if changed else ""))
        self._paint_mode()
        # the settings tabs are showing the old numbers; rebuild the built ones
        for section in list(self._filled):
            try:
                self._build_rows(section, self._filters.get(
                    section, tk.StringVar()).get()
                    if section in self._filters else "")
            except (tk.TclError, KeyError):
                pass

    def _build_rip_tab(self, root):
        # Deliberately not scrollable: the log already scrolls, and a scrollable
        # page wrapped around a scrollable log gives two adjacent scrollbars that
        # fight each other. The log absorbs the spare height instead.
        p = ttk.Frame(root, style="Bg.TFrame")
        p.pack(fill="both", expand=True)
        p.columnconfigure(0, weight=1)
        r = 0

        # ---- what do you want out of this disc
        card = Card(p, "Mode")
        card.grid(row=r, column=0, sticky="we", padx=2, pady=(4, 10))
        r += 1
        self._build_mode_card(card.body)

        # ---- where things come from and where they go, in one card
        #
        # They are one decision: the drive is the input, the folder is the
        # output, and two cards to say so cost a line of log each. Only the
        # general output folder - the per-kind ones are a refinement and live on
        # their own tabs.
        card = Card(p, "Input and output locations")
        card.grid(row=r, column=0, sticky="we", padx=2, pady=(0, 10))
        r += 1
        b = card.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Output folder", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12))
        self.out_var = tk.StringVar(
            value=str(self.dr.sget(self.settings, "general.out_dir", "") or ""))
        self.vars["general.out_dir"] = self.out_var
        ent = self.out_entry = ttk.Entry(b, textvariable=self.out_var)
        ent.grid(row=0, column=1, sticky="we")
        for seq in ("<FocusOut>", "<Return>"):
            ent.bind(seq, lambda e: self._set_and_save("general.out_dir",
                                                       self.out_var.get()))
        Tip(ent, "Where finished rips go. Every task that writes a file needs "
                 "this set, and nothing will start until it is - a per-kind "
                 "folder on the other tabs still overrides it for that kind.")
        # Flush against the box: it is not a third control on the row, it is
        # the other way of filling in the one to its left.
        self.out_browse = ttk.Button(
            b, text="Browse",
            command=lambda: self._browse("general.out_dir", self.out_var))
        # sticky w, or it is centred in a column the drive buttons below have
        # made wide and sits adrift of the box it belongs to
        self.out_browse.grid(row=0, column=2, padx=0, sticky="w")
        self.out_open = ttk.Button(b, text="Open", command=self._open_out)
        self.out_open.grid(row=0, column=3, padx=(6, 0), sticky="w")
        Tip(self.out_open, "Open this folder in Explorer.")

        # The drive on the line below, with what is in it beside the buttons in
        # a smaller font rather than on two lines of its own. The disc's own
        # description gets a line only when there is one to give.
        ttk.Label(b, text="Disc drive", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
        self.drive_var = tk.StringVar()
        # WIDE ENOUGH FOR THE WHOLE LINE. At 30 characters even an ordinary
        # entry lost its state - "D:   HL-DT-ST BD-RE BU40N   [disc present]"
        # is 42 - and the fallback description ran off unreadably. Reported
        # 1 Sep from a screenshot. See _scan_drives for what goes in it.
        self.drive_box = ttk.Combobox(b, textvariable=self.drive_var,
                                      state="readonly", width=46)
        self.drive_box.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.drive_box.bind("<<ComboboxSelected>>",
                            lambda e: self.identify_disc())
        bb = ttk.Frame(b, style="Card.TFrame")
        bb.grid(row=1, column=2, columnspan=2, sticky="e", padx=(10, 0),
                pady=(8, 0))
        self.disc_title = ttk.Label(bb, text="No disc identified yet",
                                    style="Muted.TLabel", font=self.f_small)
        self.disc_title.pack(side="left", padx=(0, 10))
        self.drive_btns = {}
        for text, fn in (("Rescan", lambda: self.refresh_drives()),
                         ("Identify", self.identify_disc),
                         ("Eject", lambda: self._tray(True)),
                         ("Close tray", lambda: self._tray(False))):
            btn = ttk.Button(bb, text=text, command=fn)
            btn.pack(side="left", padx=2)
            self.drive_btns[text] = btn
        self.disc_sub = ttk.Label(b, text="", style="Muted.TLabel",
                                  justify="left", font=self.f_small,
                                  wraplength=int(900 * self.sc))
        self.disc_sub.grid(row=2, column=0, columnspan=4, sticky="w",
                           pady=(6, 0))
        self._show_disc_sub()

        # ---- actions
        card = Card(p, "Actions")
        card.grid(row=r, column=0, sticky="we", padx=2, pady=(0, 10))
        r += 1
        b = card.body
        row1 = ttk.Frame(b, style="Card.TFrame")
        row1.grid(row=0, column=0, sticky="we")
        self.act_buttons = {}
        specs = [
            ("rip", "Rip this disc", self.action_rip, "TButton",
             "Rip the disc that is in the drive, using the settings on the "
             "other tabs."),
            ("auto", "Auto-rip", self.action_auto, "TButton",
             "Unattended: identify each disc as it is inserted, rip it the "
             "right way, eject, repeat."),
            ("info", "Title list", self.action_info, "TButton",
             "Scan the disc and show its titles, lengths and languages "
             "without ripping."),
            ("autoenc", "Auto-encode files", self.action_batch_files,
             "TButton",
             "Auto-rip pointed at a hard drive instead of the disc drive: "
             "give it films or folders of them and walk away. Uses the mode "
             "above, skips what is already done, and leaves the originals "
             "alone unless you tell it otherwise."),
            ("calibrate", "Calibrate quality", self.action_calibrate,
             "TButton",
             "Encode one passage of a film several ways and pick by eye. The "
             "quality search aims at VMAF 90, which is a number from a paper - "
             "this is how you replace it with your own. Your answer can apply "
             "to every encode afterwards, portable rips included."),
            ("retry", "Retry missing parts", self.action_retry, "TButton",
             "Fill in the gaps left by a disc that would not read. Reads only "
             "the sectors the map says were never got, straight into the image "
             "already on disk - nothing already read is read again. Worth a "
             "second go after cleaning the disc."),
            ("fit", "Fit to screen", self.action_fit, "TButton",
             "Make a film fill a screen: trim the black borders baked into "
             "the picture, and choose what happens where its shape and the "
             "screen's disagree. Measures it, shows you stills, then encodes."),
        ]
        for i, (key, text, fn, style, tip) in enumerate(specs):
            btn = ttk.Button(row1, text=text, command=fn, style=style)
            btn.pack(side="left", padx=(0, 8))
            Tip(btn, tip)
            self.act_buttons[key] = btn

        row2 = ttk.Frame(b, style="Card.TFrame")
        row2.grid(row=1, column=0, sticky="we", pady=(10, 0))
        self.dry_var = tk.BooleanVar(value=bool(self.dr.DRY_RUN))
        cb = self.dry_cb = ttk.Checkbutton(row2, text="Dry run",
                                           variable=self.dry_var,
                                           style=self.cb_style,
                                           command=self._toggle_dry)
        cb.pack(side="left")
        Tip(cb, "Print every external command instead of running it. Nothing "
                "touches the disc.")
        ttk.Label(row2, text="   Media type", style="Card.TLabel").pack(side="left")
        self.mtype_var = tk.StringVar(
            value=str(self.dr.sget(self.settings, "general.media_type", "auto")))
        mt = self.mtype_box = ttk.Combobox(
            row2, textvariable=self.mtype_var, width=10, state="readonly",
            values=choices_for(self.dr, "general.media_type"))
        mt.pack(side="left", padx=6)
        mt.bind("<<ComboboxSelected>>",
                lambda e: self._set_and_save("general.media_type",
                                             self.mtype_var.get()))
        Tip(mt, "auto = identify each disc from what is on it. Anything else "
                "forces that type; auto-rip deliberately ignores a forced type.")

        # ---- activity: what is running right now
        self.card_act = Card(p, "Activity")
        self.card_act.grid(row=r, column=0, sticky="we", padx=2, pady=(0, 10))
        r += 1
        b = self.card_act.body
        b.columnconfigure(0, weight=1)

        line = ttk.Frame(b, style="Card.TFrame")
        line.grid(row=0, column=0, sticky="we")
        line.columnconfigure(1, weight=1)
        self.act_dot = ttk.Label(line, text="●", style="Card.TLabel",
                                 foreground=CLR["faint"], font=self.f_big)
        self.act_dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        wrap = ttk.Frame(line, style="Card.TFrame")
        wrap.grid(row=0, column=1, sticky="we")
        self.act_title = ttk.Label(wrap, text="Idle", style="Badge.TLabel")
        self.act_title.pack(anchor="w")
        self.act_sub = ttk.Label(wrap, text="Nothing is running.",
                                 style="Muted.TLabel")
        self.act_sub.pack(anchor="w", pady=(1, 0))
        # A SECOND LINE, for the work that no longer needs the drive. The
        # engine's background worker reports into BG_STATE rather than JOB
        # precisely so it can have its own line here instead of fighting the
        # main one - see BackgroundJobs. Hidden while there is nothing in it,
        # because an empty row on the strip reads as something broken.
        self.act_bg = ttk.Label(wrap, text="", style="Muted.TLabel",
                                foreground=CLR["accent"])
        # AT THE FAR RIGHT OF THE TOP ROW, which is where it was asked for and
        # is also the only place it does not push the title around: the title
        # grows with the film's name, this does not.
        self.act_right = ttk.Label(line, text="", style="Muted.TLabel",
                                   foreground=CLR["accent"])
        self.act_right.grid(row=0, column=2, sticky="e", padx=(10, 0))
        # TWO STOPS, because there are two pieces of work - and the second one
        # lives down beside the bar it is about rather than up here. See
        # watch_row below.
        # THE SKIP CONTROL, left of the rip's Stop. A box and a button, so the
        # number is visible and editable rather than being a choice between
        # fixed amounts nobody's disc matches.
        self.skip_box = ttk.Frame(line, style="Card.TFrame")
        self.skip_secs = tk.StringVar(value="20")
        ttk.Label(self.skip_box, text="Skip", style="Muted.TLabel",
                  font=self.f_small).pack(side="left", padx=(0, 4))
        self.skip_entry = ttk.Entry(self.skip_box, width=4,
                                    textvariable=self.skip_secs,
                                    justify="right", font=self.f_small)
        self.skip_entry.pack(side="left")
        ttk.Label(self.skip_box, text="s of playback", style="Muted.TLabel",
                  font=self.f_small).pack(side="left", padx=(4, 6))
        self.btn_skip = ttk.Button(self.skip_box, text="Skip",
                                   command=self.action_skip_playback)
        self.btn_skip.pack(side="left")
        Tip(self.skip_box,
            "Step the sweep past this much of the film and carry on reading "
            "after it.\n\nFor a stretch that has been refusing for long "
            "enough that you would rather have the rest of the disc. The "
            "skipped part is recorded as UNTRIED, not unreadable - nothing "
            "in it was measured - so --retry-skipped can come back for it "
            "later if you change your mind.")
        self._skip_shown = False

        self.btn_stop = ttk.Button(line, text="Stop", command=self.action_stop,
                                   state="disabled")
        self.btn_stop.grid(row=0, column=4, sticky="e", padx=(10, 0))

        # How to watch this run while doing something else. On the Activity
        # card because that is what they are about, and ALWAYS - it used to be
        # gridded only while a run was going, on the argument that a strip
        # answers "where did the progress go" and nothing goes anywhere when
        # idle.
        #
        # That argument is wrong about this switch. Turning the strip on is
        # something somebody does BEFORE starting a run, so they can put the
        # window away once it is going; offering it only after the run starts
        # means the one moment it is on screen is the moment it is least
        # useful. Asked for by name.
        self.watch_row = ttk.Frame(b, style="Card.TFrame")
        self.watch_row.grid(row=2, column=0, sticky="we", pady=(10, 0))
        ttk.Label(self.watch_row, text="Watch it from:", style="Muted.TLabel",
                  font=self.f_small).pack(side="left", padx=(0, 10))
        cb = ttk.Checkbutton(self.watch_row, text="Floating strip",
                             variable=self.mini_var, style=self.cb_style,
                             command=self.toggle_mini)
        cb.pack(side="left")
        Tip(cb, "A small always-on-top strip with the progress on it, so it "
                "stays readable with this window behind something else.")
        cb2 = ttk.Checkbutton(
            self.watch_row, text="...sitting in the taskbar",
            variable=self.dock_var, style=self.cb_style,
            command=lambda: self.set_mini_dock(self.dock_var.get()))
        cb2.pack(side="left", padx=(14, 0))
        Tip(cb2, "Put the strip inside the taskbar, exactly as tall as it is, "
                 "before the clock - so it takes no screen space of its own.")
        btn = ttk.Button(self.watch_row, text="Collapse to the tray",
                         command=self.collapse)
        btn.pack(side="left", padx=(18, 0))
        Tip(btn, "Put this window away into the notification area and leave the "
                 "run reporting to the strip. Ctrl+M does the same.")

        # TWO STOPS, because there are two pieces of work. Asked for 1 Sep:
        # "make it so the stop auto-rip button only cancels the foreground
        # task, give the background task its own cancel button". Until that,
        # Stop terminated every engine process going - including the encode of
        # the PREVIOUS film, which is holding the only copy of it while the
        # lossless it replaces is deleted.
        #
        # AT THE RIGHT-HAND END OF THIS ROW, which is directly above the bar it
        # belongs to - asked for 9 Sep in those words. It used to sit in the
        # top row beside the rip's own Stop, two rows and a whole progress
        # block away from the only thing on the card that says which film it
        # would throw away. Two buttons called Stop side by side, one of them
        # about something else on screen further down, is the arrangement that
        # made naming the film in the button necessary in the first place.
        #
        # Packed rather than gridded, and on screen only while there is
        # background work: a button that does nothing most of the time is a
        # button somebody has to think about every time they look at it.
        self.btn_bg_stop = ttk.Button(self.watch_row, text="Stop background",
                                      command=self.action_bg_stop)

        # the whole progress block is hidden while idle rather than sitting there
        # as an empty bar in a blank gap
        self.prog_area = ttk.Frame(b, style="Card.TFrame")
        self.prog_area.grid(row=1, column=0, sticky="we")
        self.prog_area.columnconfigure(0, weight=1)
        b2 = self.prog_area
        self.prog_header = ttk.Label(b2, text="", style="Muted.TLabel",
                                     justify="left")
        self.prog_header.grid(row=0, column=0, sticky="we", pady=(12, 4))
        # A DRAWN BAR, NOT A THEMED ONE. ttk.Progressbar is one colour at a
        # time by construction - the style decides it - so the window's bar
        # could not say which parts of the disc are read, which are dead and
        # which took the drive off the bus. SlimBar draws, so it can.
        #
        # Taller than the strip's, because there is room here and the whole
        # point of a coloured bar is that the sections are distinguishable.
        # THE BAR AND THE TWO POSITIONS IT RUNS BETWEEN, on one row.
        #
        # A frame of its own because the readouts have to be either side of the
        # bar and the progress area is a single stretchy column - everything
        # else in it spans that column. Gridding them into row 2 was the first
        # attempt and it put them in the cell the stats row already occupies,
        # so they were stacked underneath and never appeared.
        self.zoom_row = ttk.Frame(b2, style="Card.TFrame")
        self.zoom_row.grid(row=1, column=0, sticky="we")
        self.zoom_row.columnconfigure(1, weight=1)
        # THE WHOLE FILM, shown only while the main bar is zoomed in - see
        # set_zoom_overview. Half the height of the main bar: it is context,
        # not the thing being read.
        self.whole_bar = SlimBar(self.zoom_row, height=int(7 * self.sc))
        self.zoom_lo = ttk.Label(self.zoom_row, text="", style="Muted.TLabel",
                                 font=self.f_small)
        self.prog_bar = SlimBar(self.zoom_row, height=int(14 * self.sc))
        self.prog_bar.grid(row=0, column=1, sticky="we")
        self.zoom_hi = ttk.Label(self.zoom_row, text="", style="Muted.TLabel",
                                 font=self.f_small)
        # WHAT IT IS LOOKING AT, under the bar and across the whole width -
        # the thing the docked strip has no room for at all.
        self.zoom_what = ttk.Label(self.zoom_row, text="",
                                   style="Muted.TLabel", font=self.f_small,
                                   anchor="center")
        stats = ttk.Frame(b2, style="Card.TFrame")
        stats.grid(row=2, column=0, sticky="we", pady=(7, 0))
        self.lbl_pct = ttk.Label(stats, text="", style="Stat.TLabel",
                                 foreground=CLR["accent"], font=self.f_bold)
        self.lbl_pct.pack(side="left")
        self.lbl_bytes = ttk.Label(stats, text="", style="Stat.TLabel")
        self.lbl_bytes.pack(side="left", padx=(14, 0))
        self.lbl_left = ttk.Label(stats, text="", style="Muted.TLabel")
        self.lbl_left.pack(side="left", padx=(14, 0))
        self.lbl_eta = ttk.Label(stats, text="", style="Muted.TLabel")
        self.lbl_eta.pack(side="right")

        # Only ever on screen for a run that knows its own size, which is why
        # it is gridded in and out rather than sitting there empty: an auto-rip
        # cannot know how many discs are coming, and an empty second bar would
        # be a promise of a number that is never arriving.
        self.total_bar = ttk.Progressbar(b2, maximum=1000,
                                         style="Total.Horizontal.TProgressbar")
        self.total_row = ttk.Frame(b2, style="Card.TFrame")
        self.lbl_total = ttk.Label(self.total_row, text="",
                                   style="Muted.TLabel")
        self.lbl_total.pack(side="left")
        self.lbl_total_eta = ttk.Label(self.total_row, text="",
                                       style="Muted.TLabel")
        self.lbl_total_eta.pack(side="right")
        self._total_shown = False

        # A BAR FOR THE WORK THAT NO LONGER NEEDS THE DRIVE.
        #
        # OBSERVED 31 Aug, in a screenshot of this window: an auto-rip waiting
        # for the next disc with a background encode running showed one EMPTY
        # bar and nothing else - no fill, no percentage, no stage, no time
        # remaining. The engine was reporting all of it into BG_STATE and this
        # window drew one line of text from it.
        #
        # ITS OWN BAR, not a second series on the rip's. BG_STATE exists
        # precisely because the two are different films and neither may move
        # the other's readout - see BackgroundJobs. Gridded in and out with the
        # job, because an empty bar for work that is not happening is what this
        # is fixing.
        self.bg_area = ttk.Frame(self.card_act.body, style="Card.TFrame")
        self.bg_area.columnconfigure(0, weight=1)
        self.bg_head = ttk.Label(self.bg_area, text="", style="Muted.TLabel",
                                 foreground=CLR["bg_job"], justify="left")
        self.bg_head.grid(row=0, column=0, sticky="we", pady=(12, 4))
        self.bg_bar = ttk.Progressbar(self.bg_area, maximum=1000,
                                      style="Bg.Horizontal.TProgressbar")
        self.bg_bar.grid(row=1, column=0, sticky="we")
        bgstats = ttk.Frame(self.bg_area, style="Card.TFrame")
        bgstats.grid(row=2, column=0, sticky="we", pady=(6, 0))
        self.bg_pct = ttk.Label(bgstats, text="", style="Stat.TLabel",
                                foreground=CLR["bg_job"], font=self.f_bold)
        self.bg_pct.pack(side="left")
        self.bg_bytes = ttk.Label(bgstats, text="", style="Stat.TLabel")
        self.bg_bytes.pack(side="left", padx=(14, 0))
        self.bg_eta = ttk.Label(bgstats, text="", style="Muted.TLabel")
        self.bg_eta.pack(side="right")
        self._bg_shown = False

        # ---- log
        card = Card(p, "Activity log")
        card.grid(row=r, column=0, sticky="nsew", padx=2, pady=(0, 8))
        p.rowconfigure(r, weight=1)
        b = card.body
        b.rowconfigure(0, weight=1)
        b.columnconfigure(0, weight=1)
        # On the log, because that is what it clears. One line of buttons under
        # it rather than a row above, so it costs the log no height it was using.
        self.logbox = tk.Text(
            b, wrap="word", height=18, relief="sunken", borderwidth=2,
            background=CLR["field"], foreground=CLR["text"],
            font=self.f_base, padx=4, pady=3, spacing1=1, spacing3=2,
            insertbackground=CLR["text"], selectbackground=CLR["accent"],
            selectforeground=CLR["bg"], highlightthickness=0, cursor="arrow")
        ys = ttk.Scrollbar(b, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=ys.set, state="disabled")
        self.logbox.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self._log_tags()
        foot = ttk.Frame(b, style="Card.TFrame")
        foot.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(foot, text="Clear", command=self._clear_log).pack(side="left")
        ttk.Button(foot, text="Open log folder", command=self._open_logs
                   ).pack(side="left", padx=6)
        ttk.Button(foot, text="Open output folder", command=self._open_out
                   ).pack(side="left")
        # ONLY A CONTROL WHILE TWO FILMS ARE TALKING. See _refresh_log_filter:
        # it disables itself, and resets to "Both", the moment the log holds
        # only one kind of line.
        self.btn_log_filter = ttk.Button(
            foot, text="Both", width=16, state="disabled",
            command=self._cycle_log_filter)
        self.btn_log_filter.pack(side="left", padx=(12, 0))
        Tip(self.btn_log_filter,
            "While a rip and a background encode are both running their lines "
            "share this log - the background one's are violet. This shows one "
            "or the other. Nothing is thrown away; it redraws from the "
            "history.")
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(foot, text="Follow", variable=self.autoscroll,
                        style=self.cb_style).pack(side="right")

    def _log_tags(self):
        t = self.logbox
        ind = int(74 * self.sc)     # hang wrapped lines under the message text
        t.tag_configure("ts", foreground=CLR["faint"], font=self.f_small,
                        lmargin1=0, lmargin2=ind)
        # Only the glyph is coloured. Colouring whole lines is what made this
        # read as terminal output; a real log keeps its body text legible and
        # uses colour as a marker.
        for name, colour in (("g_ok", CLR["ok"]), ("g_warn", CLR["warn"]),
                             ("g_err", CLR["err"]), ("g_info", CLR["accent"])):
            t.tag_configure(name, foreground=colour, font=self.f_bold)
        t.tag_configure("body", foreground=CLR["text"], lmargin1=0, lmargin2=ind)
        t.tag_configure("body_err", foreground=CLR["err"], lmargin1=0,
                        lmargin2=ind)
        t.tag_configure("body_warn", foreground=CLR["warn"], lmargin1=0,
                        lmargin2=ind)
        t.tag_configure("body_dim", foreground=CLR["muted"], lmargin1=0,
                        lmargin2=ind)
        # THE BACKGROUND WORKER'S LINES. Whole-body colour here, against the
        # rule two comments up, and for the reason the rule was written: colour
        # marks what a line IS rather than decorating it. These lines are about
        # a different film from the one in the drive, and the whole line is what
        # belongs to that film - the glyph alone would not carry it.
        t.tag_configure("body_bg", foreground=CLR["bg_job"], lmargin1=0,
                        lmargin2=ind)
        t.tag_configure("ts_bg", foreground=CLR["bg_job"], font=self.f_small,
                        lmargin1=0, lmargin2=ind)
        t.tag_configure("head", foreground=CLR["text"], font=self.f_h2,
                        spacing1=int(12 * self.sc), spacing3=5)

    # -- generated settings tabs ------------------------------------------
    def _prewarm(self):
        """Build one settings tab per tick, now that the window is up and the
        user can see it.

        Deferring them is what makes the window open quickly, but on its own it
        only moves the cost to the first click on each tab, where it is a visible
        sixth of a second. Filling them in while nobody is asking gets both: the
        window appears in a fifth of the time it used to, and a tab is already
        built by the time it is opened. One per tick rather than all six at once,
        so the window stays answerable throughout."""
        if self._closing:
            return
        for section, _label, _blurb in SECTION_TABS:
            if section not in self._filled:
                self._fill_section_tab(section)
                self.after(50, self._prewarm)
                return

    def _prewarm_hw(self):
        """Warm the hardware-encoder probe off the main thread, once the
        window has settled.

        speed_ok() runs on the Start click, and behind it detect_hw runs up to
        three throwaway test encodes - a few seconds with the window frozen,
        on the click that begins an unattended run. The probe's answer is
        cached for the life of the process, so paying for it here, in the
        background, makes the click instant. Skipped when nothing would be
        encoded, since the question would never be asked."""
        if self._closing:
            return

        def work():
            try:
                dr = self.dr
                if not dr.sget(self.settings, "video.compress", False):
                    return
                ff = dr.find_tool("ffmpeg", self.settings)
                if ff:
                    dr.detect_hw(ff, str(dr.sget(self.settings,
                                                 "video.vcodec", "x265")))
            except Exception:
                pass                    # a warm-up must never be a problem
        threading.Thread(target=work, daemon=True).start()

    def _on_tab_changed(self, _e=None):
        try:
            cur = str(self.nb.select())
        except tk.TclError:
            return
        for section, (frame, _label, _blurb) in self._tabframes.items():
            if str(frame) == cur and section not in self._filled:
                self._fill_section_tab(section)
                return

    def _fill_section_tab(self, section):
        frame, label, blurb = self._tabframes[section]
        self._filled.add(section)
        for child in frame.winfo_children():
            child.destroy()
        sc = Scrollable(frame)
        sc.pack(fill="both", expand=True)
        p = sc.inner
        p.columnconfigure(0, weight=1)

        card = Card(p, label, blurb)
        card.grid(row=0, column=0, sticky="we", padx=2, pady=(4, 10))
        b = card.body
        b.columnconfigure(0, weight=1)

        bar = ttk.Frame(b, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="we", pady=(0, 8))
        ttk.Label(bar, text="Filter", style="Muted.TLabel").pack(side="left",
                                                                 padx=(0, 6))
        fvar = self._filters.get(section) or tk.StringVar()
        self._filters[section] = fvar
        ent = ttk.Entry(bar, textvariable=fvar, width=26)
        ent.pack(side="left")
        ent.bind("<KeyRelease>", lambda e, s=section: self._apply_filter(s))
        ttk.Button(bar, text="Clear",
                   command=lambda s=section: (self._filters[s].set(""),
                                              self._apply_filter(s))
                   ).pack(side="left", padx=4)
        self._rowholder = ttk.Frame(b, style="Card.TFrame")
        self._rowholder.grid(row=1, column=0, sticky="we")
        self._rowholder.columnconfigure(0, weight=1)
        setattr(self, f"_rows_{section}", self._rowholder)
        self._build_rows(section)

        row = 1
        if section == "alerts":
            self._alerts_extras(p, row=row)
        elif section == "video":
            self._video_extras(p, row=row)
        elif section == "tools":
            self._tools_extras(p, row=row)
        # after whatever that section already had, so the one-off jobs read as
        # an appendix rather than as the point of the tab
        self._tab_actions(p, row + 8, section)

    def _build_rows(self, section, filt=""):
        """One tab's settings, under headings, in reading order.

        Grouped rather than alphabetical: alphabetical put auto_wait_minutes
        between auto_video and checksum, and scattered the eight strip settings
        through the middle of forty. The order comes from the engine - see
        grouped_settings - so the console editor and this agree about what
        belongs with what.

        A heading is only drawn if something under it survived the filter, and
        the stripe keeps running across the whole tab rather than restarting per
        group, so a group boundary is the heading and not a break in the
        pattern."""
        holder = getattr(self, f"_rows_{section}")
        for child in holder.winfo_children():
            child.destroy()
        dr = self.dr
        filt = (filt or "").strip().lower()
        shown = 0
        placed = 0
        for title, names in dr.grouped_settings(section):
            hits = []
            for name in names:
                helptext = dr.SETTING_HELP.get(f"{section}.{name}", "")
                if (not filt or filt in name.lower()
                        or filt in helptext.lower()):
                    hits.append((name, helptext))
            if not hits:
                continue
            if title:
                head = ttk.Frame(holder, style="Card.TFrame",
                                 padding=(2, int(14 * self.sc) if placed else 2,
                                          2, 4))
                head.grid(row=placed, column=0, sticky="we")
                placed += 1
                ttk.Label(head, text=title.upper(), style="GroupHead.TLabel",
                          ).pack(side="left")
                ttk.Frame(head, style="Rule.TFrame", height=1).pack(
                    side="left", fill="x", expand=True, padx=(10, 2), pady=6)
            for name, helptext in hits:
                dotted = f"{section}.{name}"
                tag = "Row" if shown % 2 == 0 else "RowAlt"
                row = ttk.Frame(holder, style=f"{tag}.TFrame", padding=(10, 8))
                row.grid(row=placed, column=0, sticky="we")
                placed += 1
                row.columnconfigure(1, minsize=int(250 * self.sc))
                row.columnconfigure(2, weight=1)
                cur = dr.sget(self.settings, dotted,
                              dr.sget(dr.DEFAULTS, dotted))

                lbl = ttk.Label(row, text=name.replace("_", " "),
                                style=f"{tag}.TLabel", width=24, anchor="w")
                lbl.grid(row=0, column=0, sticky="nw", padx=(0, 10))
                Tip(lbl, f"{dotted}\n\n{helptext}")
                ctrl = ttk.Frame(row, style=f"{tag}.TFrame")
                ctrl.grid(row=0, column=1, sticky="w")
                self._widget_for(ctrl, dotted, cur, tag)
                if helptext:
                    d = ttk.Label(row, text=helptext,
                                  style=f"{tag}Desc.TLabel", justify="left",
                                  wraplength=int(420 * self.sc))
                    d.grid(row=0, column=2, sticky="w", padx=(14, 0))
                shown += 1
        if not shown:
            ttk.Label(holder, text="Nothing matches that filter.",
                      style="Muted.TLabel").grid(row=0, column=0, sticky="w",
                                                 pady=8)

    def _apply_filter(self, section):
        self._build_rows(section, self._filters[section].get())

    def _widget_for(self, parent, dotted, cur, tag="Row"):
        if isinstance(cur, bool):
            var = tk.BooleanVar(value=cur)
            # cb_style carries the per-theme sequence number ("DR1", "DR2"
            # after a theme switch). The literal "DR.TCheckbutton" named a
            # style nothing ever configured, so ttk fell back to clam's
            # diagonal cross - the exact defect _install_check_indicator
            # documents itself as fixing.
            ttk.Checkbutton(parent, variable=var,
                            style=f"{tag}.{self.cb_style}",
                            text="", command=lambda: self._set_and_save(
                                dotted, var.get())).pack(anchor="w")
            self.vars[dotted] = var
            return

        if dotted == "audio.formats":
            holder = ttk.Frame(parent, style=f"{tag}.TFrame")
            holder.pack(anchor="w")
            chosen = set(cur or [])
            fvars = {}
            for i, fmt in enumerate(AUDIO_FORMATS):
                v = tk.BooleanVar(value=fmt in chosen)
                fvars[fmt] = v
                ttk.Checkbutton(
                    holder, text=fmt, variable=v,
                    style=f"{tag}.{self.cb_style}",
                    command=lambda: self._set_and_save(
                        dotted, [f for f, vv in fvars.items() if vv.get()])
                ).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 10))
            self.vars[dotted] = fvars
            return

        if dotted in PERCENT_KEYS:
            self._percent_widget(parent, dotted, cur, tag)
            return

        # One variable per setting, shared by every widget bound to it. The
        # general output folder is on the Rip tab as well as in its own settings
        # row, and two variables would let the two drift apart - type a path on
        # one and the other would go on showing the old one until a restart.
        # str() on each item, not just on the list. A setting whose value is a
        # list of NUMBERS - the speeds to fall back through while a drive
        # struggles - threw TypeError here and took the whole settings tab
        # with it, because ",".join refuses anything that is not a string.
        val = (",".join(str(x) for x in cur) if isinstance(cur, (list, tuple))
               else str(cur))
        var = self.vars.get(dotted)
        if isinstance(var, tk.StringVar):
            var.set(val)
        else:
            var = tk.StringVar(value=val)
            self.vars[dotted] = var
        if dotted == "video.screen":
            # Editable: the presets cover what people ask for, and the setting
            # also takes any ratio or exact size, which a readonly list would
            # make unreachable.
            w = ttk.Combobox(parent, textvariable=var, width=16,
                             values=[str(c) for c in self.dr.SCREEN_CHOICES])
            w.pack(anchor="w")
            for seq in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
                w.bind(seq, lambda e: self._set_and_save(dotted, var.get()))
            return
        if dotted == "alerts.voice":
            # Editable, not readonly: the value is matched as a substring, and
            # if enumerating the installed voices fails there has to be
            # something left to type into.
            w = ttk.Combobox(parent, textvariable=var, width=32, values=[""])
            w.pack(anchor="w")
            for seq in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
                w.bind(seq, lambda e: self._set_and_save(dotted, var.get()))
            # Filled when somebody reaches for it, not when the tab is
            # drawn: the prewarm builds every tab at startup, and enumerating
            # voices starts a PowerShell and loads the speech engine. Doing
            # that on every launch to populate a dropdown most people will
            # never open is a second of somebody else's morning.
            for seq in ("<Button-1>", "<FocusIn>"):
                w.bind(seq, lambda e, b=w: self._fill_voices(b), add="+")
            return
        if dotted in ("video.audio_langs", "video.sub_langs"):
            kind = "audio" if dotted.endswith("audio_langs") else "subtitle"
            # Editable, not readonly: the setting is a list, and a
            # readonly dropdown of single languages cannot say "eng,jpn".
            w = ttk.Combobox(parent, textvariable=var, width=34,
                             values=language_values(self.dr, kind, var.get()))
            w.pack(anchor="w")
            # THE LIST AS IT STANDS, remembered across the pick. Selecting
            # overwrites the box with the entry that was clicked, so by the
            # time the handler runs the old value is gone unless it was kept.
            held = [var.get()]

            def _open(_e=None, b=w, k=kind):
                held[0] = var.get()
                # Rebuilt on the way open: the disc in the drive changes and
                # the list is half about the disc. Cheap - it reads one dict.
                b.configure(values=language_values(self.dr, k, held[0]))

            def _pick(_e=None):
                code = lang_toggle(held[0], var.get())
                held[0] = code
                var.set(code)
                self._set_and_save(dotted, code)

            def _typed(_e=None):
                # Taken literally, not toggled: somebody who typed "eng,jpn"
                # means those two and not a change to what was there.
                code = ",".join(lang_codes(var.get())) or var.get().strip()
                held[0] = code
                var.set(code)
                self._set_and_save(dotted, code)

            w.bind("<<ComboboxSelected>>", _pick)
            for seq in ("<FocusOut>", "<Return>"):
                w.bind(seq, _typed)
            for seq in ("<Button-1>", "<FocusIn>"):
                w.bind(seq, _open, add="+")
            return
        # The alerts.on_* fallback moved into setting_choices, so every
        # caller gets it rather than only this one.
        choices = choices_for(self.dr, dotted)
        if choices is not None:
            w = ttk.Combobox(parent, textvariable=var, state="readonly",
                             width=18, values=[str(c) for c in choices])
            w.pack(anchor="w")
            w.bind("<<ComboboxSelected>>",
                   lambda e: self._set_and_save(dotted, var.get()))
            return

        wide = _dir_key(dotted) or _file_key(dotted) or "template" in dotted \
            or dotted.endswith(("_args", "custom_command", "hook"))
        w = ttk.Entry(parent, textvariable=var, width=34 if wide else 18)
        w.pack(side="left")
        w.bind("<FocusOut>", lambda e: self._set_and_save(dotted, var.get()))
        w.bind("<Return>", lambda e: self._set_and_save(dotted, var.get()))
        if _dir_key(dotted) or _file_key(dotted):
            ttk.Button(parent, text="Browse",
                       command=lambda: self._browse(dotted, var)
                       ).pack(side="left")

    def _percent_widget(self, parent, dotted, cur, tag):
        """0-100 as something to drag, with the number beside it.

        Committed on release rather than on every pixel of the drag: each save
        writes the config file, and each volume change renders a quieter copy of
        whatever plays next - doing either eighty times on the way across the
        slider is eighty files and eighty writes."""
        try:
            start = max(0, min(100, int(cur)))
        except (TypeError, ValueError):
            start = 100
        var = tk.IntVar(value=start)
        self.vars[dotted] = var
        holder = ttk.Frame(parent, style=f"{tag}.TFrame")
        holder.pack(anchor="w")
        shown = ttk.Label(holder, text=f"{start:>3}", style=f"{tag}.TLabel",
                          width=4, anchor="e", font=self.f_num)
        sc = ttk.Scale(holder, from_=0, to=100, orient="horizontal",
                       length=int(150 * self.sc), style="Horizontal.TScale",
                       command=lambda v: (var.set(int(round(float(v)))),
                                          shown.configure(
                                              text=f"{int(round(float(v))):>3}")))
        sc.set(start)
        sc.pack(side="left")
        shown.pack(side="left", padx=(8, 0))
        sc.bind("<ButtonRelease-1>",
                lambda e: self._set_and_save(dotted, var.get()))
        sc.bind("<KeyRelease>", lambda e: self._set_and_save(dotted, var.get()))

    def _fill_voices(self, box):
        """Ask Windows what voices it has, off the main thread, once.

        Enumerating them starts a PowerShell and loads the speech engine, which
        is most of a second - long enough that the dropdown has to open on the
        names it already has and gain the rest a moment later, rather than the
        window stopping while it asks."""
        if self._voices is not None:
            self._apply_voices(box, self._voices)
            return
        if self._voices_asked:
            return                      # already on its way
        self._voices_asked = True

        def work():
            try:
                names = self.dr.list_voices()
            except Exception:
                names = []
            self.ui_q.put(("voices", (box, names)))
        threading.Thread(target=work, daemon=True).start()

    def _apply_voices(self, box, names):
        self._voices = list(names)
        try:
            if box.winfo_exists():
                box.configure(values=[""] + self._voices)
        except tk.TclError:
            pass

    def shared_var(self, dotted, kind=None):
        """The one variable for a setting, whoever is showing it.

        A setting can be on screen in more than one place - the output folder is
        on the Rip tab and in its settings row, the auto-rip options are in a
        window of their own and on the General tab - and two variables for one
        setting means typing in one place and watching the other go on showing
        the old value until a restart."""
        cur = self.dr.sget(self.settings, dotted)
        if kind is None:
            kind = tk.BooleanVar if isinstance(
                self.dr.sget(self.dr.DEFAULTS, dotted), bool) else tk.StringVar
        var = self.vars.get(dotted)
        if isinstance(var, kind):
            return var
        if kind is tk.BooleanVar:
            var = tk.BooleanVar(value=bool(cur))
        else:
            var = tk.StringVar(
                # str() on each item, not just on the list - a setting whose
            # value is a list of NUMBERS threw TypeError here. The same fault
            # was fixed once in the settings-tab row builder and left standing
            # in the other place that renders a list.
            value=(",".join(str(x) for x in cur)
                   if isinstance(cur, (list, tuple)) else str(cur)))
        self.vars[dotted] = var
        return var

    def _browse(self, dotted, var):
        if _dir_key(dotted):
            got = filedialog.askdirectory(
                title=dotted, initialdir=os.path.expanduser(var.get() or "~"))
        else:
            got = filedialog.askopenfilename(
                title=dotted, filetypes=[("Programs", "*.exe"),
                                         ("All files", "*.*")])
        if got:
            var.set(got)
            self._set_and_save(dotted, got)

    def _set_and_save(self, dotted, value):
        dr = self.dr
        default = dr.sget(dr.DEFAULTS, dotted)
        try:
            if isinstance(default, bool):
                value = bool(value) if isinstance(value, bool) else \
                    str(value).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int) and not isinstance(default, bool):
                value = int(str(value).strip())
            elif isinstance(default, float):
                # backtrack_min_seconds and friends: uncoerced they were
                # written
                # back to the config as strings
                value = float(str(value).strip())
            elif isinstance(default, list) and not isinstance(value, list):
                value = [x.strip() for x in str(value).split(",") if x.strip()]
        except (TypeError, ValueError):
            self._log_line(f"  [!] {dotted}: '{value}' is not a number - ignored")
            return
        if dr.sget(self.settings, dotted) == value:
            return
        dr.sset(self.settings, dotted, value)
        try:
            dr.save_config(self.settings, dr.CONFIG_PATH)
            self._status(f"Saved  {dotted} = {value}")
        except OSError as e:
            self._log_line(f"  [X] could not save config: {e}")
        self._setting_took_effect(dotted, value)
        # A mode is a claim about every setting in it. Changing one by hand may
        # have made that claim false, and the radio buttons should say so
        # rather than keep pointing at a mode this no longer is.
        if getattr(self, "mode_var", None) is not None:
            self._paint_mode()

    def _show_disc_sub(self):
        """The disc's description takes a line only when it has one to give."""
        try:
            if str(self.disc_sub.cget("text")).strip():
                self.disc_sub.grid()
            else:
                self.disc_sub.grid_remove()
        except tk.TclError:
            pass

    def out_dir_set(self):
        return bool(str(self.dr.sget(self.settings, "general.out_dir", "")
                        or "").strip())

    def need_out_dir(self, what="This"):
        """True to carry on. Asks for a folder rather than guessing one.

        There used to be a line under the box explaining that a blank one meant
        each kind of disc went somewhere built-in. Nobody should have to work
        out where their films went from a sentence about precedence, so the box
        is simply required for anything that writes one."""
        if self.out_dir_set():
            return True
        messagebox.showinfo(
            self.dr.APP,
            f"{what} needs somewhere to write to.\n\nPick an output folder at "
            "the top of the Rip tab first.")
        try:
            self.nb.select(self._tabframes["rip"][0] if "rip" in
                           getattr(self, "_tabframes", {}) else self.nb.tabs()[0])
            self.out_entry.focus_set()
        except (tk.TclError, KeyError, IndexError):
            pass
        return False

    def _paint_out_hint(self):
        """Where a film would actually land, resolved rather than described.

        The general folder, the per-kind folders and the built-in defaults
        interact in a way nobody should have to hold in their head: a per-kind
        folder still set to its built-in value gives way to this one, a per-kind
        folder somebody has chosen does not, and a relative one lands inside
        this one. So rather than explain that here, it says where a DVD would go
        if one were ripped now."""
        # Nothing on screen any more - the folder is required instead of
        # explained - but the setting still lands here from the tabs, and the
        # entry on this page has to follow it.
        try:
            self.out_var.set(str(self.dr.sget(self.settings, "general.out_dir",
                                              "") or ""))
        except (AttributeError, tk.TclError):
            pass

    def _setting_took_effect(self, dotted, value):
        """A handful of settings describe something already on screen. Those act
        when they are changed, rather than at the next launch - a checkbox for a
        window that is right there and does not move reads as broken."""
        if dotted.endswith("out_dir"):
            # any of them: the general one, or a per-kind one that overrides it
            self._paint_out_hint()
        if dotted == "general.mini_monitor":
            self.show_mini(remember=False) if value else \
                self.hide_mini(remember=False)
        elif dotted == "general.mini_monitor_dock":
            docked = str(value).lower() == "taskbar"
            self.dock_var.set(docked)
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.set_docked(docked)
        elif dotted in ("general.mini_monitor_pos",
                        "general.mini_monitor_width",
                        "general.mini_monitor_height"):
            if self.mini is not None and self.mini.winfo_exists():
                self.mini._place()
                self.mini.reshape(force=True)
        elif dotted == "general.mini_monitor_detail":
            self.detail_var.set(str(value).lower())
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.reshape(force=True)
        elif dotted == "general.mini_monitor_opacity":
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.set_opacity()
        elif dotted == "general.taskbar_progress":
            if value and self.taskbar is None:
                self.taskbar = TaskbarProgress(self)
                self.repaint_mini()
            elif not value and self.taskbar is not None:
                self.taskbar.close()
                self.taskbar = None

    # -- alerts / tools extras --------------------------------------------
    def _video_extras(self, p, row):
        """The three settings above are defaults; this is the thing itself.

        They are the wrong shape for what people actually want to do, which is
        "make this one film fill my television" - one file, one look at the
        result, one button. So the tab says what they are for and hands over to
        the window that does it."""
        card = Card(p, "Fit a film to a screen",
                    "The three settings above are the defaults every "
                    "compressed rip uses. To do one film, and see it first:")
        card.grid(row=row, column=0, sticky="we", padx=2, pady=(0, 10))
        b = card.body
        ttk.Label(b, text="Measures the black borders baked into the picture, "
                          "shows what each way of fitting it would produce, "
                          "writes real stills so you can judge a crop by "
                          "looking at one, and then encodes it.",
                  style="Muted.TLabel", justify="left",
                  wraplength=int(700 * self.sc)).grid(row=0, column=0,
                                                      sticky="w")
        ttk.Button(b, text="Fit a film to a screen...",
                   command=self.action_fit).grid(row=1, column=0, sticky="w",
                                                 pady=(10, 0))

    def _alerts_extras(self, p, row):
        dr = self.dr
        card = Card(p, "Popups", "Windows silently discards a notification from "
                                 "an app it does not recognise, so test yours.")
        card.grid(row=row, column=0, sticky="we", padx=2, pady=(0, 10))
        b = card.body
        line = ttk.Frame(b, style="Card.TFrame")
        line.grid(row=0, column=0, sticky="we")
        ttk.Label(line, text="Popup style", style="Card.TLabel").pack(side="left",
                                                                     padx=(0, 8))
        self._widget_for(line, "general.notify_style",
                         dr.sget(self.settings, "general.notify_style", "auto"),
                         "Row")
        ttk.Label(b, text=dr.SETTING_HELP.get("general.notify_style", ""),
                  style="Muted.TLabel", wraplength=int(700 * self.sc),
                  justify="left").grid(row=1, column=0, sticky="w", pady=(6, 0))
        btns = ttk.Frame(b, style="Card.TFrame")
        btns.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(btns, text="Test popup",
                   command=lambda: self._run_bg("popup test",
                                                dr.cmd_test_notify, self.settings)
                   ).pack(side="left")
        ttk.Button(btns, text="Play every alert", command=self._test_sounds
                   ).pack(side="left", padx=6)
        ttk.Button(btns, text="Test progress cue",
                   command=lambda: self._run_bg(
                       "progress cue", dr.preview_alert, self.settings,
                       "progress")).pack(side="left")
        ttk.Button(btns, text="Hear the voices", command=self._test_voices
                   ).pack(side="left", padx=6)

    def _test_sounds(self):
        def work():
            dr = self.dr
            for ev, label in dr.ALERT_EVENTS:
                snd = dr.sget(self.settings, f"alerts.sound_{ev}", "")
                spoken = dr.is_voice(snd)
                dr.info(f"{label}: {snd}"
                        + (f'   "{dr.sample_line(self.settings, ev)}"'
                           if spoken else ""))
                if not dr.preview_alert(self.settings, ev):
                    dr.warn("  (could not play)")
                # A spoken line runs three seconds and a chime runs one; at 1.4
                # apiece the six of them talked over each other.
                threading.Event().wait(3.0 if spoken else 1.4)
        self._run_bg("alert test", work)

    def _test_voices(self):
        self._run_bg("voice test", self.dr.cmd_voices, self.settings)

    def _tools_extras(self, p, row):
        dr = self.dr
        card = Card(p, "Detected engines",
                    "A copy pinned in .\\tools always wins, so the folder keeps "
                    "working even if the system install changes.")
        card.grid(row=row, column=0, sticky="we", padx=2, pady=(0, 10))
        b = card.body
        self.tool_status = ttk.Label(b, justify="left", style="Card.TLabel",
                                     font=self.f_small)
        self.tool_status.grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(b, style="Card.TFrame")
        btns.grid(row=1, column=0, sticky="w", pady=(10, 0))
        for text, fn in (("Environment check", dr.cmd_doctor),
                         ("Install engines", dr.cmd_setup),
                         ("Bundle into this folder", dr.cmd_bundle),
                         ("MakeMKV key", dr.cmd_makemkv_key)):
            ttk.Button(btns, text=text,
                       command=lambda f=fn, t=text: self._run_bg(
                           t.lower(), f, self.settings)).pack(side="left",
                                                              padx=(0, 6))
        ttk.Button(btns, text="Refresh",
                   command=self._refresh_tools).pack(side="left", padx=(8, 0))
        self._refresh_tools()

    def _refresh_tools(self):
        """Off the main thread: finding an engine walks .\\tools, scans PATH and
        reads each binary's PE header for its architecture - a few milliseconds
        once the files are in the cache, but seconds on a cold one with a virus
        scanner wanting first look at every .exe it touches."""
        try:
            self.tool_status.configure(text="Looking for engines...")
        except tk.TclError:
            return
        threading.Thread(target=self._probe_tools, daemon=True).start()

    def _probe_tools(self):
        dr = self.dr
        lines = []
        for name in ("makemkvcon", "cyanrip", "ffmpeg", "ffprobe", "handbrake"):
            label = dr.TOOL_EXE.get(name, name)
            try:
                path = dr.find_tool(name, self.settings)
                if path:
                    tag = "pinned" if dr.is_pinned(path) else "system"
                    lines.append(f"✓  {label:<13}  {path}   [{tag}]"
                                 f"{dr.arch_note(path)}")
                else:
                    lines.append(f"✗  {label:<13}  not installed")
            except OSError as e:
                lines.append(f"✗  {label:<13}  {e}")
        self.ui_q.put(("tools", "\n".join(lines)))

    def _apply_tools(self, text):
        try:
            self.tool_status.configure(text=text)
        except (tk.TclError, AttributeError):
            pass                # the Engines tab was never opened, or was rebuilt

    # -- profiles ---------------------------------------------------------
    def _build_profiles_tab(self, root):
        dr = self.dr
        sc = Scrollable(root)
        sc.pack(fill="both", expand=True)
        p = sc.inner
        p.columnconfigure(0, weight=1)

        card = Card(p, "Profiles", "A profile is a named bundle of setting "
                                   "overrides. Applying one updates the other "
                                   "tabs immediately.")
        card.grid(row=0, column=0, sticky="we", padx=2, pady=(4, 10))
        b = card.body
        names = list(dr.BUILTIN_PROFILES.keys())
        user = dr.sget(self.settings, "profiles", {}) or {}
        names += [n for n in user if n not in names]
        desc = {
            "archive": "everything lossless - the defaults",
            "plex": "remux plus media-server naming",
            "portable": "FLAC+Opus audio, x265 CRF22 beside the remux",
            "space-saver": "Opus 128k, AV1 CRF28 1080p, originals not kept",
        }
        self.profile_var = tk.StringVar(value="")
        for i, n in enumerate(names):
            rb = ttk.Radiobutton(b, text=f"{n}   -   "
                                 f"{desc.get(n, 'from your config')}",
                                 value=n, variable=self.profile_var,
                                 style="Card.TRadiobutton")
            rb.grid(row=i, column=0, sticky="w", pady=2)
        btns = ttk.Frame(b, style="Card.TFrame")
        btns.grid(row=len(names), column=0, sticky="w", pady=(12, 0))
        ttk.Button(btns, text="Apply",
                   command=self._apply_profile).pack(side="left")
        ttk.Button(btns, text="Apply and use at startup",
                   command=lambda: self._apply_profile(startup=True)
                   ).pack(side="left", padx=6)
        ttk.Button(btns, text="Save current settings as...",
                   command=self._save_profile).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear startup profile",
                   command=self._clear_profile).pack(side="left")
        cur = dr.sget(self.settings, "general.profile", "") or "(none)"
        self.profile_note = ttk.Label(b, text=f"Startup profile: {cur}",
                                      style="Muted.TLabel")
        self.profile_note.grid(row=len(names) + 1, column=0, sticky="w",
                               pady=(10, 0))

        card = Card(p, "Config file", str(dr.CONFIG_PATH))
        card.grid(row=1, column=0, sticky="we", padx=2, pady=(0, 10))
        b2 = ttk.Frame(card.body, style="Card.TFrame")
        b2.grid(row=0, column=0, sticky="w")
        ttk.Button(b2, text="Open in Notepad", command=self._edit_config
                   ).pack(side="left")
        ttk.Button(b2, text="Reload from disk", command=self._reload_config
                   ).pack(side="left", padx=6)
        ttk.Button(b2, text="Reset to defaults",
                   command=self._reset_config).pack(side="left")

    def _apply_profile(self, startup=False):
        name = self.profile_var.get()
        if not name:
            messagebox.showinfo(self.dr.APP, "Pick a profile first.")
            return
        self.dr.apply_profile(self.settings, name)
        if startup:
            self.dr.sset(self.settings, "general.profile", name)
        self.dr.save_config(self.settings, self.dr.CONFIG_PATH)
        self._rebuild_settings()
        self.profile_note.configure(
            text="Startup profile: "
                 f"{self.dr.sget(self.settings, 'general.profile', '') or '(none)'}")

    def _clear_profile(self):
        self.dr.sset(self.settings, "general.profile", "")
        self.dr.save_config(self.settings, self.dr.CONFIG_PATH)
        self.profile_note.configure(text="Startup profile: (none)")

    def _save_profile(self):
        dr = self.dr
        win = self._modal("Save profile")
        ttk.Label(win, text="Profile name", style="Card.TLabel").pack(
            anchor="w", padx=16, pady=(14, 4))
        var = tk.StringVar(value="my-profile")
        e = ttk.Entry(win, textvariable=var, width=34)
        e.pack(padx=16)
        e.focus_set()

        def go():
            name = dr.sanitize_component(var.get())
            flat = {}
            for section in ("general", "audio", "video", "data", "alerts"):
                for k, v in dr.sget(self.settings, section, {}).items():
                    if v != dr.sget(dr.DEFAULTS, f"{section}.{k}") \
                            and k != "makemkv_key":
                        flat[f"{section}.{k}"] = v
            if not flat:
                messagebox.showinfo(dr.APP, "Current settings match the "
                                            "defaults - nothing to save.")
                win.destroy()
                return
            dr.sset(self.settings, f"profiles.{name}", flat)
            dr.save_config(self.settings, dr.CONFIG_PATH)
            self._log_line(f"  [OK] saved profile '{name}' with {len(flat)} "
                           f"override(s)")
            win.destroy()
        row = ttk.Frame(win, style="Card.TFrame")
        row.pack(pady=14)
        ttk.Button(row, text="Save",
                   command=go).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left")

    def _edit_config(self):
        self.dr.run(["notepad", str(self.dr.CONFIG_PATH)], mode="capture",
                    ignore_rc=True)
        self._reload_config()

    def _reload_config(self):
        fresh = self.dr.load_config(self.dr.CONFIG_PATH)
        self.dr.deep_merge(self.settings, fresh)
        self._rebuild_settings()
        self._log_line("  [OK] config reloaded")

    def _reset_config(self):
        if not messagebox.askyesno(self.dr.APP,
                                   "Reset every setting to its default?"):
            return
        import copy
        self.dr.deep_merge(self.settings, copy.deepcopy(self.dr.DEFAULTS))
        self.dr.save_config(self.settings, self.dr.CONFIG_PATH)
        self._rebuild_settings()
        self._log_line("  [OK] settings reset to defaults")

    def _rebuild_settings(self):
        # only the tabs that were actually opened need redoing; the rest read the
        # new values off self.settings whenever they are first looked at
        for section in list(self._filled):
            self._fill_section_tab(section)
        self.mtype_var.set(str(self.dr.sget(self.settings,
                                            "general.media_type", "auto")))

    # -- drives / disc ----------------------------------------------------
    def refresh_drives(self, announce=True):
        """Ask a worker thread what drives exist, and fill the box in when it
        answers.

        Worth the extra machinery because list_drives starts a PowerShell and
        waits for CIM - a third of a second on a warm machine before it has
        looked at any hardware - and disc_present after it can block for seconds
        on a disc that is still spinning up. Done inline, that is all dead time
        in front of the first paint, with Windows free to grey the window out as
        'not responding' while it passes."""
        if self._scanning:
            return
        self._scanning = True
        # announce=False means this is a background refresh nobody asked for -
        # after a run finishes, say - so it must not take the status line over.
        # It was doing exactly that, and the summary of a finished auto-rip was
        # replaced by "2 optical drive(s) found" a moment after it appeared.
        if announce:
            self._status("Looking for optical drives...")
        threading.Thread(target=self._scan_drives, args=(announce,),
                         daemon=True).start()

    def _scan_drives(self, announce):
        dr = self.dr
        drives, vals, aborted = [], [], False
        try:
            drives = dr.list_drives()
            for d in drives:
                # BOUNDED. disc_present starts with os.path.exists on
                # the drive root, and on 2 Sep that call hung for
                # minutes on a BU40N still settling a freshly inserted
                # Blu-ray - long enough to take a whole test run with
                # it. Unbounded here it stalls the scan thread, so the
                # picker never updates and the drive reads as "not
                # detected" while the filesystem can see it perfectly
                # well. list_drives already reports `loaded` from the
                # volume label, so this is only the fallback for a drive
                # with no filesystem at all.
                state = "disc present" if (
                    d["loaded"]
                    or dr.bounded(
                        lambda x=d["letter"]: dr.disc_present(x),
                        3.0, False)) else "empty"
                vals.append(f"{d['letter']}:   {d['desc']}   [{state}]")
        except dr.Cancelled:
            # A stop aimed at a rip kills every engine process going, and a scan
            # running alongside it is one of them. Nothing has gone wrong here -
            # the answer is simply missing - so this is not the crash it used to
            # be logged as.
            aborted = True
        except Exception:
            self.ui_q.put(("line", "  [X] " + traceback.format_exc()))
        self.ui_q.put(("drives", (drives, vals, announce, aborted)))

    def _apply_drives(self, drives, vals, announce, aborted=False):
        dr = self.dr
        self._scanning = False
        if aborted:
            # Keep the list that is already on screen rather than blanking it
            # over a question that was never answered, and ask again once the
            # stop it was caught by has finished. Bounded, so a drive that
            # cannot be reached at all is not retried forever.
            if self._scan_retries < 3 and not self._closing:
                self._scan_retries += 1
                self._status("Drive scan interrupted - retrying")
                self.after(600, lambda: self.refresh_drives(announce=False))
            else:
                self._status("Drive scan interrupted")
            return
        self._scan_retries = 0
        self.drives = drives
        try:
            self.drive_box.configure(values=vals)
            if vals and not self.drive_var.get():
                self.drive_var.set(vals[0])
            elif not vals:
                self.drive_var.set("")
                # short enough to sit beside the buttons; the long version
                # is on the tooltip rather than on two lines of its own.
                # AND A TIMEOUT IS NOT A DIAGNOSIS: a killed drive query and
                # an empty machine arrive here as the same empty list, and
                # only one of them means "check the cable". See DRIVE_QUERY.
                answered = bool(dr.DRIVE_QUERY.get("answered"))
                self.disc_title.configure(
                    text=("No optical drive detected" if answered
                          else "The drive query did not answer"))
                self.disc_sub.configure(text="")
                self._show_disc_sub()
                Tip(self.disc_title,
                    "If it is USB, try a direct port - many bus-powered hubs "
                    "cannot supply enough current for a Blu-ray drive. A "
                    "set-top player or games console cannot be used as a "
                    "drive."
                    if answered else
                    "Nothing has been changed and nothing is known to be "
                    "wrong with the drive - the query that lists drives was "
                    "killed for running long, which happens while the drive "
                    "is spinning up or busy. It will be asked again.")
            self.head_note.configure(
                text=f"{len(vals)} optical drive(s)   |   {dr.CONFIG_PATH.name}"
                     + ("   |   portable" if dr.portable_mode() else ""))
        except tk.TclError:
            return              # a theme switch tore the box down mid-scan
        if announce:
            self._status(f"{len(vals)} optical drive(s) found")
        if announce and vals:
            self.identify_disc()

    def current_letter(self):
        v = self.drive_var.get()
        return v.split(":")[0].strip() if v else None

    def identify_disc(self):
        letter = self.current_letter()
        if letter and not self.busy:
            self._run_bg("identify disc", self._identify_work, letter,
                         quiet=True)

    def _identify_work(self, letter):
        dr = self.dr
        if not dr.disc_present(letter):
            self.ui_q.put(("disc", (f"No disc in {letter}:",
                                    "Insert one, then press Identify.")))
            return
        # settings, so general.smart_disc_names is honoured here too: without
        # them classify_disc defaults the feature on, and Identify would rename
        # a nameless disc that the rip itself had been told to leave alone
        disc = dr.classify_disc(letter, settings=self.settings)
        tdesc = {"audio": f"Audio CD - {disc['audio_tracks']} tracks",
                 "dvd": "DVD-Video", "bluray": "Blu-ray", "data": "Data disc",
                 "mixed": "Mixed-mode CD",
                 "unreadable": "Mounted but unreadable",
                 "none": "Blank or unreadable"}.get(disc["type"], disc["type"])
        title = f"{dr.prettify_label(disc.get('label'))}"
        bits = [tdesc, f"drive {letter}:"]
        if disc.get("fs_size"):
            bits.append(dr.human_size(disc["fs_size"]))
        if disc.get("detect"):
            bits.append(disc["detect"])
        self.ui_q.put(("disc", (title, "     ".join(bits))))
        dr.job_set(label=dr.prettify_label(disc.get("label")),
                   type=disc.get("type"), size=disc.get("fs_size"),
                   source=f"{letter}:")

    def _tray(self, open_tray):
        letter = self.current_letter()
        if not letter:
            return
        self.dr.eject_drive(letter, open_tray=open_tray)
        self._status(("Ejected " if open_tray else "Closed tray on ") + letter + ":")

    def _toggle_dry(self):
        self.dr.DRY_RUN = bool(self.dry_var.get())
        self._log_line(f"  * dry run {'ON' if self.dr.DRY_RUN else 'OFF'}")

    def _open_logs(self):
        d = (self.dr.SCRIPT_DIR if self.dr.portable_mode()
             else self.dr.APPDATA_DIR) / "logs"
        if d.exists():
            os.startfile(str(d))
        else:
            messagebox.showinfo(self.dr.APP, f"No logs yet at {d}")

    def _open_out(self):
        """The folder this disc's rip would go to, resolved the way a rip
        resolves it - per disc type, then per section, then the general one."""
        dr = self.dr
        d = dr.out_dir_for(self.settings, dr.JOB.get("type") or "dvd")
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def _clear_log(self):
        self._log_history = []
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")
        self._refresh_log_filter()  # nothing left to filter

    # -- actions ----------------------------------------------------------
    def action_rip(self):
        """The options first, then the run - the same shape as auto-rip.

        It used to start immediately on whatever the tabs said, with one route
        and no way to ask for another. A disc already known to be damaged had
        to fail a title rip again before anything else was tried.
        """
        if self.disc_win is not None and self.disc_win.winfo_exists():
            self.disc_win.deiconify()
            self.disc_win.lift()
            self.disc_win.focus_force()
            self.disc_win.refresh()
            return
        self.disc_win = DiscWindow(self)

    def action_auto(self):
        """The options first, then the run - the same shape as auto-encode."""
        if self.auto_win is not None and self.auto_win.winfo_exists():
            self.auto_win.deiconify()
            self.auto_win.lift()
            self.auto_win.focus_force()
            self.auto_win.refresh()
            return
        self.auto_win = AutoWindow(self)

    def action_info(self):
        letter = self.current_letter()
        if letter:
            self._run_bg("Scanning disc", self.dr.cmd_info, self.settings,
                         letter, rip=True)

    def action_batch_files(self):
        if self.batch_win is not None and self.batch_win.winfo_exists():
            self.batch_win.deiconify()
            self.batch_win.lift()
            self.batch_win.focus_force()
            self.batch_win.refresh()
            return
        self.batch_win = BatchWindow(self)

    def action_calibrate(self):
        """One window per app, raised again if it is already up."""
        w = getattr(self, "cal_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            return
        self.cal_win = CalibrateWindow(self)

    def action_retry(self):
        """One window per app, raised again if it is already up."""
        w = getattr(self, "retry_win", None)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            return
        self.retry_win = RetryWindow(self)

    def action_fit(self):
        """One window per app, raised again if it is already up."""
        if self.fit_win is not None and self.fit_win.winfo_exists():
            self.fit_win.deiconify()
            self.fit_win.lift()
            self.fit_win.focus_force()
            return
        self.fit_win = FitWindow(self)

    def _stop_costs(self):
        """(headline, detail) for the stop confirmation.

        WHAT IT COSTS IS NOT THE SAME FOR EVERY RUN, and that is the only
        thing worth putting in a dialogue. An imaging or salvage pass banks
        every sector it reads in the mapfile, so stopping costs the time since
        the last save and nothing else - re-running resumes past what is
        already there. A MakeMKV title rip throws away everything it has read,
        which on a Blu-ray is up to an hour and up to 27 GB. Telling somebody
        "are you sure?" without that distinction makes them guess.
        """
        act = (self.action or "rip").lower()
        info = getattr(self, "_last_stats", None) or {}
        frac = info.get("frac")
        where = ""
        if isinstance(frac, (int, float)) and 0 < frac <= 1:
            where = f" It is about {frac * 100:.0f}% through."
        if "imag" in act or "salvag" in act or "retry" in act:
            return ("Stop the rip?",
                    "Everything read so far is already in the mapfile, so "
                    "this is resumable: starting again reads only what is "
                    "still missing." + where + chr(10) * 2
                    + "The map is written at the moment you stop, so no "
                      "sector has to be read twice. It can take up to four "
                      "minutes to stop, because the read already in flight "
                      "cannot be interrupted - that is the drive's own retry "
                      "budget, not this app waiting.")
        if "encod" in act or "compress" in act:
            return ("Stop encoding?",
                    "The encode starts from the beginning next time - there "
                    "is no partial output to resume from." + where
                    + chr(10) * 2 + "The lossless remux it is encoding FROM "
                    "is already on disk and is not affected.")
        if "rip" in act or "backup" in act:
            return ("Stop the rip?",
                    "A title rip keeps nothing when it stops. Everything read "
                    "off this disc is discarded and the next attempt starts "
                    "from zero." + where + chr(10) * 2
                    + "If you want it resumable, the salvage path images the "
                      "disc to a mapfile instead.")
        return ("Stop?", f"'{self.action}' is running." + where)

    def action_stop(self):
        # ASKED, because this is the one button that throws work away and it
        # sits next to the one that starts it. A title rip that has read 25 GB
        # over fifty minutes keeps none of it.
        if self.busy:
            head, detail = self._stop_costs()
            if not messagebox.askyesno(f"{self.dr.APP} - {head}",
                                       detail + chr(10) * 2 + head,
                                       default="no", icon="warning",
                                       parent=self):
                return
        n = self.dr.request_cancel()
        self._log_line(f"  [!] stop requested ({n} engine process(es) signalled)")
        # THE BACKGROUND IS NOT TOUCHED, and this is the change of 1 Sep. It
        # used to drop the queue as well, on the reasoning that a queued encode
        # has done nothing - which is true, and it is still not this button's
        # business. The background tail is a different film from the one in the
        # drive, it has its own Stop beside this one, and a person stopping a
        # rip because the disc is unreadable has said nothing at all about the
        # film that finished ten minutes ago.
        if self.dr.BG.pending():
            self._log_line(f"  * {self.dr.BG.pending()} background job(s) are "
                           f"NOT affected and will carry on. Use their own "
                           f"Stop beside this one if you want them gone.")
        # WHICH WAIT IT IS. A title rip is an external process and dies on
        # terminate(); the imager is this app reading the drive itself, and a
        # read the drive has already accepted has up to ~260 s of firmware
        # retry budget that nothing here can shorten. Saying so is the
        # difference between "it is stopping" and "it has ignored me".
        act = (self.action or "").lower()
        if any(k in act for k in ("imag", "salvag", "retry")):
            self.act_sub.configure(
                text="Stopping after the read in flight - up to 4 min")
            self._log_line("  [!] the read already in flight cannot be "
                           "interrupted; it has up to 4 minutes of the "
                           "drive's own retry budget left. The map is saved "
                           "the moment it returns.")
        else:
            self.act_sub.configure(
                text="Stopping - waiting for the engine to exit...")
        self.btn_stop.configure(state="disabled")

    # -- worker plumbing --------------------------------------------------
    def _paint_prog_header(self):
        """The line above the bar: the zoom's caption if there is one, else
        the job header.

        ASKED FOR 3 Sep: the caption REPLACES this line rather than being
        added under it. During a zoom the bar has stopped describing the disc
        and started describing thirty megabytes of it, and "Disc #1 of 1,
        25.3 GiB" above a bar running from 18.34 to 18.35 GB is worse than
        nothing - it is the wrong axis, stated confidently.
        """
        cap, col = getattr(self, "_focus_cap", ("", ""))
        try:
            if cap:
                self.prog_header.configure(
                    text=cap,
                    foreground=CLR["err"] if col == "err" else CLR["ok"])
            else:
                self.prog_header.configure(
                    text=getattr(self, "_prog_head", "") or "",
                    foreground=CLR["muted"])
        except (AttributeError, tk.TclError):
            pass

    def set_zoom_overview(self, lo=None, hi=None, colour=""):
        """The whole-film bar under the zoomed one, with `lo`..`hi` pulsing.

        `lo` None takes it away, which is what the end of a zoom does. See the
        note beside whole_bar for why it exists at all: once the main bar is
        showing a 32 MB bracket, nothing else on screen says where in the film
        that is.
        """
        want = (None if lo is None else (round(float(lo), 5),
                                         round(float(hi), 5), colour))
        if want == getattr(self, "_zoom_over", "x"):
            return
        self._zoom_over = want
        try:
            if want is None:
                self.whole_bar.grid_remove()
                return
            a, b, col = want
            # THE MAP UNDERNEATH, with the span painted over it - so the
            # overview is still the picture of the disc it always was, and the
            # pulse is an annotation on it rather than a replacement for it.
            self.whole_bar.set_cells(self.prog_bar.cells())
            self.whole_bar.set_paint(
                [(a, max(b, a + 0.004), col)] if col else None)
            if not self.whole_bar.winfo_ismapped():
                self.whole_bar.grid(row=3, column=0, columnspan=3,
                                    sticky="we", pady=(4, 0))
        except (AttributeError, tk.TclError):
            pass

    def set_focus_caption(self, text="", colour=""):
        """The window's line above the main bar, during a zoom.

        The same three-stage narration the strip gets - see
        MiniMonitor.set_focus_caption.
        """
        want = (str(text or "").strip(), str(colour or ""))
        if want == getattr(self, "_focus_cap", None):
            return
        self._focus_cap = want
        self._paint_prog_header()

    def set_focus_ends(self, lo, hi):
        """The window's version of the strip's endpoint pair.

        A row of its own under the bar, with what is being looked at in the
        middle - which is the thing the strip has no room to say at all.
        """
        try:
            if lo or hi:
                self.zoom_lo.configure(text=str(lo))
                self.zoom_hi.configure(text=str(hi))
                kind = str(getattr(self._bar_anim, "kind", "") or "")
                self.zoom_what.configure(text={
                    "island": "looking inside the damage for readable film",
                    "refusal": "finding both edges of the damage",
                    "reread": "re-reading what the drive invented",
                    "skipped": "reading back a stretch the sweep stepped "
                               "past without asking",
                }.get(kind, ""))
                # THE ROW STAYS; the two readouts come and go inside it. It
                # holds the bar, so taking the row away would take the bar
                # with it.
                if not self.zoom_lo.winfo_ismapped():
                    self.zoom_lo.grid(row=0, column=0, sticky="w",
                                      padx=(0, 8))
                    self.zoom_hi.grid(row=0, column=2, sticky="e",
                                      padx=(8, 0))
                    self.zoom_what.grid(row=1, column=0, columnspan=3,
                                        sticky="we", pady=(3, 0))
            else:
                # NOT GATED ON BEING MAPPED, which is what this said. A widget
                # in a withdrawn window - collapsed to the tray, or the page
                # not on top - reports winfo_ismapped() False, so the one
                # call that clears this row was skipped and the row came back
                # with the last disc's numbers still in it. Correctness must
                # not depend on the window being visible at the moment the
                # animator lets go; grid_remove on a removed widget is free.
                #
                # AND THE TEXT GOES TOO, not just the geometry: whatever
                # re-grids this row must not be able to bring a stale reading
                # back with it.
                for _w in (self.zoom_lo, self.zoom_hi, self.zoom_what):
                    _w.configure(text="")
                    _w.grid_remove()
        except (AttributeError, tk.TclError):
            pass

    @property
    def bar(self):
        """FocusAnimator asks its host for `.bar`; here that is the window's
        own progress bar."""
        return self.prog_bar

    def run_in_queue(self, what, fn, *args):
        """Put a long job in the background queue instead of the worker.

        For work that wants CPU and not the drive - an auto-encode - so it can
        run alongside a rip. The queue is one thread, so this can never mean
        two encodes at once; it means the batch waits its turn behind the film
        whose disc is in the drive, which is the right way round.

        Deliberately NOT _set_busy: the window is not busy, and freezing the
        controls or lighting up the foreground bar would say a rip was
        running. The background panel and the strip's parenthetical already
        describe queued work, which is what this is.
        """
        try:
            self.dr.clear_bg_cancel()
        except Exception:                                        # noqa: BLE001
            pass
        self._log_line(f"== {what} (queued in the background) ==")
        self.dr.BG.submit(what, lambda: fn(*args))
        self._refresh_bg()
        return True

    def _run_bg(self, what, fn, *args, rip=False, quiet=False):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(self.dr.APP,
                                f"'{self.action}' is still running. Stop it "
                                "first, or wait for it to finish.")
            return False
        self.dr.clear_cancel()
        # THE RUN BOUNDARY, and the only reliable one in the GUI. The engine's
        # job_reset only fires from finally blocks that cmd_image never
        # reaches, so a retry ending mid-damage left the previous disc's
        # "reads failing at 23.88 GB" and its yellow bar on the strip for the
        # whole of the next rip.
        self.dr.reset_read_state()
        self._set_busy(True, what, rip)
        # An auto-rip is the one run that has gaps in it, and the strip says so
        # while it waits. Asked of the function rather than of the button's
        # label, which is a string and could be renamed out from under this.
        self._auto = fn is self.dr.auto_rip_loop
        if not quiet:
            self._log_line(f"== {what} ==")

        def wrapper():
            try:
                fn(*args)
            except self.dr.Cancelled:
                self.ui_q.put(("line", "  [!] cancelled"))
                # THE WHOLE POINT OF CANCELLING CLEANLY. Cancelling a title
                # rip halfway used to leave "<film> - STAGING - DO NOT TOUCH"
                # behind with a partial MKV in it, because the cleanup was
                # straight-line code the unwinding stepped over.
                for _p, _sz in self.dr.clean_scratch("you cancelled the run",
                                                     mine_only=True):
                    self.ui_q.put(("line", f"  [!] removed {_p.name}"))
            except KeyboardInterrupt:
                self.ui_q.put(("line", "  [!] interrupted"))
            except Exception:
                self.ui_q.put(("line", "  [X] " + traceback.format_exc()))
                for _p, _sz in self.dr.clean_scratch("the run failed",
                                                     mine_only=True):
                    self.ui_q.put(("line", f"  [!] removed {_p.name}"))
            except BaseException:
                # SystemExit and friends are NOT Exception, so a run that left
                # this way vanished without a line anywhere - which is how a
                # retry came to go silent after "Phase 1 of 4". Say it, then
                # let it go.
                self.ui_q.put(("line", "  [X] the run exited: "
                               + traceback.format_exc()))
                raise
            finally:
                # The stop flag belongs to the run that was stopped, and dies
                # with it. Left standing, the next thing to start a process is
                # refused before it begins - and the first of those is the drive
                # rescan that follows every run, one queue message later, which
                # reported the refusal as a crash. Clearing it at the top of the
                # next run is not enough, because the queries in between (drive
                # scan, eject, engine probe) are not runs.
                self.dr.clear_cancel()
                self.ui_q.put(("done", what))
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()
        return True

    def _set_busy(self, busy, what=None, rip=False):
        self.busy = busy
        self._between = False
        if busy:
            self._ran = {"done": 0, "failed": 0}
            # kept past the end of the run, so the line afterwards can name what
            # it was a run of: "Auto-rip: 5 done, 1 failed" rather than "Last
            # run", which is true of anything
            self._last_action = what or ""
        self._moved_at = __import__("time").time() if busy else 0.0
        self._last_key, self._moved = None, True
        if self._stalled:
            self._clear_stall()
        if not busy:
            self._auto = False
        if busy:
            self._was_rip = rip         # so 'done' knows whether to celebrate
        self.action = what if busy else None
        self.action_started = __import__("time").time() if busy else 0.0
        self.disc_started = self.action_started
        self._disc_key = None
        for b in self.act_buttons.values():
            b.configure(state="disabled" if busy else "normal")
        # Everything that decides what a run does is frozen while one is
        # running. Changing the mode or the output folder halfway through does
        # not change the run in flight, so offering it is an invitation to
        # believe it did.
        self._freeze_inputs(busy)
        # NOT GRIDDED AWAY WHEN IDLE any more - see the note where
        # watch_row is built. The strip is switched on before a run, not
        # during one.
        self.btn_stop.configure(state="normal" if (busy and rip) else "disabled")
        if busy:
            self._had_error = False
            self._bar_done = False
            self.prog_bar.set_done(False)
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.set_done(False)
            self.act_dot.configure(foreground=CLR["accent"])
            self.act_title.configure(text=what, foreground=CLR["text"])
            self.act_sub.configure(text="Starting...")
            self.btn_stop.configure(text=f"Stop {what.split()[0].lower()}")
            self.title(f"{self.dr.APP} - {what}")
            self.prog_area.grid()
            if self.taskbar is not None:
                self.taskbar.set(None)      # working, percentage not known yet
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.show(what, None, "working", "",
                               stage=self._doing_line())
            if rip and self.dr.sget(self.settings, "general.collapse_on_rip",
                                    False):
                self.collapse()
        else:
            self.act_dot.configure(foreground=CLR["faint"])
            self.act_title.configure(text="Idle")
            _n = self.dr.BG.pending()
            self.act_sub.configure(
                text=(f"Waiting for the next item - {_n} job(s) still "
                      f"finishing in the background."
                      if _n else "Nothing is running."))
            self.prog_bar.set(0)
            for w in (self.lbl_pct, self.lbl_bytes, self.lbl_left, self.lbl_eta,
                      self.prog_header):
                w.configure(text="")
            self._paint_total(None)
            self.title(f"{self.dr.APP} {self.dr.VERSION}")
            self.prog_area.grid_remove()
            # A failure stays on the readouts rather than being tidied away: an
            # unattended run that died at 3am should still say so at breakfast,
            # from the taskbar, without the window being found and read.
            if self.taskbar is not None:
                if self._had_error:
                    self.taskbar.failed()
                else:
                    self.taskbar.clear()
            self._idle_line()
        for win in (getattr(self, "batch_win", None),
                    getattr(self, "auto_win", None)):
            if win is not None and win.winfo_exists():
                win.refresh()           # a finished run frees the Start button
        self._paint_status(force=True)
        self._update_tray_tip()

    # -- theme ------------------------------------------------------------
    def set_theme(self, name):
        """Repaint in the other palette and remember the choice.

        ttk styles are global and many are baked in when a widget is created,
        so the reliable way to switch is to restyle and rebuild - the log is
        replayed afterwards so nothing on screen is lost."""
        if name == self.theme:
            return
        self.theme = set_palette(name)
        self.theme_var.set(self.theme)
        self.dr.sset(self.settings, "general.theme", self.theme)
        try:
            self.dr.save_config(self.settings, self.dr.CONFIG_PATH)
        except OSError as e:
            self._log_line(f"  [!] could not save theme: {e}")
        try:
            tab = self.nb.index(self.nb.select())
        except tk.TclError:
            tab = 0
        history, busy, action = list(self._log_history), self.busy, self.action
        stats = self._last_stats
        drive = self.drive_var.get()
        disc = (self.disc_title.cget("text"), self.disc_sub.cget("text"))
        # The strip is a Toplevel and so a child of this window: the loop below
        # takes it with everything else. Note it and put it back in the new
        # palette afterwards.
        had_mini = self.mini is not None and self.mini.winfo_exists()
        self.mini = None
        for child in list(self.winfo_children()):
            child.destroy()
        self.configure(background=CLR["bg"])
        self.vars.clear()
        self._filters.clear()
        self._tabframes.clear()
        self._filled.clear()
        self._setup_theme()
        self._set_icon()
        self._apply_chrome()
        self._build()
        if had_mini:
            self.show_mini(remember=False)
        self._log_history = history
        for entry in history:
            self._log_line(entry[1], ts=entry[0],
                           src=(entry[2] if len(entry) > 2 else "fg"))
        self._refresh_log_filter()
        self.refresh_drives(announce=False)
        if drive:
            self.drive_var.set(drive)
        self.disc_title.configure(text=disc[0])
        self.disc_sub.configure(text=disc[1])
        self._show_disc_sub()
        if busy:
            self._set_busy(True, action, rip=True)
            # a rip pushes an update about once a second, but the bar should not
            # sit blank and headerless in between
            if stats:
                self._show_progress(stats)
        try:
            self.nb.select(tab)
        except tk.TclError:
            pass
        # ttk posts <<NotebookTabChanged>> to the queue rather than calling the
        # binding, so the tab the user is looking at would not be refilled until
        # the next pass round the event loop - after the prewarm timer below, if
        # that ever got in first. Refill it here and let the event be the no-op.
        self._on_tab_changed()
        self.after(150, self._prewarm)   # the palette changed, so they all rebuild
        self._status(f"{self.theme.capitalize()} theme")

    def _tick_clock(self):
        """Keep an elapsed timer visible, so a quiet stage still looks alive."""
        if self.busy and self.action_started:
            import time as _t
            el = self.dr.human_time(_t.time() - self.action_started)
            base = self.act_title.cget("text").split("   ·   ")[0]
            self.act_title.configure(text=f"{base}   ·   running {el}")
            # gentle pulse so it is obvious something is in flight
            cur = str(self.act_dot.cget("foreground"))
            self.act_dot.configure(
                foreground=CLR["accent"] if cur != CLR["accent"] else "#9dbcf5")
        # The taskbar can be moved to another edge, made taller, or set to hide
        # itself, and none of that sends us anything. A docked strip re-checks it
        # on the second that was already ticking.
        if self.mini is not None and self.mini.winfo_exists():
            if self.mini.orphaned():
                # Explorer restarted and took the strip with it, the taskbar
                # being its owner. Tk still believes the widget is there, so
                # nothing else would ever notice. Rebuilt directly rather than
                # through hide_mini, which would take a collapsed window out of
                # the notification area on the way past.
                try:
                    self.mini.destroy()
                except tk.TclError:
                    pass
                self.mini = None
                self.show_mini(remember=False)
            else:
                self.mini.retrack()
                # retrack only follows the taskbar, and only while docked. A
                # strip that has been hidden or buried needs putting back, and
                # collapsing withdraws its owner - which hides it - so the once
                # at collapse time is not enough. reassert is built to be
                # called on a tick: it costs one visibility check and one hit
                # test when there is nothing wrong, touches z-order only when
                # there is, and leaves a strip that is down on purpose alone.
                self.mini.reassert()
        # THE PRE-BAR LINE, on the clock. Nothing else repaints the strip
        # before the first progress arrives, so a doing() set two seconds after
        # the run started would otherwise wait for the scan to finish to be
        # seen - which is most of the minute it exists to describe.
        if self.busy and not self._last_stats and not self._stalled:
            _d = self._doing_line()
            if _d != getattr(self, "_said_doing", None):
                self._said_doing = _d
                self.repaint_mini()
        if self._off_at:
            self._paint_shutdown()      # a deadline, so it counts down visibly
        elif self._stalled:
            self._paint_stall()         # the figure in it is a clock, not a fact
        self._paint_status()
        if self._collapsed and self.busy:
            self._update_tray_tip()     # the elapsed figure moves; so should it
        if not self._closing:
            self.after(1000, self._tick_clock)

    # sinks: called from the worker thread, so they only enqueue
    def _sink_output(self, text, level=""):
        # TAGGED AT THE SOURCE, because this is the only place that still knows.
        # By the time a line reaches _log_line it is a string on the UI thread
        # and the thread that wrote it is gone. See BG_THREAD_NAME.
        #
        # The LEVEL is tagged at the source too, and for a stronger reason: it
        # is what the emitter knew and the text is only what it printed. See
        # say() in the engine for the two faults that came of re-deriving it.
        self.ui_q.put(("bgline" if self.dr.on_bg_thread() else "line",
                       (text, level)))

    def _sink_progress(self, info):
        self.ui_q.put(("prog", info))

    def _sink_event(self, event, message):
        self.ui_q.put(("event", (event, message)))

    def _sink_prompt(self, kind, prompt, default, choices):
        box = {"answer": default}
        done = threading.Event()
        self.pending_prompts.append(done)
        self.ui_q.put(("prompt", (kind, prompt, default, choices, box, done)))
        done.wait()
        try:
            self.pending_prompts.remove(done)
        except ValueError:
            pass
        return box["answer"]

    # How long the green "DONE" flash stays in place of the parenthetical.
    BG_FLASH_SECONDS = 25.0

    def _bg_pending(self):
        """How many background jobs there are, and never an exception.

        The one question the strip's view button asks. NOT _bg_note's
        sentence, which is also non-empty for the few seconds after a job
        finishes - that is the "DONE - <film> was created" flash, and a button
        offering to show the progress of something that has just ended is a
        button that does nothing when pressed.
        """
        try:
            return int(self.dr.BG.pending() or 0)
        except Exception:                                        # noqa: BLE001
            return 0

    def _bg_note(self):
        """(text, colour) for the background parenthetical, or ("", None).

        ONE PLACE, because there are four things that show it: the top row of
        the strip, the compact/taskbar line, the mini monitor and the idle
        line. Pirates of the Caribbean sat on MakeMKV's own "finishing the
        file" for a while with an encode running behind it, and none of those
        four said so - which is how a legible wait reads as a hang.
        """
        import time as _t
        try:
            st = self.dr.bg_state()
            pending = self.dr.BG.pending()
        except Exception:                                        # noqa: BLE001
            return "", None
        last = st.get("last") or {}
        # THE FLASH FIRST, so the outcome is never lost to the next job
        # starting a fraction of a second later.
        at = float(last.get("at") or 0)
        if at and (_t.time() - at) < self._bg_note_window():
            if last.get("error"):
                return (f"{last.get('what') or 'background job'}: "
                        f"{last['error']}"), CLR["err"]
            if last.get("out"):
                return f"DONE - {last['out']} was created", CLR["ok"]
            return f"DONE - {last.get('what') or 'background job'}", CLR["ok"]
        if not pending:
            return "", None
        stage = st.get("stage") or "working on"
        what = st.get("what") or st.get("running") or ""
        more = f" +{pending - 1}" if pending > 1 else ""
        # WITH THE PERCENTAGE. The question somebody glances at this to answer
        # is "is it nearly done", and the parenthetical answered "is it
        # running", which they could already see from the fact of it being
        # there. Only when there IS one: a job between two passes has no
        # fraction and "(0%)" on a job most of the way through is worse than
        # no number.
        try:
            _f = st.get("frac")
            pct = f" {float(_f) * 100:.0f}%" if _f is not None else ""
        except (TypeError, ValueError):
            pct = ""
        return (f"(background {stage} {what}{pct}{more})".replace("  ", " "),
                None)

    def _bg_note_window(self):
        """How long a finished background job keeps the note.

        AS LONG AS THE FLASH AND THE CHIME, AND NO LONGER, while a rip is
        actually running. BG_FLASH_SECONDS is 25 and the flash is however many
        170 ms blinks fit the finish sound - about three seconds - so the
        message outlived the thing it was announcing by twenty seconds, with a
        live rip underneath it the whole time. Reported 2 Sep.

        Idle it keeps the full window, because then it is the only thing the
        strip has to say and there is nothing for it to be in the way of.
        """
        if (not self.busy) or self._between:
            return self.BG_FLASH_SECONDS
        m = self.mini
        if m is not None:
            try:
                if m.winfo_exists():
                    return m.flash_seconds() + 0.4
            except tk.TclError:
                pass
        return 4.0

    def _bg_idle_line(self):
        """"encoding <film> in the background (43%)", or "".

        Asked for by name. The idle line already says what the RUN is waiting
        for; this says what the machine is doing while it waits, which on a
        deferred tail is the only work happening at all.

        Percentage only when there is one. A job between two passes has no
        fraction - the encode's audio pass, the checksum - and "(0%)" on a job
        that is most of the way through is worse than no number.
        """
        try:
            st = self.dr.bg_state()
            pending = int(self.dr.BG.pending() or 0)
        except Exception:                                        # noqa: BLE001
            return ""
        if not pending:
            return ""
        stage = str(st.get("stage") or "").strip()
        what = str(st.get("what") or st.get("running") or "").strip()
        if not stage and not what:
            return ""
        frac = st.get("frac")
        try:
            pct = f" ({float(frac) * 100:.0f}%)" if frac is not None else ""
        except (TypeError, ValueError):
            pct = ""
        more = f", {pending - 1} more waiting" if pending > 1 else ""
        head = " ".join(x for x in (stage or "working on", what) if x)
        return f"{head} in the background{pct}{more}"

    def strip_show_bg(self, on):
        """Turn the strip over to the background job, or back to the rip.

        Both of the strip's buttons come here - see MiniMonitor._tap_view -
        because this is the side that has both tasks' numbers, and because
        going BACK is not a matter of drawing something else: it is
        repaint_mini, which rebuilds whatever the run is actually doing from
        the run's own state. That is what makes "shows the waiting for next
        item screen again if there is no foreground task" fall out rather than
        being a case to write: with nothing running, the thing repaint_mini
        rebuilds IS the waiting line.

        Asking for the background view when there is no background job left
        gives the foreground, which is the honest answer to a button pressed
        a moment after the encode finished.
        """
        m = self.mini
        if m is None or not m.winfo_exists():
            return False
        want = bool(on) and bool(self._bg_pending())
        m._bg_view = want
        if want:
            if self._paint_bg_strip():
                return True
            m._bg_view = False          # nothing there after all
        self.repaint_mini()
        return False

    @staticmethod
    def _bg_stage_alone(stage):
        """A background stage with the name it was written to introduce gone.

        bg_stage's wording is a PREFIX: "searching for the quality of",
        "adding the audio to", "looking for the black borders on" - every one
        of them written to be followed by the film's name, which is how the
        window's own background line reads it. Put the film somewhere else -
        the strip's title field, in the background view - and the preposition
        is left dangling: "How To Train Your Dragon - searching for the
        quality of".

        Cut here rather than reworded in the engine, for the reason
        progressive() gives about phase names: the engine's wording is what
        the log and the window say, and this is presentation.
        """
        words = str(stage or "").strip().split()
        while words and words[-1].lower() in ("of", "on", "to", "for",
                                              "in", "at", "from"):
            words.pop()
        return " ".join(words)

    def _paint_bg_strip(self):
        """The background job's own progress, on the whole strip.

        The other half of the button: everything the strip can draw, drawn
        from BG_STATE instead of from the rip. It is the same show() the rip
        uses, in the same two shapes, so the background view cannot end up
        with a layout - or a set of fields at a given width - that the
        foreground one does not have.

        Returns False when there is no background work to describe, which is
        the signal to put the foreground back.
        """
        m = self.mini
        if m is None or not m.winfo_exists():
            return False
        try:
            st = self.dr.bg_state() or {}
            pending = int(self.dr.BG.pending() or 0)
        except Exception:                                        # noqa: BLE001
            return False
        if not pending:
            return False
        what = str(st.get("what") or st.get("running")
                   or st.get("label") or "background job")
        # WHICH PASS, NOT JUST WHICH VERB. bg_stage says "encoding"; the
        # Progress label inside it says which of the seven things that means -
        # the quality search, the black-border scan, the video pass, the audio
        # pass, the mux. The window's own background line has said both since
        # 31 Aug; the strip only ever had room for the verb.
        stage = self._bg_stage_alone(st.get("stage")) or "working"
        label = str(st.get("label") or "").strip()
        bits = [stage]
        if label and label.lower() not in stage.lower():
            bits.append(label)
        if pending > 1:
            bits.append(f"+{pending - 1} queued")
        stage = " · ".join(x for x in bits if x)
        try:
            frac = st.get("frac")
            frac = None if frac is None else float(frac)
        except (TypeError, ValueError):
            frac = None
        done, total = st.get("done"), st.get("total")
        sizes = (self.dr.size_pair(done, total)
                 if done is not None and total else "")
        eta = str(st.get("eta") or "")
        if eta and eta != "--:--" and not eta.endswith(" left"):
            eta = f"{eta} left"
        elif eta == "--:--":
            eta = ""
        elapsed = 0.0
        try:
            elapsed = float(st.get("elapsed") or 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        _el = self.dr.human_time(elapsed) if elapsed >= 1 else ""
        _rate = ""
        if elapsed >= 1 and done:
            try:
                _rate = f"{self.dr.human_size(float(done) / elapsed)}/s"
            except (TypeError, ValueError, ZeroDivisionError):
                _rate = ""
        pct = "working" if frac is None else f"{frac * 100:.2f}%"
        if m.docked:
            m.show(what, frac, pct, sizes, eta, "", stage,
                   elapsed=_el, rate=_rate, bg=True)
        else:
            # THE SAME FIELDS THE RIP GETS, in the same order: what is left of
            # the sentence goes on the detail line and the clock and the rate
            # on the one under it. `info` is the disc line floating, and an
            # encode has no disc - so it carries where the film is going,
            # which is the equivalent fact about a background job.
            _bits = [x for x in (sizes, eta) if x]
            m.show(what, frac, pct, "  ·  ".join(_bits),
                   "  ·  ".join(x for x in (_el, _rate) if x),
                   str(st.get("out") or ""), stage, bg=True)
        return True

    def _paint_bg_block(self, st, pending):
        """The background job's own line and bar, detached and with room.

        ASKED FOR 9 Sep: "when the progress strip is detached and there is
        room, the background task progress bar shows beneath the foreground
        one same as the app itself". The strip decides whether there is room -
        see MiniMonitor._shows - and this says what goes in it.

        One line rather than the window's three: the window puts the
        percentage, the byte counts and the time remaining on a row of their
        own under the bar, and on a strip that would be a third row for a job
        that is not the one in the drive. The same figures, on the line above.
        """
        m = self.mini
        if m is None or not m.winfo_exists():
            return False
        was = m.shows_bg()
        on = m.set_bg_block(bool(pending) and not m._bg_view)
        if on != was:
            # THE BLOCK AND THE BUTTON ARE ONE DECISION, so the strip is
            # redrawn when it changes: the button exists because the strip has
            # no room to show the thing itself, and set_bg_block only re-runs
            # the LAYOUT. Without this the button stayed on a strip that had
            # just gained the bar it offers to show, until the drive happened
            # to report something.
            self.repaint_mini()
        if not on:
            return False
        st = st or {}
        what = str(st.get("what") or st.get("running")
                   or st.get("label") or "background job")
        stage = str(st.get("stage") or "working on").strip()
        label = str(st.get("label") or "").strip()
        head = f"In the background: {stage} {what}".replace("  ", " ")
        if label and label.lower() not in head.lower():
            head += f"   ·   {label}"
        try:
            frac = st.get("frac")
            frac = None if frac is None else float(frac)
        except (TypeError, ValueError):
            frac = None
        tail = []
        if frac is not None:
            tail.append(f"{frac * 100:.0f}%")
        done, total = st.get("done"), st.get("total")
        if done is not None and total:
            tail.append(self.dr.size_pair(done, total))
        eta = str(st.get("eta") or "")
        if eta and eta != "--:--":
            tail.append(f"{eta} left")
        if int(pending or 0) > 1:
            tail.append(f"+{int(pending) - 1} queued")
        if tail:
            head += "   ·   " + "   ·   ".join(tail)
        m.set_bg_progress(head, frac)
        return True

    def _refresh_bg(self):
        """The background parenthetical and the detail line, every poll."""
        self._paint_bg_stop()
        self._paint_skip()
        note, colour = self._bg_note()
        # A JOB STARTING OR ENDING IS A CHANGE TO THE STRIP, and nothing else
        # would notice. The strip is repainted when the DRIVE reports, and an
        # encode queued behind an idle drive makes it report nothing at all -
        # so the button that is the whole way over to that encode never
        # appeared. Found on the idle strip: a job submitted after the last
        # layout left the strip with no button on it until something else
        # happened to repaint. The same edge puts up the detached block.
        _has = bool(self._bg_pending())
        if _has != self._bg_seen:
            self._bg_seen = _has
            self.repaint_mini()
        # THE HEADLINE, BRIEFLY, WHEN A BACKGROUND JOB ENDS. Asked for by name:
        # somebody watching only a docked strip has no other way to learn that
        # the film they were waiting for exists. It takes the title line rather
        # than the parenthetical for the length of the flash, and only while
        # the foreground has nothing of its own to say - a rip in progress
        # keeps its own headline and gets the parenthetical, as before.
        _quiet = (not self.busy) or self._between
        # _bg_note returns the COLOUR, not a name - CLR["err"], not "err". A
        # smoke run caught this comparing it against the name, which would have
        # flashed a failed background job green.
        _show = note if (colour and _quiet) else None
        if _show != self._bg_headline:
            self._bg_headline = _show
            if _show:
                self._idle_line(_show, failed=(colour == CLR["err"]))
            elif _quiet:
                self._idle_line()       # the flash expired; put the line back
        try:
            self.act_right.configure(text=note,
                                     foreground=colour or CLR["accent"])
        except (AttributeError, tk.TclError):
            pass
        try:
            st = self.dr.bg_state()
            n = self.dr.BG.pending()
        except Exception:                                        # noqa: BLE001
            return
        # THE STRIP, WHILE IT IS DESCRIBING THIS JOB RATHER THAN THE RIP.
        # Nothing else repaints it then - the foreground's own progress reports
        # are refused, and an encode's numbers move whether or not a disc is
        # being read - so the drain is what keeps it live. Twelve times a
        # second, which is why both painters are guarded on what changed.
        if self.mini is not None and getattr(self.mini, "_bg_view", False):
            if not self._paint_bg_strip():
                # the job ended under the view: back to the rip, or to the
                # waiting line if there is no rip either
                self.strip_show_bg(False)
        # ...AND THE SECOND BAR, for a detached strip with the height for it.
        self._paint_bg_block(st, n)
        if not n:
            if self.act_bg.winfo_ismapped():
                self.act_bg.pack_forget()
            self._paint_bg_bar(st, False)
            return
        what = st.get("what") or st.get("running") or st.get("label") or ""
        stage = st.get("stage") or "working on"
        frac = st.get("frac")
        pct = f" {frac * 100:.0f}%" if isinstance(frac, float) else ""
        more = f" (+{n - 1} queued)" if n > 1 else ""
        self.act_bg.configure(
            text=f"in the background: {stage} {what}{pct}{more}")
        if not self.act_bg.winfo_ismapped():
            self.act_bg.pack(anchor="w", pady=(1, 0))
        self._paint_bg_bar(st, True)

    def _paint_skip(self):
        """On screen only while the sweep is running.

        The sweep is the one phase where stepping forward means skipping past
        trouble rather than skipping the recovery, and it is the only phase
        that reads the disc in order - so it is the only one where "the next
        twenty seconds" is a thing that exists.
        """
        try:
            want = bool(self.busy) and not self._between and (
                str((self._last_stats or {}).get("label") or "") == "sweep")
        except (AttributeError, TypeError):
            want = False
        try:
            if want and not self._skip_shown:
                self._skip_shown = True
                self.skip_box.grid(row=0, column=2, sticky="e", padx=(10, 0))
            elif not want and self._skip_shown:
                self._skip_shown = False
                self.skip_box.grid_remove()
        except tk.TclError:
            pass

    def action_skip_playback(self):
        """Step the sweep over this many seconds of film.

        No confirmation. It is a small, named, recoverable amount - the map
        records it as untried and --retry-skipped comes back for it - and the
        moment somebody wants it is the moment a disc has been grinding for
        five minutes, which is not a moment to put a dialogue in front of.
        """
        raw = (self.skip_secs.get() or "").strip()
        try:
            secs = float(raw)
        except ValueError:
            messagebox.showinfo(
                self.dr.APP,
                f"'{raw}' is not a number of seconds.")
            return
        if secs <= 0:
            return
        n = self.dr.playback_seconds_to_sectors(secs)
        if not n:
            return
        total = self.dr.request_skip_ahead(n)
        mb = n * 2048 / 1e6
        self._log_line(f"  [>] asked the sweep to step over {secs:g}s of "
                       f"playback ({mb:.0f} MB)"
                       + (f" - {total * 2048 / 1e6:.0f} MB outstanding"
                          if total > n else ""))

    def _paint_bg_stop(self):
        """Its own Stop, on screen only while there is background work.

        In the watch row, at the far right - which is the row directly above
        the background job's own bar. PACKED, not gridded: the row is packed
        left to right and a `side="right"` widget packed last takes the
        right-hand end of it, which is where it has to be for the button and
        the bar it is about to line up.
        """
        try:
            pending = int(self.dr.BG.pending() or 0)
        except Exception:                                        # noqa: BLE001
            pending = 0
        try:
            if pending and not self._bg_stop_shown:
                self._bg_stop_shown = True
                self.btn_bg_stop.pack(side="right")
            elif not pending and self._bg_stop_shown:
                self._bg_stop_shown = False
                self.btn_bg_stop.pack_forget()
            if pending:
                # NAMES THE FILM, because the whole point of the second button
                # is that it is about a different one from the first.
                what = str((self.dr.bg_state() or {}).get("what") or "").strip()
                more = f" +{pending - 1}" if pending > 1 else ""
                self.btn_bg_stop.configure(
                    text=(f"Stop {what}{more}" if what
                          else f"Stop background{more}"))
        except tk.TclError:
            pass

    def action_bg_stop(self):
        """Throw away the background tail. Asked, because it loses a film.

        The foreground Stop can afford to be blunt: a title rip keeps nothing
        anyway, and the imager has a mapfile. This one is different in kind -
        the job in hand is an encode replacing a lossless remux, and past a
        certain point that lossless has already been deleted.
        """
        try:
            st = self.dr.bg_state() or {}
            pending = int(self.dr.BG.pending() or 0)
        except Exception:                                        # noqa: BLE001
            st, pending = {}, 0
        if not pending:
            return
        what = str(st.get("what") or st.get("running") or "the background job")
        stage = str(st.get("stage") or "working on")
        queued = max(0, pending - 1)
        if not messagebox.askyesno(
                f"{self.dr.APP} - Stop the background work?",
                f"'{stage} {what}' is running behind the rip in the drive."
                + (f" {queued} more job(s) are queued behind it." if queued
                   else "")
                + chr(10) * 2
                + "An encode starts from the beginning next time - there is "
                  "no partial output to resume from. If the lossless remux it "
                  "is encoding FROM has already been deleted, stopping here "
                  "loses that film until the disc is ripped again."
                + chr(10) * 2 + "The rip in the drive is NOT affected."
                + chr(10) * 2 + "Stop the background work?",
                default="no", icon="warning", parent=self):
            return
        procs, dropped = self.dr.request_bg_cancel()
        self._log_line(f"  [!] background stop requested ({procs} process(es) "
                       f"signalled, {dropped} queued job(s) dropped)")
        try:
            self.btn_bg_stop.configure(state="disabled")
        except tk.TclError:
            pass

    def _paint_bg_bar(self, st, running):
        """The background job's own bar, or nothing at all.

        `st` is a bg_state() snapshot. Everything on it can be absent: a job
        between two Progress objects - after the prune and before the encode -
        reports a stage and no fraction, and an indeterminate bar is the honest
        drawing of that rather than a zero.
        """
        area = getattr(self, "bg_area", None)
        if area is None or not area.winfo_exists():
            return
        if not running:
            if self._bg_shown:
                self._bg_shown = False
                try:
                    self.bg_bar.stop()
                except tk.TclError:
                    pass
                area.grid_remove()
            return
        if not self._bg_shown:
            self._bg_shown = True
            # Row 3 of the Activity card, under the rip's progress block
            # and the watch row - NOT inside prog_area, which is gridded away
            # the moment the foreground goes idle. That is the state a
            # background encode is most often the only thing in.
            area.grid(row=3, column=0, sticky="we", pady=(4, 0))
        stage = str(st.get("stage") or "working on")
        what = str(st.get("what") or st.get("running")
                   or st.get("label") or "")
        queued = int(st.get("queued") or 0)
        # AND WHICH PASS. bg_stage says "encoding"; the Progress label inside
        # it says which of the seven things that means - looking for black
        # borders, the quality search, pricing AQ, the video pass, the audio
        # pass, the mux, the subtitles. The strip's parenthetical is spoken for
        # by the wording that was asked for; this line has the room.
        label = str(st.get("label") or "").strip()
        head = f"In the background: {stage} {what}".replace("  ", " ")
        if label and label.lower() not in head.lower():
            head += f"   ·   {label}"
        self.bg_head.configure(
            text=head + (f"   (+{queued} queued)" if queued else ""))
        frac = st.get("frac")
        if isinstance(frac, float):
            try:
                self.bg_bar.stop()
            except tk.TclError:
                pass
            self.bg_bar.configure(mode="determinate",
                                  value=int(max(0.0, min(1.0, frac)) * 1000))
            self.bg_pct.configure(text=f"{frac * 100:.1f}%")
        else:
            # NOT A ZERO. A job with no fraction is between two passes, not at
            # the beginning of one, and an empty bar says the wrong thing about
            # it - which is the whole complaint this block answers.
            if str(self.bg_bar.cget("mode")) != "indeterminate":
                self.bg_bar.configure(mode="indeterminate")
                self.bg_bar.start(30)
            self.bg_pct.configure(text="")
        done, total = st.get("done"), st.get("total")
        if done is not None and total:
            self.bg_bytes.configure(text=self.dr.size_pair(done, total))
        elif done:
            self.bg_bytes.configure(text=f"{self.dr.human_size(done)} written")
        else:
            self.bg_bytes.configure(text="")
        eta = str(st.get("eta") or "")
        self.bg_eta.configure(
            text=("" if not eta or eta == "--:--" else f"{eta} left"))

    def _drain(self):
        self._refresh_bg()
        try:
            for _ in range(500):
                kind, payload = self.ui_q.get_nowait()
                if kind == "line":
                    self._log_line(payload[0], level=payload[1])
                elif kind == "bgline":
                    self._log_line(payload[0], src="bg", level=payload[1])
                elif kind == "prog":
                    self._show_progress(payload)
                elif kind == "disc":
                    self.disc_title.configure(text=payload[0])
                    self.disc_sub.configure(text=payload[1])
                    self._show_disc_sub()
                elif kind == "drives":
                    self._apply_drives(*payload)
                elif kind == "tools":
                    self._apply_tools(payload)
                elif kind == "tray":
                    # posted from inside the window procedure, so the actual work
                    # happens out here rather than re-entering Tcl from a callback
                    if payload == "show":
                        self.uncollapse()
                    else:
                        self._tray_menu()
                elif kind == "prompt":
                    self._show_prompt(*payload)
                elif kind == "event":
                    self._engine_event(*payload)
                elif kind == "shutdown":
                    self._shutdown_coming(payload)
                elif kind == "waiting":
                    self._wait_on_user(*payload)
                elif kind == "voices":
                    self._apply_voices(*payload)
                elif kind == "fit":
                    what, win, data = payload
                    if win.winfo_exists():
                        (win.probed if what == "probed"
                         else win.measured_bars)(data)
                elif kind == "worklist":
                    # a folder walk finished; both the auto-encode and the
                    # fit-to-screen windows resolve their lists this way
                    win, gen, todo, skipped, size, err = payload
                    try:
                        if win.winfo_exists():
                            win._resolved(gen, todo, skipped, size, err)
                    except tk.TclError:
                        pass
                elif kind == "done":
                    self._set_busy(False)
                    self._status(f"{payload} finished")
                    self.refresh_drives(announce=False)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(80, self._drain)

    # -- log --------------------------------------------------------------
    # WHOSE LINES TO SHOW. Two films report into one log the moment a rip's tail
    # is deferred, and a filter is only a control while both are talking - so
    # this is a plain cycle on a button that disables itself when there is
    # nothing to choose between. Not a combo box: three states, one of which is
    # right almost always.
    LOG_FILTERS = ("all", "fg", "bg")
    LOG_FILTER_LABELS = {"all": "Both", "fg": "This disc only",
                         "bg": "Background only"}

    def _log_shows(self, src):
        want = getattr(self, "_log_filter", "all")
        return want == "all" or want == (src or "fg")

    def _cycle_log_filter(self):
        want = getattr(self, "_log_filter", "all")
        i = self.LOG_FILTERS.index(want) if want in self.LOG_FILTERS else 0
        self._log_filter = self.LOG_FILTERS[(i + 1) % len(self.LOG_FILTERS)]
        self._relog()
        self._refresh_log_filter()

    def _refresh_log_filter(self):
        """Enabled only while the log actually holds both kinds of line.

        Asked for as "greyed out when only one process is running". Keyed on the
        HISTORY rather than on BG.pending(), because a filter is still the thing
        you want a minute after a background job finished and left forty lines
        behind - and it can do nothing at all when every line is one colour.
        """
        btn = getattr(self, "btn_log_filter", None)
        if btn is None or not btn.winfo_exists():
            return
        kinds = {(e[2] if len(e) > 2 else "fg") for e in self._log_history}
        both = len(kinds) > 1
        want = getattr(self, "_log_filter", "all")
        btn.configure(text=self.LOG_FILTER_LABELS.get(want, "Both"),
                      state=("normal" if both else "disabled"))
        if not both and want != "all":
            # nothing left to hide, so a filter still hiding things would make
            # the log look truncated with no way to see why
            self._log_filter = "all"
            self._relog()

    def _relog(self):
        """Redraw the whole log from the history, honouring the filter."""
        t = self.logbox
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.configure(state="disabled")
        for entry in list(self._log_history):
            ts, line = entry[0], entry[1]
            self._log_line(line, ts=ts, src=(entry[2] if len(entry) > 2
                                             else "fg"))

    LEVEL_TAGS = {
        "ok":   ("✓", "g_ok", "body"),
        "warn": ("!", "g_warn", "body_warn"),
        "err":  ("✗", "g_err", "body_err"),
        "info": ("•", "g_info", "body"),
    }
    # What the engine puts in FRONT of each of those, so the glyph replaces the
    # marker rather than sitting next to it.
    LEVEL_MARKS = {"ok": "[OK]", "warn": "[!]", "err": "[X]", "info": "*"}

    def _log_line(self, text, ts=None, src="fg", level=""):
        """Render one line as a normal application log entry.

        The engine's console markers ([OK], [!], [X], *) are turned into a small
        coloured glyph rather than printed verbatim - reading raw terminal
        decoration in a window is what makes it feel like a screen recording.

        `src` is "bg" for anything the background worker said. It is decided in
        _sink_output, which is the last place that knows which thread wrote it.

        `level` is the severity the ENGINE knew, carried across the seam beside
        the text. When it is given it is believed, and the marker is only
        stripped off the front for display. When it is empty the markers are
        parsed as before - which is what this window's own ~20 _log_line calls
        rely on, and they are not the engine speaking, so they must not be able
        to set the run's verdict. See _had_error below.
        """
        raw = self.dr.strip_ansi(text).rstrip()
        if not raw.strip():
            return
        import datetime
        # Keep the line and the time it arrived, so a theme change can replay the
        # log without restamping every entry with the moment of the switch.
        if ts is None:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self._log_history.append((ts, raw, src))
            if len(self._log_history) > 2000:
                del self._log_history[:400]
            # a strip big enough to be showing the log gets it as it happens,
            # rather than only when something else makes it repaint
            if self.mini is not None and self.mini.winfo_exists():
                self.mini.log_push(ts, raw, src)
            if src == "bg":
                self._refresh_log_filter()
        if not self._log_shows(src):
            return
        body = raw.strip()
        glyph, gtag, btag, head = "", "", "body", False
        if level in self.LEVEL_TAGS:
            glyph, gtag, btag = self.LEVEL_TAGS[level]
            mark = self.LEVEL_MARKS[level]
            if body.startswith(mark):
                body = body[len(mark):].strip()
            # THE VERDICT, and only from here. This is the engine's own err(),
            # not a string that happens to start "[X]" - and this window writes
            # about twenty of those itself, two of them for a config save that
            # failed. On a read-only config path, changing a setting mid-rip
            # used to report the RIP as failed.
            if level == "err" and self.busy:
                self._had_error = True
                if self.taskbar is not None:
                    self.taskbar.state("error")
        elif level == "head":
            body, head = body.strip("= ").strip(), True
        elif body.startswith("[OK]"):
            glyph, gtag, body = "✓", "g_ok", body[4:].strip()
        elif body.startswith("[!]"):
            glyph, gtag, btag, body = "!", "g_warn", "body_warn", body[3:].strip()
        elif body.startswith("[X]"):
            glyph, gtag, btag, body = "✗", "g_err", "body_err", body[3:].strip()
        elif body.startswith("* "):
            glyph, gtag, body = "•", "g_info", body[2:].strip()
        elif body.startswith("==") and body.endswith("=="):
            body, head = body.strip("= ").strip(), True
        else:
            btag = "body_dim"       # continuation / engine detail lines
        if src == "bg":
            # A FAILURE STILL READS AS A FAILURE. The body colour says which
            # film a line belongs to, and that is worth less than knowing
            # something went wrong - so an error or a warning keeps its own
            # colour and only the glyph and the timestamp carry the source.
            if btag in ("body", "body_dim"):
                btag = "body_bg"

        t = self.logbox
        at_end = t.yview()[1] > 0.999 or self.autoscroll.get()
        t.configure(state="normal")
        if head:
            if t.index("end-1c") != "1.0":
                t.insert("end", "\n")
            t.insert("end", body + "\n", ("head",))
        else:
            t.insert("end", ts + "   ", ("ts_bg" if src == "bg" else "ts",))
            t.insert("end", (glyph or " ") + "  ", (gtag or "body_dim",))
            t.insert("end", body + "\n", (btag,))
        if int(t.index("end-1c").split(".")[0]) > 3000:
            t.delete("1.0", "400.0")
        t.configure(state="disabled")
        if at_end:
            t.see("end")

    # -- progress ---------------------------------------------------------
    def _paint_total(self, run):
        """The second bar: how far through the whole pile, not this one file."""
        if not run or not run.get("total"):
            if self._total_shown:
                self._total_shown = False
                self.total_bar.grid_remove()
                self.total_row.grid_remove()
            return
        if not self._total_shown:
            self._total_shown = True
            self.total_bar.grid(row=3, column=0, sticky="we", pady=(10, 0))
            self.total_row.grid(row=4, column=0, sticky="we", pady=(5, 0))
        frac = max(0.0, min(1.0, float(run.get("frac") or 0.0)))
        self.total_bar.configure(
            value=frac * 1000,
            style="TotalDone.Horizontal.TProgressbar" if frac >= 0.999
            else "Total.Horizontal.TProgressbar")
        job = self.dr.JOB
        unit = str(job.get("unit") or "item").lower()
        bits = [("Whole batch" if unit == "file" else "Whole run")
                + f" {frac * 100:.0f}%"]
        if job.get("total"):
            bits.append(f"{unit} {job.get('number') or 0} of {job['total']}")
        pair = self.dr.size_pair(run.get("done"), run.get("total"))
        if pair:
            bits.append(pair)
        self.lbl_total.configure(text="   ".join(bits))
        eta = str(run.get("eta") or "")
        self.lbl_total_eta.configure(
            text=f"all of it done in {eta}" if eta and eta != "--:--" else "")

    def _show_progress(self, info):
        self._last_stats = info     # so a theme switch can repaint it as-is
        self._detected = ""         # the rip is reporting; it noticed
        # A NEW ITEM GETS A NEW CLOCK. The label is what changes between discs
        # of an auto-rip and between films of an auto-encode, and it is the
        # only thing either of them agrees on.
        _key = str(self.dr.JOB.get("label") or "")
        if _key != self._disc_key:
            self._disc_key = _key
            self.disc_started = __import__("time").time()
        frac = info.get("frac")
        header = info.get("header") or ""
        label = info.get("label") or ""
        self._prog_head = header
        self._paint_prog_header()
        self._paint_total(info.get("run"))
        # A NEW STAGE IS ALSO A DISC MOVING AGAIN, and this is why the strip
        # read "1 failed, waiting for next item..." through an entire salvage.
        #
        # _between is set when a rip reports a failure, and was cleared only by
        # the BAR MOVING. On the title rip -> salvage path the title rip fails,
        # the salvage takes over on the same disc, and its bar sits at zero
        # while the drive grinds - so the one signal that could retract the
        # message is the one thing a grind never produces. Observed on HTTYD3:
        # the salvage ran for minutes under "1 failed, waiting for next item".
        #
        # The step is the honest signal. "reading the disc - step 2 of 6" means
        # work, whether or not a byte has landed yet.
        #
        # AND IT HAS TO BE ABOVE THE `frac is None` RETURN, which is the whole
        # of why the fix above did not take. REPORTED AGAIN 1 Sep on The Empire
        # Strikes Back, from a screenshot: "Auto-rip - 1 done, 1 failed -
        # waiting for next item..." with step 2 of 6 imaging the disc behind
        # it. salvage_step() announces a step with NO fraction - that is what
        # an indeterminate stage is - so every report the early phases of a
        # salvage make returned before reaching this, and the one branch that
        # could retract the message was unreachable from the one path that
        # needed it.
        _stage_now = (info.get("step"), info.get("step_name"),
                      info.get("label"))
        _stage_new = _stage_now != getattr(self, "_last_stage", None)
        self._last_stage = _stage_now
        if _stage_new:
            self._between = False   # a new stage is work, bytes or no bytes
            self._moved_at = __import__("time").time()
            if self._stalled:
                self._clear_stall()
        if frac is None:
            self.prog_bar.set(0)
            for w in (self.lbl_pct, self.lbl_bytes, self.lbl_left, self.lbl_eta):
                w.configure(text="")
            # ...AND THEN SAY WHAT IT IS DOING, which this used to return
            # without doing. REPORTED 1 Sep on Raiders of the Lost Ark: the
            # title rip failed, the salvage took over, and the strip kept
            # "Saving to MKV file - step 2 of 5" with the rip's own
            # "53.1%  18.1 / 35.0 GiB" and "35:45 left" beside it until the
            # sweep produced a fraction. The owner: "it just now updated to
            # the sweep. I think it needed progress again - this should never
            # be the case for visual updates."
            #
            # Everything below the old return is what draws the stage: the
            # Activity card's line, the window title, and _echo_progress,
            # which is the strip and the taskbar. An indeterminate stage - a
            # salvage phase, which is most of a salvage - reached none of it.
            # The numbers are cleared just above, so nothing stale is carried
            # over with the name.
            if self.busy:
                self.act_sub.configure(text=(label or "working")
                                       + step_tail(info))
                self.title(f"{self.dr.APP} - {self.action}")
            self._echo_progress(info)
            return
        # MOVEMENT, not merely a report of one. MakeMKV re-sends the same
        # percentage while the drive retries a damaged sector, and every one of
        # those was being counted as progress: it refreshed the "last progress"
        # clock and cleared the stall warning off the strip. Measured on a
        # Blu-ray with two bad blocks - the engine-side watchdog fired correctly
        # at 3:01 and again at 3:04, because IT tests whether the numbers
        # changed, while the window wiped its own warning and sat at 53.4% for
        # thirteen minutes looking healthy.
        key = (round(frac, 6), info.get("done"), info.get("total"))
        self._moved = key != self._last_key
        self._last_key = key
        if self._moved:
            self._between = False   # the bar moved: this disc is working
            self._moved_at = __import__("time").time()
            if self._stalled:
                self._clear_stall()
        if frac < 0.999 and self._bar_done:
            # moving again: the next disc, or a later stage of this one
            self._bar_done = False
            self.prog_bar.set_done(False)
        # THE PICTURE FIRST, for the same reason the strip takes it first: a
        # bar with a map is drawn from the map, and the fraction only decides
        # how far a plain fill reaches. An encode has no map and gets the fill.
        _cells = str(info.get("cells") or "")
        self.prog_bar.set_cells(_cells)
        # THE SAME DECISION AS THE STRIP'S, from the same function, so the two
        # bars cannot disagree about whether the head is worth marking.
        self.prog_bar.set_head(head_frac(info, _cells))
        self.prog_bar.set(max(0.0, min(1.0, frac)))
        self.lbl_pct.configure(text=f"{frac * 100:.2f}%")
        done, total = info.get("done"), info.get("total")
        if done is not None and total:
            self.lbl_bytes.configure(text=self.dr.size_pair(done, total))
            self.lbl_left.configure(
                text=f"{self.dr.human_size(max(0, total - done))} left"
                if total > done else "finishing")
        elif done:
            self.lbl_bytes.configure(text=f"{self.dr.human_size(done)} written")
            self.lbl_left.configure(text="")
        else:
            self.lbl_bytes.configure(text="")
            self.lbl_left.configure(text="")
        self.lbl_eta.configure(text=f"ETA {info.get('eta') or '--:--'}")
        if self.busy:
            # WHICH STEP OF HOW MANY, beside the stage. A percentage answers
            # "how far through this read", and on a damaged disc that is the
            # wrong question: the read is one of six things that have to
            # happen, and a bar sitting at 100% while the job carries on looks
            # like a job that has stopped.
            self.act_sub.configure(text=(label or "working")
                                   + step_tail(info))
            self.title(f"{self.dr.APP} - {frac * 100:.0f}%  {self.action}")
        self._echo_progress(info)

    def _sink_wait(self, reason, attention=False):
        """Called from the worker thread, so it only queues."""
        self.ui_q.put(("waiting", (str(reason), bool(attention))))

    def _wait_on_user(self, reason, attention=False):
        """The run has stopped on the person rather than on the drive.

        The bug this fixes: auto-rip refuses a disc it has just done, says so
        once, and then sits in a two-second poll waiting for it to be taken out.
        Nothing in the window changed. The strip still read "waiting for next
        item...", which was true and useless - there was a disc in the drive,
        and the one thing that would move the run on was somebody taking it out.
        So the reason is held up for as long as it is the reason: on the line at
        the bottom of the window, and on the Activity card.

        Only a wait the person is not expecting takes the strip's attention face
        and flashes the taskbar button. Swapping discs is what an unattended run
        is for; the strip keeps its "N done - waiting for next item..." line for
        that, which is what it was asked for."""
        reason = str(reason or "")
        attention = bool(reason and attention)
        had_attn = self._blocked_attn
        self._blocked = reason
        self._blocked_attn = attention
        if reason and self._stalled:
            # "no progress in 4 min" means nothing while nothing is meant to be
            # moving
            self._clear_stall()
        if self.taskbar is not None and not self._asking:
            if attention:
                # full and paused rather than left wherever the last disc got
                # to, which is over and done with
                self.taskbar.set(1.0)
                self.taskbar.state("waiting")
                self.taskbar.attention(True)
            elif had_attn:
                self.taskbar.attention(False)
                self.taskbar.state("working" if self.busy else "none")
        try:
            if reason:
                self.act_dot.configure(foreground=CLR["warn"])
                self.act_sub.configure(text=reason)
            elif self.busy:
                self.act_dot.configure(foreground=CLR["accent"])
        except tk.TclError:
            pass
        self._paint_status(force=True)
        self.repaint_mini()

    def _sink_shutdown(self, seconds):
        """Called from the worker thread, so it only queues."""
        self.ui_q.put(("shutdown", int(seconds)))

    def _shutdown_coming(self, seconds):
        """The machine is going off. Say so where the person is looking.

        This is the one message with a deadline on it: sixty seconds, and the
        only thing that stops it is a command in a terminal nobody watching a
        taskbar strip has open. So it takes the strip's attention face, counts
        down on it, and double-clicking the strip stops it - which is the
        gesture the strip already uses for "do the thing this is about"."""
        import time as _t
        self._off_at = _t.time() + max(1, int(seconds))
        self._paint_shutdown()

    def _paint_shutdown(self):
        if not self._off_at:
            return False
        import time as _t
        left = int(round(self._off_at - _t.time()))
        if left <= 0:
            self._off_at = 0.0
            return False
        if self.taskbar is not None:
            self.taskbar.set(1.0)
            self.taskbar.state("waiting")
            self.taskbar.attention(True)
        try:
            self.title(f"{self.dr.APP} - shutting down in {left} s")
        except tk.TclError:
            pass
        if self.mini is None or not self.mini.winfo_exists():
            return False
        summary = self._run_summary()
        return self.mini.attention(
            (f"{self.action or 'Run'}: {summary}" if summary
             else (self.action or self.dr.APP)), "",
            f"Shutting down in {left} s - double-click to stop")

    def cancel_shutdown(self):
        """shutdown /a, which is the only thing that stops it."""
        if not self._off_at:
            return False
        self._off_at = 0.0
        self.dr.run(["shutdown", "/a"], mode="capture", ignore_rc=True)
        self._log_line("  [OK] Shutdown cancelled.")
        if self.taskbar is not None:
            self.taskbar.attention(False)
            self.taskbar.state("none")
        try:
            self.title(self.dr.APP)
        except tk.TclError:
            pass
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.unstall()
        self._idle_line()
        return True

    def _engine_event(self, event, message=""):
        """The engine's own milestones, as they happen.

        Taken from the same call that decides whether to play a sound, but
        ahead of it and regardless of the answer: this draws rather than makes a
        noise. It has to come from here rather than from the worker finishing,
        because an auto-rip is one worker and a whole stack of finished discs -
        which is why the strip used to sit at 100% saying nothing while the
        chime played."""
        if event == "rip_finished":
            self._mark_rip_done(True)
        elif event == "disc_done":
            self._mark_disc_done()
        elif event == "bg_done":
            self._mark_bg_done(message)
        elif event == "failure":
            self._mark_rip_done(False)
        elif event == "stall":
            self._mark_stalled()
        elif event == "drive_lost":
            self._mark_drive_wedged()
        elif event == "drive_back":
            # NOT `self._wedged = False` FIRST, which is what this said and
            # is why a wedge outlived its own recovery. _clear_stall reads the
            # flag to decide whether to put the red activity card, the window
            # title and the taskbar button BACK - so clearing it here made
            # that restore unreachable from the one event that always precedes
            # it, and every later caller was gated on _stalled, which
            # _clear_stall had just turned off as well.
            #
            # OBSERVED 7 Sep on Harry Potter 6: the drive wedged at 21:13:34
            # and came back at 21:14:30; eight minutes later the card still
            # read "Drive wedged" in red over a film that was muxing.
            self._clear_stall()
        elif event == "disc_detected":
            # ...AND DURING A RUN TOO, which is where it matters most. Between
            # discs of an auto-rip the strip said "waiting for next item" and
            # went on saying it after the disc was in, through the scan and the
            # key capture - the exact stretch where somebody is standing there
            # wondering whether the machine noticed. Asked for 1 Sep.
            _nm = str(message or "").split(":")[-1].strip()
            # NAMED, AND SAID TO BE AN EVENT. The name on its own reads as a
            # title sitting on the strip, which is what it looks like for the
            # whole of the next rip anyway - the point of this line is that
            # something just HAPPENED.
            self._detected = f"Disc detected: {_nm}" if _nm else "Disc detected"
            if not self.busy:
                # nothing is running, so the strip has nothing better to say
                self._idle_line(self._detected)
            else:
                self.repaint_mini()

    def _mark_drive_wedged(self):
        """The engine says the drive has stopped serving data.

        Kept separate from _stalled so the readout says what to DO. A stall
        means the drive is working; this means it is not there, and the only
        thing that brings it back is a hand on the enclosure.

        BOTH SURFACES, asked for 1 Sep. The strip goes wholly red - see
        MiniMonitor.drive_wedged - and the window says the same sentence in the
        same words, because the last wedge sat for five and a half hours with
        the window showing "no progress detected in N min", which is what it
        also says when the drive is grinding through a scratch and nothing is
        wrong. The window is the surface somebody has open; the strip is the
        one they can see from across the room."""
        self._wedged = True
        self._stalled = True
        if not self._moved_at:
            self._moved_at = __import__("time").time()
        msg = self.dr.DRIVE_WEDGED_MSG
        if self.taskbar is not None:
            # ERROR, not "waiting". The shell paints waiting the same amber it
            # uses for a paused download; this is the state where the run is
            # over until somebody acts.
            self.taskbar.set(1.0)
            self.taskbar.state("error")
            self.taskbar.attention(True)
        try:
            self.title(f"{self.dr.APP} - drive wedged")
            self.act_dot.configure(foreground=CLR["err"])
            self.act_title.configure(text="Drive wedged",
                                     foreground=CLR["err"])
            self.act_sub.configure(text=msg)
        except tk.TclError:
            pass
        self._paint_status(force=True)
        self._paint_stall()

    def _mark_stalled(self):
        """The watchdog says the rip has stopped advancing.

        How long for is not taken from the message - that is a sentence written
        for a person - but from when the last progress line arrived here, which
        this window already knows and which keeps counting up on the clock tick
        without the engine having to say anything more."""
        self._stalled = True
        if not self._moved_at:
            self._moved_at = __import__("time").time()
        # AND THE TASKBAR BUTTON, from here rather than from a warning line.
        # Its one writer used to be _log_line, gated on the word "progress"
        # appearing in the sentence - and only one of the engine's four stall
        # warnings contains that word, on purpose. This runs for all four,
        # because EVENT_SINK("stall") fires for all four.
        if self.busy and self.taskbar is not None:
            self.taskbar.state("stalled")
        self._paint_stall()

    def _paint_stall(self):
        """Put the stalled readout on the strip, with the minutes as they are."""
        if not (self._stalled and self.busy):
            return False
        if self.mini is None or not self.mini.winfo_exists():
            return False
        import time as _t
        mins = max(1, int((_t.time() - (self._moved_at or _t.time())) // 60))
        disc = str(self.dr.JOB.get("label") or "")
        stage = str((self._last_stats or {}).get("label") or self.action or "")
        if getattr(self, "_wedged", False):
            return self.mini.drive_wedged(disc or stage,
                                          stage if disc else "", mins)
        return self.mini.reading_hard(disc or stage, stage if disc else "",
                                      mins)

    def _clear_stall(self):
        was_wedged = self._wedged
        self._stalled = False
        self._wedged = False
        if was_wedged:
            # PUT THE WINDOW BACK TOO. _mark_drive_wedged writes over the
            # activity card, the window title and the taskbar button, and none
            # of those is rewritten by anything until the next progress tick -
            # so a drive that came back left a red "Drive wedged" sitting on a
            # rip that was running again.
            try:
                self.act_dot.configure(
                    foreground=CLR["accent"] if self.busy else CLR["faint"])
                self.act_title.configure(text=self.action or "Idle",
                                         foreground=CLR["text"])
                self.act_sub.configure(text="Carrying on where it stopped."
                                       if self.busy else "Nothing is running.")
                self.title(f"{self.dr.APP}"
                           + (f" - {self.action}" if self.action else ""))
            except tk.TclError:
                pass
            if self.taskbar is not None:
                self.taskbar.attention(False)
        # AND THE TASKBAR BUTTON BACK. _mark_stalled turns it amber, and
        # nothing here used to turn it back - so the button stayed amber for
        # the rest of the run after one rough patch. (It was _log_line that
        # set it then, on any warning line containing the word "progress";
        # the amber now comes from the typed stall event instead, which is
        # both more of them and only them.) Guarded on _had_error: a run that
        # has already failed something should keep saying so.
        if self.taskbar is not None and not self._had_error:
            self.taskbar.state("normal")
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.unstall()

    def _doing_line(self):
        """What the engine says it is doing, or "starting..." if it has not.

        ASKED FOR 1 Sep. A rip's first minute has no progress to report and
        the strip said "starting..." through all of it - the LibreDrive check,
        the speed request, the twenty seconds of key capture, the scan. Every
        one of those already printed a line to the log; none reached the one
        surface somebody watching a taskbar can see. See doing() in the engine.
        """
        try:
            return str(self.dr.JOB.get("doing") or "").strip() or "starting..."
        except Exception:                                        # noqa: BLE001
            return "starting..."

    def _run_summary(self):
        """How many discs this run has finished, and how many it lost."""
        done, failed = self._ran["done"], self._ran["failed"]
        bits = [f"{done} done"] if done else []
        if failed:
            bits.append(f"{failed} failed")
        return ", ".join(bits)

    def _idle_line(self, text=None, failed=None):
        """What the strip says when there is no bar to draw.

        This is the only line the person watching a docked strip has when
        nothing is transferring, so it carries the state of the run rather than
        the word "idle" - what the stack did, and what is about to happen to the
        machine. The six-field rule is about a rip in progress; with no bar
        there are no fields to keep to."""
        if self.mini is None or not self.mini.winfo_exists():
            return False
        if text is None:
            # A tally is worth saying about a stack and not about one disc: one
            # that worked needs no counting, and one that failed says so on its
            # own. Mid-run is the other way round - see the waiting line, where
            # "1 done" is the whole of what there is to know so far.
            ran = self._ran["done"] + self._ran["failed"]
            summary = self._run_summary() if (ran > 1 or self._ran["failed"])                 else ""
            named = self.action or self._last_action or "Last run"
            if self.busy and self._between:
                # A RUN IS STILL GOING. "No rip in progress" is what this said
                # between discs, which is the opposite of true - the run is
                # mid-stack and the only thing it is short of is the next disc.
                # The tally goes in from the FIRST disc here, because "1 done"
                # is the whole of what there is to know at that point.
                text = ("Waiting for the next disc"
                        + (f" - {self._run_summary()}"
                           if (ran or self._ran["failed"]) else ""))
            else:
                text = (f"{named}: {summary}" if summary
                        else ("Last rip failed" if self._had_error
                              else "No rip in progress"))
        self.mini.idle(text,
                       failed=self._had_error if failed is None else failed,
                       background=self._bg_idle_line())
        return True

    def _mark_disc_done(self):
        """The drive is free and the film is not finished.

        OBSERVED on A New Hope: the strip read "Saving to MKV file - finishing
        the file - step 2 of 5" for the whole of a background encode, because
        the only thing that ever moved it off a rip was "rip_finished" - and
        with a deferred tail that does not arrive until the ENCODE ends, which
        is minutes later and is a different event.

        Deliberately NOT _mark_rip_done: no green flash, no chime, and no tally.
        Those belong to the film being finished, which this is not. All that has
        happened is that the disc can come out, which is exactly what the strip
        should now be saying.
        """
        self._between = True
        self._bar_done = False
        # The stale readout goes with it. _status_text short-circuits on
        # _between, but the strip keeps whatever was last painted on it until
        # something repaints - which is the bug itself.
        self._last_stats = None
        self._last_key = None
        if self.taskbar is not None:
            # Not "complete" - the run is not. Just not stalled or failed any
            # more, whatever the last disc did.
            self.taskbar.state("normal")
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.set_done(False)
            self.mini.unstall()
        self._idle_line()
        self._paint_status(force=True)
        return True

    def _mark_rip_done(self, okay):
        """One disc has finished. Green and full if it worked, and a blink
        either way."""
        self._bar_done = okay
        try:
            self.prog_bar.set_done(okay)
            if okay:
                # AND THE PICTURE GOES, so a full green bar means what it
                # says. A disc that finished with damage keeps its coloured
                # map until the next tick otherwise, which reads as a finished
                # rip that is also still broken.
                self.prog_bar.set_cells("")
                self.prog_bar.set(1.0)
        except tk.TclError:
            pass
        self._ran["done" if okay else "failed"] += 1
        if not okay:
            self._had_error = True
        if self._auto:
            # One disc down and the next one not in the drive yet. The strip
            # stands at a finished bar otherwise, which reads as a rip in
            # progress that has stopped moving rather than as a wait.
            self._between = True
        if self.taskbar is not None:
            self.taskbar.state("error" if not okay else "normal")
            if okay:
                self.taskbar.set(1.0)
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.set_done(okay)
            if not self.mini.flash(failed=not okay):
                self.repaint_mini()     # no blink to wait for the end of

    def _mark_bg_done(self, message=""):
        """A BACKGROUND job finished. Flash and chime, and touch nothing else.

        Everything _mark_rip_done does is a statement about the disc in the
        drive: the bar goes to 100% and green, the taskbar button fills, the
        run tally goes up, and on an auto-rip `_between` starts the strip
        saying it is waiting for the next disc. A finished encode makes none
        of those true - and until 2 Sep it did all of them, because the
        background tail fired the same "rip_finished" event a finished disc
        does. Reported: a parallel rip's bar jumped to 100% and the strip said
        "waiting for the next item" until the rip it was still running made
        its next tick.

        What IS true is that something finished, so the flash and the sound
        stay - they are the whole point of announcing it - and the message
        takes the strip for exactly as long as they last.
        """
        self._bg_done_at = __import__("time").time()
        if self.mini is not None and self.mini.winfo_exists():
            # THE WHOLE STRIP, not the parenthetical. Somebody watching a
            # docked strip has no other way to learn the film exists, and
            # "DONE - <film> [AV1].mkv was created" in a footnote field
            # competing with a live rip is not an announcement.
            note, colour = self._bg_note()
            if note:
                self.mini.banner(note, failed=(colour == CLR["err"]))
            self.mini.flash(failed=(colour == CLR["err"]))
        self._refresh_bg()

    def _echo_progress(self, info):
        """Repeat the progress on the taskbar button and the pinned strip.

        Both are fed from the one dict the rip already sends, so neither can
        drift from what the window itself is showing."""
        frac = info.get("frac")
        if self._asking:
            # The worker is blocked on the answer, so anything arriving now is
            # a line that was already in the queue when the question went up.
            # Drawing it would put the readouts back to saying a rip is in
            # progress, which is the thing being fixed.
            return
        run = info.get("run")
        if self.taskbar is not None:
            # On a batch the button follows the whole pile rather than filling
            # and emptying once per film. Forty round trips tell somebody
            # glancing at it nothing they wanted to know.
            self.taskbar.set(run["frac"] if run and run.get("total") else frac)
        if self._collapsed:
            self._update_tray_tip()
        if self.mini is None or not self.mini.winfo_exists():
            return
        if self._stalled and not self._moved:
            # Still stuck. Keep the stalled readout up and let its clock run;
            # show() clears the warning as its first act, so drawing a repeated
            # percentage through it is what made the warning disappear while
            # nothing was happening.
            self._paint_stall()
            return
        # THE WHOLE PLAN, for the shape that has room to draw it. Handed over
        # here rather than as another argument to show(): it changes about six
        # times in a rip, where show() is called every tick, and set_stages
        # returns at once when nothing has changed.
        self.mini.set_stages(info.get("step_names"), info.get("step_name"))
        # THE DRIVE IS FREE, AND THAT IS WORTH THE TOP ROW. See
        # MiniMonitor.set_drive_free; DRIVE_FREE is the engine's own state and
        # clear_cancel takes it down at every run boundary.
        try:
            _fs = self.dr.DRIVE_FREE
            if not _fs.get("free"):
                self._free_seen = 0.0
                self.mini.set_drive_free(False)
            else:
                if self._free_seen != _fs.get("at"):
                    # A NEW SEAM IS A NEW BANNER, even if the last one was
                    # dismissed - a second disc's "you can take it out" is a
                    # different fact from the first's.
                    self._free_seen = _fs.get("at")
                    self.mini._free_dismissed = False
                self.mini.set_drive_free(
                    not getattr(self.mini, "_free_dismissed", False),
                    str(_fs.get("label") or ""))
        except (AttributeError, tk.TclError):
            pass
        # the job header is written for a wide window; the strip gets the disc
        # and the stage, which is what it has room for
        disc = str(self.dr.JOB.get("label") or "")
        # THE VERB AND WHERE WE ARE IN THE PLAN. `label` is the phase
        # ("sweep") and step_tail is "2/6". The phase's own status note used
        # to sit between them - "nominal", "rough patch", "asked
        # 2,340/14,112 sectors, 118 back" - and it is gone: see step_tail for
        # what the room is spent on instead. The note still reaches the strip
        # through `info` when it says something a person can act on.
        stage = " · ".join(
            x for x in (progressive(info.get("label") or self.action
                                    or "working"),
                        step_tail(info).lstrip(" ·-"))
            if x)
        done, total = info.get("done"), info.get("total")
        if self.mini.docked:
            # Docked the strip shows six things and nothing else, so it is given
            # exactly those six and none of the joining-up the wide layouts do:
            # the disc and the stage as separate fields, the bar, and then the
            # percentage, the byte counts and the time remaining, each on its
            # own so the strip can drop them one at a time as it narrows.
            sizes = self.dr.size_pair(done, total) \
                if done is not None and total else ""
            eta = str(info.get("eta") or "")
            if total and done is not None and total <= done:
                eta = "finishing"
            elif eta and eta != "--:--" and not eta.endswith(" left"):
                eta = f"{eta} left"
            # HOW LONG AND HOW FAST, at the widths that have room for them.
            # _extra_bits already builds exactly this for the floating strip;
            # the drive letter is dropped because docked it is already at the
            # end of the disc line.
            # ELAPSED AND RATE AS THEIR OWN FIELDS, docked. They used to be
            # joined onto `eta` with dots, which put three figures in one
            # right-aligned label - so the strip could not stack them and the
            # whole group was dropped the moment the row ran out of width.
            # _extra_bits builds the same three for the floating strip; the
            # drive letter is dropped here because docked it is already at the
            # end of the disc line.
            _elapsed, _rate = "", ""
            _more = [x for x in self._extra_bits(info).split("  ·  ")
                     if x and not x.endswith(":")]
            for _x in _more:
                if _x.endswith("/s"):
                    _rate = _x
                elif ":" in _x and not _elapsed:
                    _elapsed = _x
            # IS THERE A SECOND PIECE OF WORK, which is all `bg_note` is
            # asked. It used to ride in the sixth argument, `info`, on the
            # grounds that the docked strip shows that field at every size and
            # it is empty most of the time - and it was a sentence there,
            # measured last and cut first. The strip draws a button for it
            # now; see MiniMonitor.set_bg_button, and view_btn for the
            # screenshot of the sentence cut off before the film's name.
            _bgn, _bgc = self._bg_note()
            _cells = str(info.get("cells") or "")
            self.mini.show(disc or stage, frac,
                           ("working" if frac is None
                            else f"{frac * 100:.2f}%"),
                           sizes, eta, "",
                           stage if disc else "",
                           total=run["frac"] if run and run.get("total")
                           else None,
                           state=str(info.get("state") or ""),
                           bg_note=_bgn if self._bg_pending() else "",
                           cells=_cells,
                           head=head_frac(info, _cells),
                           elapsed=_elapsed, rate=_rate)
            return
        title = f"{disc} · {stage}" if disc else str(stage)
        bits = []
        # THE NOTE, WHICH THE FLOATING STRIP NEVER SHOWED. `note` is the one
        # field that says WHY - "asked 2,340 of 14,112 sectors, 118 back",
        # "skipped 32 MB at 11.03 GB" - and the docked branch above puts it
        # in the stage. Floating, the sixth argument is already spoken for by
        # _disc_bits, so it goes at the head of the detail line where it is
        # read first. Detached is the BIGGER strip; it had less to say.
        _rn = str(info.get("note") or "")
        if _rn:
            bits.append(_rn)
        if done is not None and total:
            bits.append(self.dr.size_pair(done, total))
            if total <= done:
                bits.append("finishing")
            else:
                bits.append(f"{self.dr.human_size(total - done)} left")
        elif done:
            bits.append(f"{self.dr.human_size(done)} written")
        if info.get("eta"):
            bits.append(str(info["eta"]))
        if run and run.get("total"):
            bits.append(f"batch {run['frac'] * 100:.0f}%")
        pct = "" if frac is None else f"{frac * 100:.2f}%"
        # NOT ON THE DETAIL LINE ANY MORE. It used to be the last of the
        # joined bits - a sentence about a different film on the end of this
        # film's byte counts - and detached it now has either a bar of its own
        # further down the strip or a button to ask for one. See
        # MiniMonitor.set_bg_button and set_bg_block.
        _bgn2, _bgc2 = self._bg_note()
        # `state` was missing here, so the read state - waiting, struggling,
        # skipped - tinted the DOCKED strip's bar and never the floating one.
        # Found by a smoke run built to check the amber came back, which it
        # could not do on a bar that had never gone amber.
        self.mini.show(title, frac, pct, "  ·  ".join(bits),
                       self._extra_bits(info), self._disc_bits(),
                       total=run["frac"] if run and run.get("total") else None,
                       state=str(info.get("state") or ""),
                       bg_note=_bgn2 if self._bg_pending() else "",
                       cells=str(info.get("cells") or ""),
                       head=head_frac(info, info.get("cells")))

    def _disc_bits(self):
        """What the disc is, and where it is going.

        A line the floating strip gets at its larger sizes. The docked one never
        does: it shows six things and this is not one of them."""
        dr, job = self.dr, self.dr.JOB
        kind = str(job.get("type") or "")
        bits = [{"dvd": "DVD-Video", "bluray": "Blu-ray", "audio": "Audio CD",
                 "data": "Data disc", "mixed": "Mixed-mode CD"}.get(kind, kind)]
        if job.get("size"):
            bits.append(dr.human_size(job["size"]))
        if job.get("total") and int(job.get("total") or 0) > 1:
            bits.append(f"disc {job.get('number')} of {job['total']}")
        if job.get("source"):
            bits.append(f"from {job['source']}")
        try:
            bits.append(f"-> {dr.out_dir_for(self.settings, kind or 'dvd')}")
        except Exception:
            pass
        return "   ".join(b for b in bits if b)

    def _extra_bits(self, info):
        """The line only a strip with room to spare gets: how long it has been
        going, how fast, and which drive it is reading.

        Built whatever the size, because it is three string joins and the strip
        is the one that knows whether it has anywhere to put them."""
        import time as _t
        bits = []
        _since = self.disc_started or self.action_started
        elapsed = _t.time() - _since if _since else 0
        if elapsed >= 1:
            bits.append(self.dr.human_time(elapsed))
        done = info.get("done")
        if done and elapsed >= 3:
            bits.append(f"{self.dr.human_size(done / elapsed)}/s")
        source = str(self.dr.JOB.get("source") or "")
        if source:
            bits.append(source)
        return "  ·  ".join(bits)

    # -- modal helpers ----------------------------------------------------
    def _modal(self, title):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(background=CLR["card"])
        win.transient(self)
        win.resizable(False, False)
        win.grab_set()
        self.update_idletasks()
        win.geometry(f"+{self.winfo_rootx() + 140}+{self.winfo_rooty() + 140}")
        return win

    def _show_prompt(self, kind, prompt, default, choices, box, done):
        self._begin_asking(prompt)
        win = self._modal(self.dr.APP)
        self._asking_win = win
        ttk.Label(win, text=prompt, wraplength=int(460 * self.sc),
                  justify="left", style="Card.TLabel").pack(
            padx=18, pady=(18, 12), anchor="w")

        def finish(val):
            box["answer"] = val
            self._end_asking()
            try:
                win.destroy()
            finally:
                done.set()

        if kind == "yesno":
            row = ttk.Frame(win, style="Card.TFrame")
            row.pack(padx=18, pady=(0, 18), anchor="e")
            ttk.Button(row, text="Yes",
                       command=lambda: finish(True)).pack(side="left", padx=4)
            ttk.Button(row, text="No", command=lambda: finish(False)).pack(
                side="left")
            win.bind("<Return>", lambda e: finish(bool(default)))
        elif kind == "choice":
            var = tk.StringVar(value=str(default))
            for key, label in (choices or []):
                ttk.Radiobutton(win, text=label, value=key, variable=var,
                                style="Card.TRadiobutton").pack(
                    anchor="w", padx=24, pady=2)
            ttk.Button(win, text="OK",
                       command=lambda: finish(var.get())).pack(pady=16)
        else:
            var = tk.StringVar(value="" if default is None else str(default))
            e = ttk.Entry(win, textvariable=var, width=44)
            e.pack(padx=18)
            e.focus_set()
            e.bind("<Return>", lambda ev: finish(var.get()))
            ttk.Button(win, text="OK",
                       command=lambda: finish(var.get())).pack(pady=16)
        win.protocol("WM_DELETE_WINDOW", lambda: finish(default))

    def _begin_asking(self, prompt):
        """The rip has stopped and wants an answer. Say so everywhere.

        The bug this fixes: the scan drives the progress bar up to whatever it
        reaches, the prompt opens, and nothing tells any of the readouts. The
        taskbar button sat at a blue 90%, the strip at 89.9% "Decrypting data",
        and the title bar at "90% Ripping disc" - all three saying a rip was in
        progress while the app was doing nothing at all except waiting to be
        answered."""
        self._asking = str(prompt or "")
        self._paint_status(force=True)
        if self.taskbar is not None:
            # full and paused rather than left at the fraction the last stage
            # happened to reach, which means nothing now, and flashing, which is
            # the shell's own way of saying a window wants somebody
            self.taskbar.set(1.0)
            self.taskbar.state("waiting")
            self.taskbar.attention(True)
        try:
            self.title(f"{self.dr.APP} - waiting for you")
            self.act_sub.configure(text=self._asking)
            self.act_dot.configure(foreground=CLR["warn"])
        except tk.TclError:
            pass
        self._paint_asking()

    def _paint_asking(self):
        """The one thing the run is stopped on, if it is stopped on anything.

        A dialog outranks the tray: the tray can be dealt with whenever somebody
        walks past it, and a modal window is in the way of everything until it
        has been answered."""
        if not (self._asking or self._blocked_attn):
            return False
        if self.mini is None or not self.mini.winfo_exists():
            return False
        disc = str(self.dr.JOB.get("label") or "")
        stage = str((self._last_stats or {}).get("label") or self.action or "")
        if self._asking:
            return self.mini.waiting(disc or stage, stage if disc else "",
                                     self._asking)
        # Straight onto the face with no "Waiting for you:" in front of it: the
        # reason is already an instruction that starts with its verb, and on a
        # docked strip that prefix would cost a third of the room to say what
        # "Take the disc out" says on its own.
        return self.mini.attention(disc or stage, stage if disc else "",
                                   self._blocked)

    def _end_asking(self):
        """Answered. Back to whatever the rip is actually doing."""
        if not self._asking:
            return
        self._asking = ""
        self._asking_win = None
        self._paint_status(force=True)
        # The scan's percentage is not the rip's, and it is the only one on
        # hand. Dropped rather than repainted, or answering the question puts
        # the button straight back to a blue nine-tenths for as long as it takes
        # the next stage to report anything - which is the same lie again, just
        # a shorter one.
        self._last_stats = None
        if self.taskbar is not None:
            self.taskbar.attention(False)
            self.taskbar.state("working" if self.busy else "none")
        try:
            self.act_dot.configure(foreground=CLR["accent"])
            if self.busy:
                self.title(f"{self.dr.APP} - {self.action}")
        except tk.TclError:
            pass
        if self.mini is not None and self.mini.winfo_exists():
            self.mini.unstall()
        self.repaint_mini()

    def _status(self, text, hold=5.0):
        """Say something in the bar at the bottom, and keep it there a moment.

        These are answers to something the person just did - a drive scan, a
        saved setting - so they hold the bar for a few seconds against the
        running state, which would otherwise paint over them on the next tick."""
        self._status_hold = __import__("time").time() + max(0.0, hold)
        try:
            self.status.configure(text=text)
        except tk.TclError:
            pass

    def _status_text(self):
        """What the bar says when nothing has been typed at recently.

        It used to say "Ready", or whatever the last drive scan found, for the
        whole of a rip - the one part of the window that never changed while
        everything was happening. It carries the state the strip carries,
        because somebody with the window open is looking at the window."""
        import time as _t
        if self._off_at:
            left = max(0, int(round(self._off_at - _t.time())))
            tail = (" - double-click the strip to stop it"
                    if self.mini is not None and self.mini.winfo_exists()
                    else "")
            return f"Shutting down in {left} s{tail}"
        if self._asking:
            return "Waiting for you: " + self._asking
        if self._blocked:
            return self._blocked
        _bg, _c = self._bg_note()
        _tail = f" {_bg}" if _bg else ""
        if not self.busy:
            summary = self._run_summary()
            if summary:
                return f"{self._last_action or 'Last run'}: {summary}{_tail}"
            if _bg:
                # NOT "Ready" while a film is still being made. This is the
                # case Pirates of the Caribbean landed in.
                return f"Waiting for the next item{_tail}"
            return "Last rip failed" if self._had_error else "Ready"
        head = self.action or "Working"
        disc = str(self.dr.JOB.get("label") or "")
        if disc:
            head += f" - {disc}"
        if self._wedged:
            # NOT "no progress detected", which is also what a rough patch
            # says. This is the one state where waiting is the wrong response.
            return f"{head} - {self.dr.DRIVE_WEDGED_MSG}"
        if self._stalled:
            mins = max(1, int((_t.time() - (self._moved_at or _t.time())) // 60))
            return f"{head} - no progress detected in {mins} min" + _tail
        if self._between:
            summary = self._run_summary()
            return (f"{head} - " + (f"{summary} - " if summary else "")
                    + "waiting for the next item" + _tail)
        st = self._last_stats or {}
        if st.get("frac") is not None:
            bits = [f"{st['frac'] * 100:.0f}%"]
            if st.get("label"):
                # WITH THE STEP. The full strip has said "step 2 of 4" since
                # StagePlan was wired up, and the compact line - which is what
                # the taskbar and a docked strip actually show - never did. On
                # a damaged disc the step is the number that says how much is
                # left; the percentage is about one read.
                bits.append(str(st["label"]) + step_tail(st))
            if st.get("eta"):
                bits.append(f"ETA {st['eta']}")
            return head + " - " + "  ".join(bits) + _tail
        return head + " - starting..." + _tail

    def _paint_status(self, force=False):
        if not force and self._status_hold > __import__("time").time():
            return
        text = self._status_text()
        if not text:
            return
        self._status_hold = 0.0
        try:
            if self.status.cget("text") != text:
                self.status.configure(text=text)
        except tk.TclError:
            pass

    # -- shutdown ---------------------------------------------------------
    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    self.dr.APP,
                    f"'{self.action}' is still running.\n\nStop it and close?"):
                return
            self.dr.request_cancel()
        # ASKED SEPARATELY, because the answer is different. A background
        # encode is not "the run": the disc it came from is out of the
        # drive and the lossless it is replacing has been deleted or is
        # about to be, so closing on it loses a film that was minutes
        # from finished. The worker is not a daemon thread either, so the
        # process would sit there and finish anyway - with the window
        # gone and nothing on screen saying why it had not exited.
        _bg = self.dr.BG.pending()
        if _bg:
            _run = self.dr.BG.running() or "an encode"
            if not messagebox.askyesno(
                    self.dr.APP,
                    f"{_bg} background job(s) are still finishing "
                    f"({_run})." + chr(10) * 2 + "These are films whose "
                    f"disc is already out of the drive. Closing now "
                    f"abandons them - the window goes but the work "
                    f"carries on with nothing to show it, and a job that "
                    f"is interrupted loses that film." + chr(10) * 2
                    + "Close anyway?",
                    default="no", icon="warning"):
                return
            self.dr.BG.forget_queued()
        self._closing = True
        for done in list(self.pending_prompts):
            done.set()          # never leave a worker blocked on a dead dialog
        self.dr.OUTPUT_SINK = None
        self.dr.PROGRESS_SINK = None
        self.dr.PROMPT_SINK = None
        self.dr.EVENT_SINK = None
        self.dr.SHUTDOWN_SINK = None
        self.dr.WAIT_SINK = None
        # A PowerShell holding a speech engine would otherwise outlive the
        # window that started it, still working through whatever was queued.
        self.dr.stop_speaking()
        # clear the taskbar button and take the tray icon down before the window
        # goes: neither can be asked to tidy up after an HWND that has gone, and
        # an abandoned tray icon sits there as a ghost until something pokes it
        if self.taskbar is not None:
            self.taskbar.close()
            self.taskbar = None
        if self.tray is not None:
            self.tray.close()       # also puts Tk's own window procedure back
            self.tray = None
        # THE ANIMATORS' CLOCKS, BEFORE THE WIDGETS THEY DRAW ON GO. A
        # pending `after` whose command is destroyed makes Tk write
        # `invalid command name "..._tick"` to stderr - which on the way out
        # of an app reads exactly like the crash somebody was worried about.
        for _a in (getattr(self, "_bar_anim", None),
                   getattr(getattr(self, "mini", None), "anim", None)):
            if _a is not None:
                _a.stop()
        if self.mini is not None:
            self.mini._unhook()     # before Tk takes the window they fire at
        self.mini = None            # destroyed with everything else below
        self.destroy()


def run(dr, settings):
    enable_dpi_awareness()
    claim_app_identity()        # before the first window, or it does not take
    App(dr, settings).mainloop()
    return 0
