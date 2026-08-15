"""Round-trip tests: a cartridge must survive unpack -> pack byte for byte."""

import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from p8tool import cart as cartmod  # noqa: E402
from p8tool import palette, png  # noqa: E402
from p8tool.project import MANIFEST, Project  # noqa: E402
from p8tool.sync import Syncer  # noqa: E402


def make_busy_cart(seed=1234, label=True, extra=True):
    """A cartridge with every section populated, including awkward edge cases."""
    rng = random.Random(seed)
    lines = ["pico-8 cartridge // http://www.pico-8.com", "version 42", "__lua__"]

    lines += [
        "-- tab one",
        'print("hi \\"there\\"")',
        "-->8",
        "-- tab two",
        "function f() return 1 end",
        "",
        "-->8",
        "-- tab three, with unicode glyphs: █░◮",
        "x=⬆️",
    ]

    lines.append("__gfx__")
    for y in range(128):
        lines.append("".join("%x" % rng.randrange(16) for _ in range(128)))

    if label:
        lines.append("__label__")
        for y in range(128):
            lines.append("".join("%x" % rng.randrange(16) for _ in range(128)))

    lines.append("__gff__")
    for _ in range(2):
        lines.append("".join("%02x" % rng.randrange(256) for _ in range(128)))

    lines.append("__map__")
    for y in range(32):
        lines.append("".join("%02x" % rng.randrange(256) for _ in range(128)))

    lines.append("__sfx__")
    for _ in range(64):
        lines.append("".join("%x" % rng.randrange(16) for _ in range(168)))

    lines.append("__music__")
    for _ in range(64):
        lines.append("%02x %02x%02x%02x%02x" % tuple(rng.randrange(256) for _ in range(5)))

    if extra:
        lines.append("__gfx2__")
        lines.append("deadbeef")

    return "\n".join(lines) + "\n"


def _slurp(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TempProject:
    def __init__(self, text=None, name="probe"):
        self.text = text
        self.name = name

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="p8tool-test-")
        source = cartmod.Cart.parse(self.text) if self.text else cartmod.Cart()
        with open(os.path.join(self.dir, MANIFEST), "w", encoding="utf-8") as fh:
            fh.write('{"name": "%s", "cart": "build/%s.p8", "version": %d}'
                     % (self.name, self.name, source.version))
        self.project = Project(self.dir)
        self.project.unpack(source)
        return self.project

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


class RoundTripTests(unittest.TestCase):
    """Exploding a cart and rebuilding it must land on its canonical form,
    and canonical form must be a fixed point of that operation."""

    def assert_roundtrips(self, text):
        canonical = cartmod.Cart.parse(text).canonical().to_text()
        self.assertEqual(canonical, cartmod.Cart.parse(canonical).canonical().to_text(),
                         "canonical form is not idempotent")
        with TempProject(text) as project:
            rebuilt, warnings = project.pack()
            self.assertEqual([], warnings)
            self.assertEqual(canonical, rebuilt.to_text())
        # ...and a second lap changes nothing.
        with TempProject(canonical) as project:
            rebuilt, _ = project.pack()
            self.assertEqual(canonical, rebuilt.to_text())

    def test_full_cart(self):
        self.assert_roundtrips(make_busy_cart())

    def test_no_label_no_extra(self):
        self.assert_roundtrips(make_busy_cart(seed=7, label=False, extra=False))

    def test_several_seeds(self):
        for seed in (1, 2, 3, 99):
            self.assert_roundtrips(make_busy_cart(seed=seed))

    def test_sparse_cart(self):
        text = (
            "pico-8 cartridge // http://www.pico-8.com\n"
            "version 42\n"
            "__lua__\n"
            "print('hi')\n"
            "__gfx__\n"
            + "\n".join("".join("%x" % ((x + y) % 16) for x in range(128)) for y in range(3))
            + "\n__gff__\n" + "0" * 256 + "\n" + "0" * 256 + "\n"
            "__map__\n"
            "0102030405" + "0" * 246 + "\n"
            "__sfx__\n"
            "011000" + "0" * 162 + "\n"
            "__music__\n"
            "00 41424344\n"
        )
        self.assert_roundtrips(text)

    def test_empty_cart(self):
        text = ("pico-8 cartridge // http://www.pico-8.com\n"
                "version 42\n__lua__\n__gfx__\n__gff__\n__map__\n__sfx__\n__music__\n")
        self.assert_roundtrips(text)

    def test_crlf_input_is_normalised(self):
        text = make_busy_cart(seed=5)
        with TempProject(text.replace("\n", "\r\n")) as project:
            rebuilt, _ = project.pack()
            self.assertEqual(text, rebuilt.to_text())


class TabTests(unittest.TestCase):
    def test_tabs_split_and_rejoin(self):
        c = cartmod.Cart.parse(make_busy_cart())
        self.assertEqual(3, len(c.tabs()))
        self.assertIn("tab two", c.tabs()[1])

    def test_renamed_tab_files_keep_order(self):
        with TempProject(make_busy_cart()) as project:
            src = project.path("src")
            os.rename(os.path.join(src, "00.lua"), os.path.join(src, "00_main.lua"))
            os.rename(os.path.join(src, "01.lua"), os.path.join(src, "01_helpers.lua"))
            rebuilt, _ = project.pack()
            self.assertEqual(3, len(rebuilt.tabs()))
            self.assertIn("tab one", rebuilt.tabs()[0])
            self.assertIn("tab two", rebuilt.tabs()[1])

    def test_tab_removed_in_pico8_deletes_the_file(self):
        with TempProject(make_busy_cart()) as project:
            self.assertEqual(3, len(project._tab_files()))
            smaller = cartmod.Cart.parse(make_busy_cart())
            smaller.set_tabs(smaller.tabs()[:1])
            project.unpack(smaller)
            self.assertEqual(1, len(project._tab_files()))

    def test_added_tab_gets_a_new_file(self):
        with TempProject(make_busy_cart()) as project:
            bigger = cartmod.Cart.parse(make_busy_cart())
            bigger.set_tabs(bigger.tabs() + ["-- brand new"])
            project.unpack(bigger)
            self.assertEqual(4, len(project._tab_files()))
            rebuilt, _ = project.pack()
            self.assertIn("brand new", rebuilt.tabs()[3])


class ImageTests(unittest.TestCase):
    def test_indexed_png_roundtrip(self):
        rng = random.Random(3)
        pixels = [rng.randrange(16) for _ in range(128 * 128)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.png")
            png.write_indexed(p, 128, 128, pixels, palette.PALETTE)
            img = png.read(p)
            self.assertEqual("P", img.mode)
            self.assertEqual(pixels, [palette.index_of(img.palette[v]) for v in img.data])

    def test_rgb_png_is_matched_by_colour(self):
        """What an image editor that flattens to RGB would hand us back."""
        rng = random.Random(4)
        pixels = [rng.randrange(16) for _ in range(128 * 128)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.png")
            _write_rgb(p, 128, 128, [palette.PALETTE[v] for v in pixels])
            with TempProject(make_busy_cart()) as project:
                shutil.copy(p, project.path("gfx", "sprites.png"))
                got, warnings = project._read_gfx()
                self.assertEqual([], warnings)
                self.assertEqual(pixels, got)

    def test_off_palette_colour_snaps_and_warns(self):
        with TempProject(make_busy_cart()) as project:
            colours = [(255, 1, 78)] * (128 * 128)  # nearly PICO-8 red
            _write_rgb(project.path("gfx", "sprites.png"), 128, 128, colours)
            got, warnings = project._read_gfx()
            self.assertEqual(1, len(warnings))
            self.assertEqual({8}, set(got))

    def test_transparent_pixels_become_colour_zero(self):
        with TempProject(make_busy_cart()) as project:
            _write_rgba(project.path("gfx", "sprites.png"), 128, 128,
                        [(255, 255, 255, 0)] * (128 * 128))
            got, _ = project._read_gfx()
            self.assertEqual({0}, set(got))

    def test_wrong_size_image_is_rejected(self):
        with TempProject(make_busy_cart()) as project:
            png.write_indexed(project.path("gfx", "sprites.png"), 64, 64,
                              [0] * 4096, palette.PALETTE)
            with self.assertRaises(Exception):
                project._read_gfx()


class SyncTests(unittest.TestCase):
    def test_source_edit_rebuilds_the_cart(self):
        with TempProject(make_busy_cart()) as project:
            s = Syncer(project)
            s.force_pack()
            path = project.path("src", "00.lua")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n-- edited externally\n")
            result = s.tick()
            self.assertEqual("packed", result.kind)
            self.assertIn("edited externally", _slurp(project.cart_path))

    def test_cart_edit_updates_the_sources(self):
        with TempProject(make_busy_cart()) as project:
            s = Syncer(project)
            s.force_pack()
            text = _slurp(project.cart_path)
            text = text.replace("-- tab one", "-- tab one, edited in pico-8")
            with open(project.cart_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            result = s.tick()
            self.assertEqual("unpacked", result.kind)
            self.assertIn("edited in pico-8",
                          _slurp(project.path("src", "00.lua")))

    def test_both_sides_changed_is_a_conflict(self):
        with TempProject(make_busy_cart()) as project:
            s = Syncer(project)
            s.force_pack()
            with open(project.path("src", "00.lua"), "a", encoding="utf-8") as fh:
                fh.write("\n-- from the editor\n")
            with open(project.cart_path, "a", encoding="utf-8") as fh:
                fh.write("\n__extra__\nx\n")
            self.assertEqual("conflict", s.tick().kind)
            # ...and the caller can pick a winner.
            self.assertEqual("packed", s.tick(prefer="src").kind)
            self.assertEqual("idle", s.tick().kind)

    def test_sync_is_stable_when_nothing_changes(self):
        with TempProject(make_busy_cart()) as project:
            s = Syncer(project)
            s.force_pack()
            for _ in range(3):
                self.assertEqual("idle", s.tick().kind)

    def test_no_ping_pong_after_reverse_sync(self):
        """A save from PICO-8 must not bounce back and re-trigger a pack."""
        with TempProject(make_busy_cart()) as project:
            s = Syncer(project)
            s.force_pack()
            text = _slurp(project.cart_path)
            with open(project.cart_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.replace("print(", "printh("))
            self.assertEqual("unpacked", s.tick().kind)
            self.assertEqual("idle", s.tick().kind)
            self.assertEqual("idle", s.tick().kind)


class MapAndFlagTests(unittest.TestCase):
    def test_flags_survive(self):
        c = cartmod.Cart.parse(make_busy_cart())
        flags = c.flags()
        self.assertEqual(256, len(flags))
        c2 = cartmod.Cart()
        c2.set_flags(flags)
        self.assertEqual(flags, c2.flags())

    def test_map_survives(self):
        c = cartmod.Cart.parse(make_busy_cart())
        tiles = c.map_tiles()
        self.assertEqual(32, len(tiles))
        self.assertEqual(128, len(tiles[0]))
        c2 = cartmod.Cart()
        c2.set_map_tiles(tiles)
        self.assertEqual(tiles, c2.map_tiles())

    def test_map_text_accepts_unspaced_hex(self):
        with TempProject(make_busy_cart()) as project:
            rows = project._read_map()
            packed = "\n".join("".join("%02x" % v for v in row) for row in rows)
            with open(project.path("map", "map.txt"), "w", encoding="utf-8") as fh:
                fh.write(packed + "\n")
            self.assertEqual(rows, project._read_map())


def _png_chunk(name, data):
    import struct, zlib
    body = name + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _write_raw(path, width, height, ctype, samples, channels):
    import struct, zlib
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes(samples[y * width + x])
    blob = bytearray(png.PNG_SIG)
    blob += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
    blob += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    blob += _png_chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(blob)


def _write_rgb(path, w, h, pixels):
    _write_raw(path, w, h, 2, pixels, 3)


def _write_rgba(path, w, h, pixels):
    _write_raw(path, w, h, 6, pixels, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
