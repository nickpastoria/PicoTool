"""Finding and launching the PICO-8 executable."""

import os
import shutil
import subprocess
import sys

CANDIDATES = [
    r"C:\Program Files (x86)\PICO-8\pico8.exe",
    r"C:\Program Files\PICO-8\pico8.exe",
    "/Applications/PICO-8.app/Contents/MacOS/pico8",
    os.path.expanduser("~/Applications/PICO-8.app/Contents/MacOS/pico8"),
    "/usr/local/bin/pico8",
    os.path.expanduser("~/pico-8/pico8"),
]


class Pico8NotFound(Exception):
    pass


def locate(manifest_hint=None):
    """Order of preference: manifest, $PICO8_PATH, $PATH, then usual install dirs."""
    for cand in (manifest_hint, os.environ.get("PICO8_PATH")):
        if cand and os.path.isfile(cand):
            return cand

    for name in ("pico8", "pico8.exe"):
        found = shutil.which(name)
        if found:
            return found

    for cand in CANDIDATES:
        if os.path.isfile(cand):
            return cand

    raise Pico8NotFound(
        "could not find the PICO-8 executable.\n"
        "Set PICO8_PATH, put pico8 on your PATH, or add \"pico8\": \"<path>\" to p8project.json."
    )


def run(exe, args, wait=True):
    cmd = [exe] + list(args)
    if wait:
        return subprocess.call(cmd)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    return 0
