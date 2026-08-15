"""The exploded, version-controllable project layout.

    p8project.json      manifest (cart name, header, version, tab order)
    src/NN_name.lua     one file per PICO-8 code tab, ordered by the NN prefix
    gfx/sprites.png     the 128x128 sprite sheet, 8-bit indexed PICO-8 palette
    gfx/flags.txt       one hex byte of sprite flags per sprite
    map/map.txt         the top 32 map rows, one hex byte per tile
    sfx/sfx.txt         64 sound effects, one per line
    sfx/music.txt       64 music patterns, one per line
    label.png           the cart label (optional)
    extra/<name>.txt    any section this tool does not recognise
    build/<name>.p8     the generated cartridge
"""

import json
import os
import re

from . import cart as cartmod
from . import palette
from . import png

MANIFEST = "p8project.json"
STATE_DIR = ".p8tool"

SPRITES_PNG = os.path.join("gfx", "sprites.png")
FLAGS_TXT = os.path.join("gfx", "flags.txt")
MAP_TXT = os.path.join("map", "map.txt")
SFX_TXT = os.path.join("sfx", "sfx.txt")
MUSIC_TXT = os.path.join("sfx", "music.txt")
LABEL_PNG = "label.png"
EXTRA_DIR = "extra"

_TAB_RE = re.compile(r"^(\d+)[_.-]?(.*)\.lua$", re.IGNORECASE)
_HEX_TOKEN = re.compile(r"[0-9a-fA-F]+")


class ProjectError(Exception):
    pass


def _read(path):
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        return fh.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(text)


def _write_if_changed(path, text):
    """Avoid touching mtimes when nothing actually changed."""
    if os.path.exists(path):
        try:
            if _read(path) == text:
                return False
        except OSError:
            pass
    _write(path, text)
    return True


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.manifest = {}
        self.load_manifest()

    # --------------------------------------------------------------- plumbing

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    @staticmethod
    def find(start=None):
        """Walk up from `start` looking for a p8project.json."""
        cur = os.path.abspath(start or os.getcwd())
        while True:
            if os.path.exists(os.path.join(cur, MANIFEST)):
                return Project(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                raise ProjectError(
                    "no p8project.json found here or in any parent directory.\n"
                    "Run `p8 init <name>` to create a project, or `p8 unpack <cart.p8>`."
                )
            cur = parent

    def load_manifest(self):
        p = self.path(MANIFEST)
        if os.path.exists(p):
            self.manifest = json.loads(_read(p))
        else:
            self.manifest = {}
        self.manifest.setdefault("name", os.path.basename(self.root))
        self.manifest.setdefault("header", cartmod.DEFAULT_HEADER)
        self.manifest.setdefault("version", cartmod.DEFAULT_VERSION)
        self.manifest.setdefault("cart", "build/%s.p8" % self.manifest["name"])
        self.manifest.setdefault("tabs", [])

    def save_manifest(self):
        _write(self.path(MANIFEST), json.dumps(self.manifest, indent=2) + "\n")

    @property
    def cart_path(self):
        return self.path(self.manifest["cart"])

    # ---------------------------------------------------------------- explode

    def unpack(self, cart):
        """Write every part of `cart` out as an editable file."""
        self.manifest["header"] = cart.header
        self.manifest["version"] = cart.version

        self._write_tabs(cart.tabs())
        self._write_gfx(cart.gfx())
        self._write_flags(cart.flags())
        self._write_map(cart.map_tiles())
        self._write_sfx(cart.sfx())
        self._write_music(cart.music())
        self._write_label(cart.label())
        self._write_extra(cart)

        self.save_manifest()

    def _write_tabs(self, tabs):
        src = self.path("src")
        os.makedirs(src, exist_ok=True)
        existing = self._tab_files()

        names = []
        for i, body in enumerate(tabs):
            name = existing[i] if i < len(existing) else "%02d.lua" % i
            names.append(name)
            # Always add the file-terminating newline; _read_tabs strips exactly
            # one back off, so a tab ending in a blank line survives the trip.
            _write_if_changed(os.path.join(src, name), body + "\n")

        # A tab was deleted inside PICO-8: drop the orphaned file.
        for stale in existing[len(tabs):]:
            try:
                os.remove(os.path.join(src, stale))
            except OSError:
                pass

        self.manifest["tabs"] = names

    def _tab_files(self):
        src = self.path("src")
        if not os.path.isdir(src):
            return []
        found = []
        for name in os.listdir(src):
            m = _TAB_RE.match(name)
            if m:
                found.append((int(m.group(1)), name))
        found.sort()
        return [name for _, name in found]

    def _write_gfx(self, pixels):
        path = self.path(SPRITES_PNG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            try:
                if self._read_gfx()[0] == pixels:
                    return
            except Exception:
                pass
        png.write_indexed(path, cartmod.GFX_COLS, cartmod.GFX_ROWS, pixels, palette.PALETTE)

    def _write_label(self, pixels):
        path = self.path(LABEL_PNG)
        if pixels is None:
            if os.path.exists(path):
                os.remove(path)
            return
        if os.path.exists(path):
            try:
                if self._read_image(path, cartmod.LABEL_COLS, cartmod.LABEL_ROWS)[0] == pixels:
                    return
            except Exception:
                pass
        png.write_indexed(path, cartmod.LABEL_COLS, cartmod.LABEL_ROWS, pixels, palette.PALETTE)

    def _write_flags(self, flags):
        lines = ["# PICO-8 sprite flags: one hex byte per sprite, 16 sprites per line.",
                 "# Line N covers sprites N*16 .. N*16+15."]
        for row in range(16):
            chunk = flags[row * 16:(row + 1) * 16]
            lines.append(" ".join("%02x" % v for v in chunk))
        _write_if_changed(self.path(FLAGS_TXT), "\n".join(lines) + "\n")

    def _write_map(self, rows):
        lines = ["# PICO-8 map, top half: one hex byte per tile, 128 tiles per line.",
                 "# Map rows 32-63 share memory with sprites 128-255, so they live in gfx/sprites.png."]
        trimmed = list(rows)
        while trimmed and not any(trimmed[-1]):
            trimmed.pop()
        for row in trimmed:
            lines.append(" ".join("%02x" % v for v in row))
        _write_if_changed(self.path(MAP_TXT), "\n".join(lines) + "\n")

    def _write_sfx(self, entries):
        lines = ["# PICO-8 sound effects. <index>: <editor mode><speed><loop start><loop end> <32 notes x 5 hex>"]
        for i, e in enumerate(entries):
            if set(e) <= {"0"}:
                continue
            lines.append("%02d: %s %s" % (i, e[:8], e[8:]))
        _write_if_changed(self.path(SFX_TXT), "\n".join(lines) + "\n")

    def _write_music(self, entries):
        lines = ["# PICO-8 music patterns. <index>: <flags> <sfx channel 0..3>"]
        for i, e in enumerate(entries):
            if e.replace(" ", "").strip("0") == "":
                continue
            lines.append("%02d: %s" % (i, e))
        _write_if_changed(self.path(MUSIC_TXT), "\n".join(lines) + "\n")

    def _write_extra(self, cart):
        known = set(cartmod.SECTION_ORDER)
        extra = {n: b for n, b in cart.sections.items() if n not in known}
        d = self.path(EXTRA_DIR)
        if not extra:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))
                try:
                    os.rmdir(d)
                except OSError:
                    pass
            return
        os.makedirs(d, exist_ok=True)
        for name, body in extra.items():
            _write_if_changed(os.path.join(d, name + ".txt"), "\n".join(body) + "\n")

    # ---------------------------------------------------------------- implode

    def pack(self):
        """Rebuild a Cart from the files on disk. Returns (cart, warnings)."""
        warnings = []
        c = cartmod.Cart(self.manifest["header"], int(self.manifest["version"]))

        c.set_tabs(self._read_tabs())

        pixels, warn = self._read_gfx()
        warnings += warn
        c.set_gfx(pixels)

        c.set_flags(self._read_flags())
        c.set_map_tiles(self._read_map())
        c.set_sfx(self._read_sfx())
        c.set_music(self._read_music())

        label_path = self.path(LABEL_PNG)
        if os.path.exists(label_path):
            lpix, warn = self._read_image(label_path, cartmod.LABEL_COLS, cartmod.LABEL_ROWS)
            warnings += warn
            c.set_label(lpix)

        d = self.path(EXTRA_DIR)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".txt"):
                    body = _read(os.path.join(d, f)).replace("\r\n", "\n").split("\n")
                    while body and body[-1] == "":
                        body.pop()
                    c.sections[f[:-4]] = body

        return c, warnings

    def _read_tabs(self):
        src = self.path("src")
        files = self._tab_files()
        if not files:
            return [""]
        out = []
        for name in files:
            body = _read(os.path.join(src, name)).replace("\r\n", "\n")
            if body.endswith("\n"):
                body = body[:-1]
            out.append(body)
        return out

    def _read_image(self, path, width, height):
        img = png.read(path)
        if img.width != width or img.height != height:
            raise ProjectError(
                "%s must be exactly %dx%d pixels (found %dx%d)"
                % (os.path.relpath(path, self.root), width, height, img.width, img.height)
            )

        warnings = []
        unmatched = {}
        pixels = []

        if img.mode == "P":
            pal_idx = {}
            for i, rgb in enumerate(img.palette):
                pal_idx[i] = palette.index_of(rgb)
            for v in img.data:
                idx = pal_idx.get(v)
                if idx is None:
                    rgb = img.palette[v] if v < len(img.palette) else (0, 0, 0)
                    idx = palette.nearest(rgb)
                    unmatched[rgb] = idx
                pixels.append(idx)
        else:
            cache = {}
            for r, g, b, a in img.data:
                if a < 128:
                    pixels.append(0)
                    continue
                key = (r, g, b)
                idx = cache.get(key)
                if idx is None:
                    idx = palette.index_of(key)
                    if idx is None:
                        idx = palette.nearest(key)
                        unmatched[key] = idx
                    cache[key] = idx
                pixels.append(idx)

        if unmatched:
            shown = list(unmatched.items())[:6]
            detail = ", ".join(
                "#%02X%02X%02X->%d(%s)" % (r, g, b, i, palette.NAMES[i])
                for (r, g, b), i in shown
            )
            more = "" if len(unmatched) <= 6 else " (+%d more)" % (len(unmatched) - 6)
            warnings.append(
                "%s uses %d colour(s) outside the PICO-8 palette; snapped to nearest: %s%s"
                % (os.path.relpath(path, self.root), len(unmatched), detail, more)
            )
        return pixels, warnings

    def _read_gfx(self):
        path = self.path(SPRITES_PNG)
        if not os.path.exists(path):
            return [0] * (cartmod.GFX_COLS * cartmod.GFX_ROWS), []
        return self._read_image(path, cartmod.GFX_COLS, cartmod.GFX_ROWS)

    def _read_flags(self):
        path = self.path(FLAGS_TXT)
        if not os.path.exists(path):
            return [0] * cartmod.GFF_BYTES
        vals = []
        for line in _read(path).split("\n"):
            line = line.split("#", 1)[0]
            for tok in line.split():
                if _HEX_TOKEN.fullmatch(tok):
                    vals.append(int(tok, 16) & 0xFF)
        vals = vals[:cartmod.GFF_BYTES]
        vals += [0] * (cartmod.GFF_BYTES - len(vals))
        return vals

    def _read_map(self):
        path = self.path(MAP_TXT)
        rows = []
        if os.path.exists(path):
            for line in _read(path).split("\n"):
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if " " in line:
                    toks = line.split()
                else:
                    toks = [line[i:i + 2] for i in range(0, len(line), 2)]
                row = [int(t, 16) & 0xFF for t in toks if _HEX_TOKEN.fullmatch(t)]
                row = row[:cartmod.MAP_COLS]
                row += [0] * (cartmod.MAP_COLS - len(row))
                rows.append(row)
        rows = rows[:cartmod.MAP_ROWS]
        while len(rows) < cartmod.MAP_ROWS:
            rows.append([0] * cartmod.MAP_COLS)
        return rows

    def _read_indexed_lines(self, path, count, blank):
        out = [blank] * count
        if not os.path.exists(path):
            return out
        for line in _read(path).split("\n"):
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            idx, _, rest = line.partition(":")
            idx = idx.strip()
            if not idx.isdigit():
                continue
            i = int(idx)
            if 0 <= i < count:
                out[i] = rest.strip()
        return out

    def _read_sfx(self):
        raw = self._read_indexed_lines(self.path(SFX_TXT), cartmod.SFX_COUNT, "")
        return [r.replace(" ", "") for r in raw]

    def _read_music(self):
        raw = self._read_indexed_lines(self.path(MUSIC_TXT), cartmod.MUSIC_COUNT, "00 00000000")
        out = []
        for r in raw:
            parts = r.split()
            if len(parts) >= 2:
                out.append("%s %s" % (parts[0], parts[1]))
            else:
                out.append("00 00000000")
        return out
