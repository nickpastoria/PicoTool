"""Drive `p8 watch` as a real subprocess and poke both sides of the sync."""
import os
import subprocess
import sys
import tempfile
import time

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "p8.py")


def run(args, cwd):
    return subprocess.run([sys.executable, TOOL] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(s)


base = tempfile.mkdtemp(prefix="p8watch-")
print(run(["init", "game", "--no-git"], base).stdout.strip().splitlines()[0])
proj = os.path.join(base, "game")
cart = os.path.join(proj, "build", "game.p8")
lua = os.path.join(proj, "src", "00.lua")

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
logf = open(os.path.join(base, "watch.log"), "w+", encoding="utf-8")
watch = subprocess.Popen([sys.executable, TOOL, "watch", "--interval", "0.2"],
                         cwd=proj, stdout=logf, stderr=subprocess.STDOUT, env=env)
time.sleep(1.5)

failures = []


def check(label, cond):
    print("  %-52s %s" % (label, "ok" if cond else "FAILED"))
    if not cond:
        failures.append(label)


try:
    # 1. editor -> cart
    write(lua, read(lua) + "\n-- change from the code editor\n")
    time.sleep(1.5)
    check("editor edit reaches the cartridge", "change from the code editor" in read(cart))

    # 2. PICO-8 -> editor (simulating a Ctrl-S inside PICO-8)
    write(cart, read(cart).replace("cls(1)", "cls(7) -- recoloured in pico-8"))
    time.sleep(1.5)
    check("PICO-8 save reaches src/00.lua", "recoloured in pico-8" in read(lua))

    # 3. no ping-pong: the loop must settle
    time.sleep(1.5)
    settled = read(cart)
    time.sleep(1.5)
    check("sync settles instead of ping-ponging", settled == read(cart))

    # 4. a sprite edit made in PICO-8 lands in the PNG
    before_png = os.path.getsize(os.path.join(proj, "gfx", "sprites.png"))
    text = read(cart)
    lines = text.split("\n")
    gi = lines.index("__gfx__")
    lines[gi + 1] = "f" * 128
    write(cart, "\n".join(lines))
    time.sleep(1.5)
    sys.path.insert(0, os.path.dirname(TOOL))
    from p8tool import palette, png
    img = png.read(os.path.join(proj, "gfx", "sprites.png"))
    row0 = [palette.index_of(img.palette[v]) for v in img.data[:128]]
    check("sprite edit from PICO-8 lands in gfx/sprites.png", row0 == [15] * 128)

    # 5. a sprite edit made in an image editor lands in the cart
    pixels = [palette.index_of(img.palette[v]) for v in img.data]
    pixels[128 * 5:128 * 5 + 128] = [3] * 128
    png.write_indexed(os.path.join(proj, "gfx", "sprites.png"), 128, 128, pixels, palette.PALETTE)
    time.sleep(1.5)
    lines = read(cart).split("\n")
    gi = lines.index("__gfx__")
    check("image-editor sprite edit reaches the cartridge", lines[gi + 6] == "3" * 128)
finally:
    watch.terminate()
    try:
        watch.wait(timeout=5)
    except Exception:
        watch.kill()
    logf.seek(0)
    print("\n--- watch output ---")
    print(logf.read().strip())
    logf.close()

print()
print("FAILURES: %s" % failures if failures else "all watch checks passed")
sys.exit(1 if failures else 0)
