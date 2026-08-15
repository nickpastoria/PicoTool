"""Parse and serialise PICO-8 `.p8` cartridges.

A `.p8` is a plain-text file: a header line, a `version N` line, then a series
of `__section__` markers. This module keeps every section's raw lines so that
anything we do not understand still round-trips untouched.
"""

import os

DEFAULT_HEADER = "pico-8 cartridge // http://www.pico-8.com"
DEFAULT_VERSION = 42

# The order PICO-8 itself writes sections in.
SECTION_ORDER = ["lua", "gfx", "label", "gff", "map", "sfx", "music"]

GFX_ROWS, GFX_COLS = 128, 128
LABEL_ROWS, LABEL_COLS = 128, 128
MAP_ROWS, MAP_COLS = 32, 128
GFF_BYTES = 256
SFX_COUNT, SFX_LEN = 64, 168
MUSIC_COUNT = 64

TAB_SEPARATOR = "-->8"


class CartError(Exception):
    pass


class Cart:
    def __init__(self, header=DEFAULT_HEADER, version=DEFAULT_VERSION, sections=None):
        self.header = header
        self.version = version
        self.sections = sections if sections is not None else {}

    # ---------------------------------------------------------------- parsing

    @classmethod
    def parse(cls, text):
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        header = DEFAULT_HEADER
        version = DEFAULT_VERSION
        sections = {}
        order = []

        idx = 0
        if idx < len(lines) and not lines[idx].startswith("__"):
            header = lines[idx].strip()
            idx += 1
        if idx < len(lines) and lines[idx].strip().startswith("version"):
            parts = lines[idx].split()
            if len(parts) > 1 and parts[1].isdigit():
                version = int(parts[1])
            idx += 1

        current = None
        for line in lines[idx:]:
            stripped = line.strip()
            if stripped.startswith("__") and stripped.endswith("__") and len(stripped) > 4:
                current = stripped[2:-2]
                sections[current] = []
                order.append(current)
            elif current is not None:
                sections[current].append(line)

        # PICO-8 emits a blank line between sections; drop the trailing one.
        for name, body in sections.items():
            while body and body[-1] == "":
                body.pop()

        cart = cls(header, version, sections)
        cart.order = order
        return cart

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            head = fh.read(8)
        if head.startswith(b"\x89PNG"):
            base = os.path.basename(path)
            stem = base[:-7] if base.lower().endswith(".p8.png") else os.path.splitext(base)[0]
            raise CartError(
                "%s is a .p8.png cartridge, which stores its code compressed inside "
                "the image.\nOpen it in PICO-8 and re-save it as plain text first "
                "(load the cart, then `save %s.p8`)." % (base, stem)
            )
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            return cls.parse(fh.read())

    # ------------------------------------------------------------ serialising

    def to_text(self):
        out = [self.header, "version %d" % self.version]
        names = [n for n in SECTION_ORDER if n in self.sections]
        names += [n for n in self.sections if n not in SECTION_ORDER]
        for name in names:
            out.append("__%s__" % name)
            out.extend(self.sections[name])
        return "\n".join(out) + "\n"

    def save(self, path):
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
            fh.write(self.to_text())

    def canonical(self):
        """Normalised copy: sections in PICO-8's order, trailing zero rows trimmed.

        Exploding a cart and rebuilding it always lands here, so this is the
        form `p8 pack` produces and the fixed point round-trip tests assert on.
        """
        c = Cart(self.header, self.version, dict(self.sections))
        c.set_tabs(self.tabs())
        c.set_gfx(self.gfx())
        c.set_flags(self.flags())
        c.set_map_tiles(self.map_tiles())
        c.set_sfx(self.sfx())
        c.set_music(self.music())
        if "label" in self.sections:
            c.set_label(self.label())
        return c

    # ------------------------------------------------------------------- code

    def tabs(self):
        """The Lua source split on `-->8` tab separators."""
        body = self.sections.get("lua", [])
        tabs, current = [], []
        for line in body:
            if line.rstrip() == TAB_SEPARATOR:
                tabs.append(current)
                current = []
            else:
                current.append(line)
        tabs.append(current)
        return ["\n".join(t) for t in tabs]

    def set_tabs(self, tabs):
        body = []
        for i, tab in enumerate(tabs):
            if i:
                body.append(TAB_SEPARATOR)
            body.extend(tab.replace("\r\n", "\n").split("\n"))
        while body and body[-1] == "":
            body.pop()
        self.sections["lua"] = body

    # --------------------------------------------------------------- graphics

    def _hex_grid(self, name, rows, cols):
        body = self.sections.get(name, [])
        grid = []
        for y in range(rows):
            line = body[y] if y < len(body) else ""
            line = line.strip().ljust(cols, "0")[:cols]
            grid.append([int(c, 16) if c in "0123456789abcdefABCDEF" else 0 for c in line])
        return grid

    def _set_hex_grid(self, name, grid):
        lines = ["".join("%x" % v for v in row) for row in grid]
        self.sections[name] = _trim_zero_lines(lines)

    def gfx(self):
        """The 128x128 sprite sheet as a row-major list of palette indices."""
        grid = self._hex_grid("gfx", GFX_ROWS, GFX_COLS)
        return [v for row in grid for v in row]

    def set_gfx(self, pixels):
        grid = [pixels[y * GFX_COLS:(y + 1) * GFX_COLS] for y in range(GFX_ROWS)]
        self._set_hex_grid("gfx", grid)

    def label(self):
        if "label" not in self.sections:
            return None
        grid = self._hex_grid("label", LABEL_ROWS, LABEL_COLS)
        return [v for row in grid for v in row]

    def set_label(self, pixels):
        if pixels is None:
            self.sections.pop("label", None)
            return
        grid = [pixels[y * LABEL_COLS:(y + 1) * LABEL_COLS] for y in range(LABEL_ROWS)]
        # A label is a fixed-size image; do not trim it.
        self.sections["label"] = ["".join("%x" % v for v in row) for row in grid]

    # ------------------------------------------------------------ sprite flags

    def flags(self):
        """One byte of flags per sprite, 256 of them."""
        raw = "".join(line.strip() for line in self.sections.get("gff", []))
        raw = raw.ljust(GFF_BYTES * 2, "0")
        return [int(raw[i * 2:i * 2 + 2], 16) for i in range(GFF_BYTES)]

    def set_flags(self, values):
        text = "".join("%02x" % (v & 0xFF) for v in values[:GFF_BYTES])
        text = text.ljust(GFF_BYTES * 2, "0")
        self.sections["gff"] = _trim_zero_lines([text[:256], text[256:]])

    # -------------------------------------------------------------------- map

    def map_tiles(self):
        """The top half of the map: 32 rows of 128 tile indices.

        The bottom half (rows 32-63) shares memory with sprites 128-255 and
        lives in the `__gfx__` section, so it round-trips via the sprite sheet.
        """
        body = self.sections.get("map", [])
        rows = []
        for y in range(MAP_ROWS):
            line = body[y].strip() if y < len(body) else ""
            line = line.ljust(MAP_COLS * 2, "0")[:MAP_COLS * 2]
            rows.append([int(line[x * 2:x * 2 + 2], 16) for x in range(MAP_COLS)])
        return rows

    def set_map_tiles(self, rows):
        lines = ["".join("%02x" % (v & 0xFF) for v in row) for row in rows]
        self.sections["map"] = _trim_zero_lines(lines)

    # ------------------------------------------------------------ sfx / music

    def sfx(self):
        body = self.sections.get("sfx", [])
        out = []
        for i in range(SFX_COUNT):
            line = body[i].strip() if i < len(body) else ""
            out.append(line.ljust(SFX_LEN, "0")[:SFX_LEN])
        return out

    def set_sfx(self, entries):
        lines = [e.strip().ljust(SFX_LEN, "0")[:SFX_LEN] for e in entries[:SFX_COUNT]]
        while len(lines) < SFX_COUNT:
            lines.append("0" * SFX_LEN)
        self.sections["sfx"] = _trim_zero_lines(lines)

    def music(self):
        body = self.sections.get("music", [])
        out = []
        for i in range(MUSIC_COUNT):
            out.append(body[i].strip() if i < len(body) else "00 00000000")
        return out

    def set_music(self, entries):
        lines = [e.strip() for e in entries[:MUSIC_COUNT]]
        while len(lines) < MUSIC_COUNT:
            lines.append("00 00000000")
        while lines and lines[-1].replace(" ", "").strip("0") == "":
            lines.pop()
        self.sections["music"] = lines


def _trim_zero_lines(lines):
    """PICO-8 omits trailing all-zero rows; matching that keeps diffs small."""
    out = list(lines)
    while out and set(out[-1]) <= {"0"}:
        out.pop()
    return out
