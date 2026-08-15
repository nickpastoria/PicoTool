"""Reloading a running PICO-8 in place instead of restarting the process.

PICO-8 re-reads a cartridge that changed on disk when it is sent CTRL-R (CMD-R
on macOS) - see "Using an External Text Editor" in the manual. Driving that
hotkey from the outside keeps the same window alive, so it stays exactly where
the user put it: position, size, fullscreen state and all.

We press ESCAPE first. CTRL-R is "reload / run / restart", and while a cart is
running it takes the restart branch: the cart starts over from the copy already
in memory and the new file on disk is never read. Dropping to the console first
makes it take the reload branch instead.

The keystrokes have to land in the PICO-8 window, so every backend focuses it,
types, and then hands focus back to whatever had it before. Each one checks the
focus actually moved first - typing ESCAPE and CTRL-R into the editor the user
is working in would be worse than not reloading at all.
"""

import shutil
import subprocess
import sys
import time

# Long enough for the window manager to hand focus over before the keystrokes
# land, and for PICO-8 to read them before focus goes back.
SETTLE = 0.06
# PICO-8 has to finish leaving the cart before the reload key means "reload".
BETWEEN_KEYS = 0.12


class HotkeyError(Exception):
    """The reload keystroke could not be delivered."""


def send_reload(pid):
    """Make the PICO-8 process `pid` reload its cartridge, in place."""
    if sys.platform == "win32":
        return _windows(pid)
    if sys.platform == "darwin":
        return _macos(pid)
    return _x11(pid)


# ------------------------------------------------------------------ windows

VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_R = 0x52
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0


def _windows(pid):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    hwnd = _win_window_for(user32, pid)
    if not hwnd:
        raise HotkeyError("PICO-8 has no visible window")

    previous = user32.GetForegroundWindow()
    if not _win_focus(user32, kernel32, hwnd):
        raise HotkeyError("could not focus the PICO-8 window")
    time.sleep(SETTLE)

    _win_type(user32, [VK_ESCAPE])
    time.sleep(BETWEEN_KEYS)
    _win_type(user32, [VK_CONTROL, VK_R])
    time.sleep(SETTLE)

    if previous and previous != hwnd:
        _win_focus(user32, kernel32, previous)


def _win_type(user32, keys):
    """Press `keys` together, then release them in reverse."""
    for flags, order in ((0, keys), (KEYEVENTF_KEYUP, list(reversed(keys)))):
        for vk in order:
            user32.keybd_event(vk, user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC), flags, 0)


def _win_window_for(user32, pid):
    """The first visible top-level window owned by `pid`."""
    import ctypes
    from ctypes import wintypes

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    match = []

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            match.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return match[0] if match else None


def _win_focus(user32, kernel32, hwnd):
    """Focus `hwnd`, borrowing input state so SetForegroundWindow is allowed."""
    ours = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    threads = set()
    for target in (hwnd, foreground):
        if target:
            thread = user32.GetWindowThreadProcessId(target, None)
            if thread and thread != ours:
                threads.add(thread)

    attached = [t for t in threads if user32.AttachThreadInput(ours, t, True)]
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(ours, thread, False)
    return user32.GetForegroundWindow() == hwnd


# -------------------------------------------------------------------- macos

MACOS_SCRIPT = """
tell application "System Events"
    set targets to (every process whose unix id is %d)
    if targets is {} then error "no PICO-8 process"
    set previous to name of (first process whose frontmost is true)
    set frontmost of item 1 of targets to true
    delay %.2f
    key code 53
    delay %.2f
    keystroke "r" using command down
    delay %.2f
    set frontmost of (first process whose name is previous) to true
end tell
"""


def _macos(pid):
    script = MACOS_SCRIPT % (pid, SETTLE, BETWEEN_KEYS, SETTLE)
    done = subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True)
    if done.returncode:
        detail = (done.stderr or "").strip().splitlines()
        raise HotkeyError(detail[-1] if detail else "osascript failed "
                          "(Terminal may need Accessibility permission)")


# ---------------------------------------------------------------------- x11

def _x11(pid):
    if not shutil.which("xdotool"):
        raise HotkeyError("xdotool is not installed")

    windows = _xdotool("search", "--pid", str(pid)).split()
    if not windows:
        raise HotkeyError("no X window for PICO-8")

    previous = _xdotool("getactivewindow", allow_fail=True).strip()
    _xdotool("windowactivate", "--sync", windows[-1],
             "key", "--clearmodifiers", "Escape",
             "sleep", "%.2f" % BETWEEN_KEYS,
             "key", "--clearmodifiers", "ctrl+r")
    if previous:
        _xdotool("windowactivate", previous, allow_fail=True)


def _xdotool(*args, **kwargs):
    allow_fail = kwargs.pop("allow_fail", False)
    done = subprocess.run(["xdotool"] + list(args), capture_output=True, text=True)
    if done.returncode and not allow_fail:
        detail = (done.stderr or "").strip().splitlines()
        raise HotkeyError(detail[-1] if detail else "xdotool %s failed" % args[0])
    return done.stdout if not done.returncode else ""
