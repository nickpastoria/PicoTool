"""Resolve `#include` while packing, and undo it while unpacking.

PICO-8 has an `#include` of its own, but it is no use here. It resolves paths as
the cartridge loads, relative to the cartridge, and it only reaches files inside
PICO-8's own carts folder - so a cart in `build/` cannot see `src/`. Worse, code
PICO-8 pulls in that way never enters the editor buffer, so anything you change
in there has nowhere to go on the way back out.

So p8tool resolves includes itself, before PICO-8 ever sees the cart. This:

    #include lib/util.lua

becomes, in the built cartridge:

    --#include lib/util.lua
    function lerp(a,b,t) return a+(b-a)*t end
    --#end lib/util.lua

Unpacking reads those markers in reverse: the body is written back out to
`src/lib/util.lua` and the region collapses to the one-line directive again.

Each file is inlined at most once per cartridge. PICO-8 tabs are one chunk, so
a second copy of a library would only redefine what the first copy already
defined - and a library reached by two different tabs is the normal case, not a
mistake. Later directives naming a file that is already in the cart become a
single marker line instead:

    --#included lib/util.lua

which unpacking turns back into `#include lib/util.lua`, without writing the
file a second time. Order decides which one wins: tabs left to right, lines top
to bottom, depth first.

Both directions are fixed points - expanding a collapsed tab and collapsing an
expanded one land exactly where they started - which is what stops the watcher
ping-ponging. It also means an edit you make *inside* an included region while
in PICO-8 lands in the file it came from, instead of being flattened into the
tab that included it.

Paths are relative to the file holding the directive, and must stay under
`src/`: that is the directory p8tool watches and hashes, so an include living
outside it would be a source file that no change ever reaches.
"""

import os
import re

DIRECTIVE = re.compile(r"^[ \t]*#include[ \t]+(\S.*?)[ \t]*$")
BEGIN = re.compile(r"^--#include[ \t]+(\S.*?)[ \t]*$")
END = re.compile(r"^--#end[ \t]+(\S.*?)[ \t]*$")
AGAIN = re.compile(r"^--#included[ \t]+(\S.*?)[ \t]*$")

TAB_SEPARATOR = "-->8"
MAX_DEPTH = 16


def _clean(path):
    """Normalise a path as written in a directive: quotes off, forward slashes."""
    p = path.strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
        p = p[1:-1].strip()
    return p.replace("\\", "/")


def _under(path, root):
    a = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(root))
    return a == b or a.startswith(b + os.sep)


def resolve(rel, base_dir, src_root):
    """Absolute path for `rel`, or None if it does not stay under `src_root`."""
    if not rel or os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
        return None
    full = os.path.normpath(os.path.join(base_dir, rel))
    return full if _under(full, src_root) else None


def expand(text, base_dir, src_root, read, origin="", reject=None, seen=None,
           _stack=()):
    """Inline every `#include` in `text`. Returns (text, warnings).

    `read(path)` returns a file's text; `reject(path)` optionally returns a
    reason this file may not be included. Anything that cannot be expanded
    keeps its directive line verbatim - the cart is then slightly wrong, but
    the source survives the round trip intact and the next pack tries again.

    `seen` is the set of files already inlined; a directive naming one of them
    becomes a `--#included` marker rather than a second copy. Pass the same set
    to every tab of a cart - the tabs share one Lua chunk, so including a file
    from two of them would be redefining it, not defining it twice over.
    """
    out, warnings = [], []
    where = "%s: " % origin if origin else ""
    if seen is None:
        seen = set()

    for line in text.split("\n"):
        m = DIRECTIVE.match(line)
        if not m:
            out.append(line)
            continue

        rel = _clean(m.group(1))
        full = resolve(rel, base_dir, src_root)
        why = None

        if full is None:
            why = "must name a file under src/, without a leading / or .."
        elif not os.path.isfile(full):
            why = "no such file (looked in %s)" % os.path.dirname(full)
        elif reject is not None and reject(full) is not None:
            why = reject(full)
        elif os.path.normcase(full) in _stack:
            why = "includes itself, directly or through another file"
        elif len(_stack) >= MAX_DEPTH:
            why = "nested more than %d deep" % MAX_DEPTH

        # Already in the cart, and resolvable: leave a marker so unpacking can
        # put the directive back, and move on. This is the ordinary shape of a
        # library two tabs both need, so it is not worth a warning.
        if why is None and os.path.normcase(full) in seen:
            out.append("--#included %s" % rel)
            continue

        if why is None:
            body = read(full).replace("\r\n", "\n")
            if body.endswith("\n"):
                body = body[:-1]
            if any(l.rstrip() == TAB_SEPARATOR for l in body.split("\n")):
                why = "contains a `%s` tab separator, which would split the tab" % TAB_SEPARATOR

        if why is not None:
            warnings.append("%s`#include %s` %s" % (where, rel, why))
            out.append(line)
            continue

        seen.add(os.path.normcase(full))
        body, w = expand(body, os.path.dirname(full), src_root, read,
                         origin=rel, reject=reject, seen=seen,
                         _stack=_stack + (os.path.normcase(full),))
        warnings += w

        out.append("--#include %s" % rel)
        out.extend(body.split("\n"))
        out.append("--#end %s" % rel)

    return "\n".join(out), warnings


def collapse(text, base_dir, src_root, write, origin=""):
    """Fold marked regions in `text` back out into their own files.

    `write(path, text)` persists one. Returns (text, warnings).
    """
    warnings = []
    lines = text.split("\n")
    out, _, _ = _collapse(lines, 0, None, base_dir, src_root, write,
                          "%s: " % origin if origin else "", warnings)
    return "\n".join(out), warnings


def _collapse(lines, i, want_end, base_dir, src_root, write, where, warnings):
    """Consume lines from `i`. Returns (lines out, next index, end line seen)."""
    out = []
    while i < len(lines):
        line = lines[i]

        m = END.match(line)
        if m is not None and want_end is not None and _clean(m.group(1)) == want_end:
            return out, i + 1, line

        # A file inlined somewhere else in the cart: restore the directive, and
        # write nothing - the region that does hold the body owns the file.
        m = AGAIN.match(line)
        if m is not None:
            rel = _clean(m.group(1))
            if resolve(rel, base_dir, src_root) is None:
                warnings.append("%skept `%s` as text: it does not name a file under src/"
                                % (where, line.strip()))
                out.append(line)
            else:
                out.append("#include %s" % rel)
            i += 1
            continue

        m = BEGIN.match(line)
        if m is None:
            out.append(line)
            i += 1
            continue

        rel = _clean(m.group(1))
        full = resolve(rel, base_dir, src_root)
        body, i, end_line = _collapse(
            lines, i + 1, rel, os.path.dirname(full) if full else base_dir,
            src_root, write, where, warnings)

        # A region we will not write to - unterminated, or pointing somewhere we
        # refuse to put a file - is kept exactly as it came in. Losing it would
        # lose whatever the user just wrote in PICO-8.
        if end_line is None or full is None:
            if full is None:
                warnings.append("%skept `%s` as text: it does not name a file under src/"
                                % (where, line.strip()))
            else:
                warnings.append("%skept `%s` as text: no matching `--#end %s`"
                                % (where, line.strip(), rel))
            out.append(line)
            out.extend(body)
            if end_line is not None:
                out.append(end_line)
            continue

        write(full, "\n".join(body) + "\n")
        out.append("#include %s" % rel)

    return out, i, None
