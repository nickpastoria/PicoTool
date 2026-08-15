"""Two-way sync between the exploded project files and the built `.p8`.

Both sides are editable: you change `src/*.lua` in your code editor, or you
change sprites/sfx inside PICO-8 itself. The syncer works out which side moved
since the last known-good state and pushes the change the other way. If both
sides moved it refuses and says so rather than picking a winner.
"""

import hashlib
import json
import os
import shutil
import time

from . import cart as cartmod
from .project import STATE_DIR

STATE_FILE = "state.json"
BACKUP_DIR = "backups"
MAX_BACKUPS = 20

WATCH_DIRS = ["src", "gfx", "map", "sfx", "extra"]
WATCH_FILES = ["label.png", "p8project.json"]

IDLE = "idle"
PACKED = "packed"
UNPACKED = "unpacked"
CONFLICT = "conflict"


class Result:
    def __init__(self, kind, messages=None, warnings=None):
        self.kind = kind
        self.messages = messages or []
        self.warnings = warnings or []


def _sha1(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    return hashlib.sha1(data).hexdigest()


class Syncer:
    def __init__(self, project):
        self.project = project
        self._fp = None

    # ------------------------------------------------------------------ state

    @property
    def state_path(self):
        return self.project.path(STATE_DIR, STATE_FILE)

    def load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save_state(self, src_hash, cart_hash):
        os.makedirs(self.project.path(STATE_DIR), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump({"src": src_hash, "cart": cart_hash, "at": time.time()}, fh, indent=2)

    # ------------------------------------------------------------- inspection

    def cart_hash(self):
        p = self.project.cart_path
        if not os.path.exists(p):
            return None
        with open(p, "rb") as fh:
            return _sha1(fh.read())

    def build(self):
        """Pack the sources in memory. Returns (text, hash, warnings)."""
        c, warnings = self.project.pack()
        text = c.to_text()
        return text, _sha1(text), warnings

    def fingerprint(self):
        """Cheap mtime/size snapshot used to skip work while nothing changes."""
        fp = {}
        root = self.project.root
        for d in WATCH_DIRS:
            full = os.path.join(root, d)
            if not os.path.isdir(full):
                continue
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = [x for x in dirnames if x not in (".git", "__pycache__")]
                for name in filenames:
                    p = os.path.join(dirpath, name)
                    try:
                        st = os.stat(p)
                        fp[p] = (st.st_mtime_ns, st.st_size)
                    except OSError:
                        pass
        for name in WATCH_FILES + [self.project.manifest["cart"]]:
            p = os.path.join(root, name)
            try:
                st = os.stat(p)
                fp[p] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
        return fp

    # ------------------------------------------------------------ the actions

    def write_cart(self, text):
        p = self.project.cart_path
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
            fh.write(text)
        return _sha1(text)

    def _backup_sources(self, text):
        """Snapshot the current sources as a cart before we overwrite them."""
        d = self.project.path(STATE_DIR, BACKUP_DIR)
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s-before-unpack-%s" % (stamp, os.path.basename(self.project.cart_path))
        with open(os.path.join(d, name), "w", encoding="utf-8",
                  errors="surrogateescape", newline="\n") as fh:
            fh.write(text)
        for stale in sorted(os.listdir(d))[:-MAX_BACKUPS]:
            try:
                os.remove(os.path.join(d, stale))
            except OSError:
                pass

    def force_pack(self):
        text, src_hash, warnings = self.build()
        cart_hash = self.write_cart(text)
        self.save_state(src_hash, cart_hash)
        return Result(PACKED, ["built %s" % self.project.manifest["cart"]], warnings)

    def force_unpack(self):
        p = self.project.cart_path
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        c = cartmod.Cart.load(p)
        try:
            self._backup_sources(self.build()[0])
        except Exception:
            pass
        self.project.unpack(c)
        text, src_hash, warnings = self.build()
        self.save_state(src_hash, self.cart_hash())
        return Result(UNPACKED, ["updated project files from %s" % self.project.manifest["cart"]], warnings)

    def tick(self, prefer=None):
        """Decide what moved and sync it. `prefer` is None, 'src' or 'cart'."""
        state = self.load_state()
        text, src_hash, warnings = self.build()
        cart_hash = self.cart_hash()

        if cart_hash is None:
            cart_hash_new = self.write_cart(text)
            self.save_state(src_hash, cart_hash_new)
            return Result(PACKED, ["created %s" % self.project.manifest["cart"]], warnings)

        src_moved = state.get("src") != src_hash
        cart_moved = state.get("cart") != cart_hash

        # No recorded state yet (fresh clone): trust the sources.
        if not state:
            if src_hash == cart_hash:
                self.save_state(src_hash, cart_hash)
                return Result(IDLE, [], warnings)
            src_moved, cart_moved = True, False

        if src_moved and cart_moved:
            if prefer == "src":
                return self.force_pack()
            if prefer == "cart":
                return self.force_unpack()
            return Result(CONFLICT, [
                "both the project files and %s changed since the last sync."
                % self.project.manifest["cart"],
                "resolve with `p8 pack --force` (keep files) or `p8 unpack --force` (keep cart).",
            ], warnings)

        if src_moved:
            self.write_cart(text)
            self.save_state(src_hash, _sha1(text))
            return Result(PACKED, ["built %s" % self.project.manifest["cart"]], warnings)

        if cart_moved:
            return self.force_unpack()

        return Result(IDLE, [], warnings)

    # ------------------------------------------------------------------ watch

    def changed_since_last_look(self):
        fp = self.fingerprint()
        changed = fp != self._fp
        self._fp = fp
        return changed
