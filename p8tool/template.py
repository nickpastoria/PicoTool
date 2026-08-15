"""The starter cartridge `p8 init` writes."""

FACE = [
    "00aaaa00",
    "0aaaaaa0",
    "aa0aa0aa",
    "aaaaaaaa",
    "aa0000aa",
    "0aa00aa0",
    "00aaaa00",
    "00000000",
]

CODE = """\
-- {name}
-- a pico-8 cartridge

function _init()
 x,y=64,64
 dx,dy=1.3,0.9
end

function _update60()
 x+=dx y+=dy
 if x<4 or x>123 then dx=-dx end
 if y<4 or y>123 then dy=-dy end
end

function _draw()
 cls(1)
 print("hello from an external editor",8,8,6)
 spr(1,x-4,y-4)
end
"""


def starter_cart(name):
    """Return .p8 text for a tiny bouncing-sprite demo."""
    from . import cart as cartmod

    c = cartmod.Cart()
    c.set_tabs([CODE.format(name=name)])

    pixels = [0] * (128 * 128)
    for row, bits in enumerate(FACE):
        for col, ch in enumerate(bits):
            pixels[row * 128 + 8 + col] = int(ch, 16)
    c.set_gfx(pixels)

    c.set_flags([0] * 256)
    c.set_map_tiles([[0] * 128 for _ in range(32)])
    c.set_sfx([""] * 64)
    c.set_music([])
    return c


GITATTRIBUTES = """\
# Keep line endings stable so carts do not churn between machines.
* text=auto eol=lf
*.png binary
*.p8 text eol=lf
"""

VSCODE_TASKS = """\
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "PICO-8: run",
      "detail": "Build, launch PICO-8, and sync both ways until stopped",
      "type": "shell",
      "command": "p8 run",
      "group": { "kind": "build", "isDefault": true },
      "isBackground": true,
      "problemMatcher": [],
      "presentation": { "panel": "dedicated", "clear": true }
    },
    {
      "label": "PICO-8: watch",
      "detail": "Sync both ways without launching PICO-8",
      "type": "shell",
      "command": "p8 watch",
      "isBackground": true,
      "problemMatcher": [],
      "presentation": { "panel": "dedicated", "clear": true }
    },
    {
      "label": "PICO-8: pack",
      "detail": "Build the cartridge once",
      "type": "shell",
      "command": "p8 pack",
      "problemMatcher": []
    },
    {
      "label": "PICO-8: status",
      "type": "shell",
      "command": "p8 status",
      "problemMatcher": []
    }
  ]
}
"""

VSCODE_SETTINGS = """\
{
  "files.eol": "\\n",
  "files.associations": { "*.p8": "lua" },
  "files.exclude": { "**/.p8tool": true },
  "search.exclude": { "build": true, ".p8tool": true }
}
"""

GITIGNORE = """\
# Built cartridges are generated from the files in this repo.
# Rebuild any time with `p8 pack`.
build/

# Local sync state and safety backups.
.p8tool/

# Exported bundles
*.p8.png
*.html
*.js
*.zip
"""

README = """\
# {name}

A PICO-8 cartridge, kept as ordinary files so it works with git and external editors.

## Layout

| Path | What it is |
| --- | --- |
| `src/*.lua` | One file per PICO-8 code tab, ordered by the numeric filename prefix |
| `gfx/sprites.png` | The 128x128 sprite sheet - edit in Aseprite, GIMP, Photoshop, anything |
| `gfx/flags.txt` | Sprite flags, one hex byte per sprite |
| `map/map.txt` | Map rows 0-31, one hex byte per tile |
| `sfx/sfx.txt` | Sound effects |
| `sfx/music.txt` | Music patterns |
| `build/{name}.p8` | The generated cartridge (not committed) |

## Working on it

```
p8 run        # build, launch PICO-8, and keep everything in sync
p8 watch      # sync without launching PICO-8
p8 pack       # build build/{name}.p8 once
p8 status     # what is out of sync
```

`watch` and `run` sync both ways: edit `src/*.lua` in your editor and the cart
rebuilds, or draw sprites and write sfx inside PICO-8, hit Ctrl-S, and the files
in this repo update to match.
"""
