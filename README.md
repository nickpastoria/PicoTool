# p8tool

PICO-8 development with real version control and real external editors.

A `.p8` cartridge is a single file that holds code, sprites, map, flags, sound and
music all together. That is lovely for making games and miserable for everything
else: every commit touches one giant file, merges conflict on the whole cart, and
no code editor or art tool can open it.

`p8tool` explodes a cartridge into ordinary files, rebuilds it on demand, and
keeps the two in sync **in both directions** while you work. Edit Lua in VS Code,
draw sprites in Aseprite, and write music in PICO-8 itself — at the same time.

No dependencies. Just Python 3.8+.

## Install

```
git clone <this repo>
```

Then put the repo directory on your `PATH` (it contains `p8.cmd` for Windows and
`p8` for macOS/Linux), or install it properly:

```
pip install -e .
```

Check everything is wired up:

```
p8 doctor
```

PICO-8 is found via `$PICO8_PATH`, your `PATH`, or the usual install locations.
You can also pin it per project with `"pico8": "C:/path/to/pico8.exe"` in
`p8project.json`.

## Use

```
p8 init mygame          # new project, with a starter cart and a git repo
p8 unpack mygame.p8     # or: explode a cartridge you already have
cd mygame
p8 run                  # build, launch PICO-8, sync both ways
```

`p8 run` watches everything. Save `src/00.lua` in your editor and the cart
reloads inside the PICO-8 window that is already open, so it stays exactly
where you put it. Draw a sprite in PICO-8, hit Ctrl-S, and `gfx/sprites.png`
updates on disk. Commit the result and the diff is readable.

The reload works by sending PICO-8 the keys you would press yourself (Escape,
then Ctrl-R), which means the window is focused for a moment before focus goes
back to where it was. PICO-8 refuses to reload while it has unsaved changes of
its own, so save inside PICO-8 first if a reload seems to do nothing. If the
keystrokes cannot be delivered at all, `p8 run` says so once and falls back to
restarting PICO-8; `p8 run --relaunch` picks that behaviour deliberately.

| Command | |
| --- | --- |
| `p8 init [name]` | New project. `--from cart.p8` seeds it from an existing cartridge, `--here` uses the current directory |
| `p8 unpack [cart.p8]` | Explode a cartridge. With no argument, pulls changes out of this project's cart |
| `p8 pack` | Build the cartridge from the files |
| `p8 sync` | Push whichever side changed to the other |
| `p8 status` | What is out of sync, and where PICO-8 is |
| `p8 watch` | Sync continuously, without launching PICO-8 |
| `p8 run` | Build, launch PICO-8, sync continuously, reload the running cart on code changes |
| `p8 export dist/game.p8.png` | Export through PICO-8 |
| `p8 tab list` / `p8 tab new <name>` | Manage code tabs |
| `p8 doctor` | Check the setup and prove a cartridge survives a round trip |

## Project layout

```
mygame/
  p8project.json     cart name, header, PICO-8 version, tab order
  src/00.lua         one file per PICO-8 code tab
  src/01_player.lua  rename freely - the NN prefix sets the tab order
  src/lib/vec.lua    a library - no NN prefix, so not a tab: `#include` it
  gfx/sprites.png    128x128 sprite sheet, 8-bit indexed PICO-8 palette
  gfx/flags.txt      sprite flags, one hex byte per sprite
  map/map.txt        map rows 0-31, one hex byte per tile
  sfx/sfx.txt        64 sound effects
  sfx/music.txt      64 music patterns
  label.png          cart label, if the cart has one
  extra/*.txt        any section p8tool does not recognise, preserved verbatim
  build/mygame.p8    the generated cartridge (gitignored)
```

`build/` and `.p8tool/` are gitignored: the files above are the source of truth
and `p8 pack` regenerates the cart from them.

### Code tabs

Each PICO-8 tab is one `.lua` file. Order comes from the numeric filename prefix,
so `git mv src/01.lua src/01_player.lua` is a rename, not a rewrite. Add a tab
with `p8 tab new player`, or just create `src/02_enemies.lua` yourself. Delete a
tab in PICO-8 and the file goes away on the next sync.

### Libraries: `#include`

A tab can pull in any other file under `src/`, so shared code lives in its own
file instead of being pasted into whichever tab needed it:

```lua
-- src/00.lua
#include lib/vec.lua

function _init() p = vec(64, 64) end
```

```lua
-- src/lib/vec.lua
function vec(x, y) return {x = x, y = y} end
```

`p8 pack` resolves that itself, before PICO-8 ever sees the cart — the library's
code is compiled straight into the tab. Paths are relative to the file holding
the directive, and includes can nest.

This is deliberately **not** PICO-8's own `#include`. PICO-8 resolves those as
the cart loads, and only reaches files inside its own carts folder, so a cart in
`build/` cannot see `src/` at all. Nor does that code ever enter PICO-8's editor,
which means anything you changed in there would have nowhere to go on the way
back out.

Resolving it here fixes both. In the built cartridge the region is fenced with
marker comments:

```lua
--#include lib/vec.lua
function vec(x, y) return {x = x, y = y} end
--#end lib/vec.lua
```

so unpacking can read it in reverse: the body goes back to `src/lib/vec.lua` and
the region collapses to the one-line directive again. **Edit a library function
inside PICO-8 and the change lands in the library file**, not in the tab that
included it. Both directions are fixed points, so the watcher still settles.

Some details worth knowing:

- Includes must resolve to a file under `src/` — that is the tree p8tool watches,
  so a library outside it would be source no change ever reached. A path leading
  elsewhere is refused with a warning.
- A file that cannot be resolved — missing, circular, or naming a tab, which is
  already concatenated for you — keeps its directive line untouched and warns.
  Nothing is silently dropped.
- Including the same file from two tabs inlines it twice, exactly as writing it
  twice would. There are no include guards.
- Markers cost two comment lines per include. Comments are free in PICO-8's token
  budget, and count only against the character limit.
- `p8 status` lists the library files it can see.

### Sprites

`gfx/sprites.png` is a normal indexed PNG with the 16 PICO-8 colours, so any
image editor can open it. On the way back in:

- exact palette colours map straight to their index;
- anything else snaps to the nearest PICO-8 colour and you get a warning naming
  the offending colours, so a stray anti-aliased pixel never passes silently;
- fully transparent pixels become colour 0.

Sprites 128-255 share memory with map rows 32-63 — that is PICO-8's design, not a
quirk of this tool. The bottom half of `gfx/sprites.png` *is* the bottom half of
your map. `map/map.txt` holds rows 0-31 only.

### Sound

`sfx/sfx.txt` and `sfx/music.txt` are line-per-entry text, indexed so a diff
points at the sound that changed. They are faithful and diffable rather than
hand-editable — write music in PICO-8's tracker and let it sync out.

## Two-way sync, and conflicts

`p8tool` records a hash of both sides after every sync, so it knows which one
moved. If both moved — you edited Lua in your editor *and* saved a sprite in
PICO-8 without syncing in between — it refuses to guess:

```
[14:22:31] conflict
           both the project files and build/mygame.p8 changed since the last sync.
           resolve with `p8 pack --force` (keep files) or `p8 unpack --force` (keep cart).
```

Before any reverse sync overwrites your files, the previous state is saved as a
cartridge under `.p8tool/backups/`, so nothing is ever lost to a bad merge.

## Round-trip guarantee

Exploding a cart and rebuilding it lands on the cart's *canonical* form, and
canonical form is a fixed point: pack it again and nothing changes. That is what
stops the watcher ping-ponging. `p8 doctor` verifies it on your actual cart, and
the test suite verifies it on randomised carts with every section populated.

The canonical form trims trailing all-zero rows the way PICO-8 does, so a cart
PICO-8 wrote and the same cart rebuilt here may differ by a few blank rows. Data
is identical; PICO-8 normalises it again on its next save.

## Known limits

- **`.p8.png` cartridges cannot be unpacked.** Their code is compressed inside
  the image. Open one in PICO-8 and `save mygame.p8` first — `p8 unpack` says so
  rather than producing garbage.
- **Only `.p8.png` export works from the command line.** PICO-8 0.2.7's headless
  `-export` logs html and bin exports and then writes nothing. `p8 export` checks
  whether a file actually appeared and tells you to run `export` from the PICO-8
  console instead of falsely reporting success.
- Interlaced PNGs are rejected with a message asking you to re-save. Every other
  common colour type and bit depth is handled.

## Tests

```
python tests/test_roundtrip.py     # unit + round-trip, no PICO-8 needed
python tests/test_watch.py         # drives `p8 watch` as a real subprocess
```
