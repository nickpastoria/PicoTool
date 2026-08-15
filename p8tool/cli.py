"""Command line interface."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import cart as cartmod
from . import hotkey
from . import pico8
from . import sync as syncmod
from . import template
from .project import MANIFEST, Project, ProjectError


def out(msg=""):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def warn(msg):
    out("  ! " + msg)


def report(result, prefix="  "):
    for m in result.messages:
        out(prefix + m)
    for w in result.warnings:
        warn(w)


def _stamp():
    return time.strftime("%H:%M:%S")


# --------------------------------------------------------------------- init

def _write_new(path, body):
    """Write a scaffold file, never clobbering something the user already has."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def _scaffold(root, name, source, vscode=True):
    """Lay out a fresh project around `source` and return it, packed."""
    os.makedirs(root, exist_ok=True)
    _write_new(os.path.join(root, MANIFEST), json.dumps({
        "name": name,
        "cart": "build/%s.p8" % name,
        "header": source.header,
        "version": source.version,
        "tabs": [],
    }, indent=2) + "\n")

    project = Project(root)
    project.unpack(source)

    _write_new(os.path.join(root, ".gitignore"), template.GITIGNORE)
    _write_new(os.path.join(root, ".gitattributes"), template.GITATTRIBUTES)
    _write_new(os.path.join(root, "README.md"), template.README.format(name=name))
    if vscode:
        _write_new(os.path.join(root, ".vscode", "tasks.json"), template.VSCODE_TASKS)
        _write_new(os.path.join(root, ".vscode", "settings.json"), template.VSCODE_SETTINGS)

    return project, syncmod.Syncer(project).force_pack()


def cmd_init(args):
    name = args.name or os.path.basename(os.path.abspath(os.getcwd()))
    root = os.getcwd() if args.here else os.path.abspath(name)

    if not args.here:
        if os.path.exists(root) and os.listdir(root):
            out("error: %s already exists and is not empty" % root)
            return 1
    elif os.path.exists(os.path.join(root, MANIFEST)):
        out("error: %s already contains a p8project.json" % root)
        return 1

    source = cartmod.Cart.load(args.from_cart) if args.from_cart else template.starter_cart(name)
    _, result = _scaffold(root, name, source, vscode=not args.no_vscode)

    out("created project %s" % root)
    report(result)

    if not args.no_git and not os.path.isdir(os.path.join(root, ".git")):
        try:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            out("  initialised a git repository")
        except (OSError, subprocess.CalledProcessError):
            warn("could not run `git init` (is git installed?)")

    out()
    out("next:")
    if not args.here:
        out("  cd %s" % name)
    out("  p8 run          build, launch PICO-8, and sync both ways")
    return 0


# ------------------------------------------------------------------- unpack

def cmd_unpack(args):
    if args.cart and not os.path.exists(args.cart):
        out("error: no such cartridge: %s" % args.cart)
        return 1

    if args.cart and not _in_project(args.into):
        # A standalone cart: explode it into a fresh project of its own.
        base = os.path.basename(args.cart)
        for suffix in (".p8.png", ".p8"):
            if base.lower().endswith(suffix):
                base = base[:-len(suffix)]
                break
        root = os.path.abspath(args.into or base)
        name = os.path.basename(root)
        _, result = _scaffold(root, name, cartmod.Cart.load(args.cart))
        out("unpacked %s into %s" % (args.cart, root))
        report(result)
        return 0

    project = Project.find(args.into)
    syncer = syncmod.Syncer(project)

    if args.cart:
        source = cartmod.Cart.load(args.cart)
        for w in project.unpack(source):
            warn(w)
        result = syncer.force_pack()
        out("unpacked %s into %s" % (args.cart, project.root))
        report(result)
        return 0

    if args.force:
        report(syncer.force_unpack())
        return 0
    return _run_tick(syncer, prefer="cart" if args.force else None)


def _in_project(start):
    try:
        Project.find(start)
        return True
    except ProjectError:
        return False


# --------------------------------------------------------------- pack / sync

def cmd_pack(args):
    project = Project.find()
    syncer = syncmod.Syncer(project)
    if args.force:
        report(syncer.force_pack())
        return 0
    return _run_tick(syncer, prefer=None)


def cmd_sync(args):
    project = Project.find()
    return _run_tick(syncmod.Syncer(project), prefer=None)


def _run_tick(syncer, prefer):
    result = syncer.tick(prefer=prefer)
    if result.kind == syncmod.IDLE:
        out("  already in sync")
    report(result)
    return 2 if result.kind == syncmod.CONFLICT else 0


# ------------------------------------------------------------------- status

def cmd_status(args):
    project = Project.find()
    syncer = syncmod.Syncer(project)
    state = syncer.load_state()
    text, src_hash, warnings = syncer.build()
    cart_hash = syncer.cart_hash()

    out("project   %s" % project.root)
    out("cart      %s%s" % (project.manifest["cart"],
                            "" if cart_hash else "   (not built yet)"))
    out("tabs      %d  (%s)" % (len(project.manifest["tabs"]),
                                ", ".join(project.manifest["tabs"]) or "-"))
    includes = project.include_files()
    if includes:
        shown = ", ".join(includes[:4])
        if len(includes) > 4:
            shown += ", +%d more" % (len(includes) - 4)
        out("includes  %d  (%s)" % (len(includes), shown))

    src_moved = state.get("src") != src_hash
    cart_moved = cart_hash is not None and state.get("cart") != cart_hash

    if not state:
        out("sync      never synced - run `p8 pack`")
    elif src_moved and cart_moved:
        out("sync      CONFLICT: both sides changed")
        out("          `p8 pack --force` keeps the files, `p8 unpack --force` keeps the cart")
    elif src_moved:
        out("sync      project files are ahead - run `p8 pack`")
    elif cart_moved:
        out("sync      cartridge is ahead - run `p8 unpack`")
    else:
        out("sync      up to date")

    for w in warnings:
        warn(w)

    try:
        out("pico-8    %s" % pico8.locate(project.manifest.get("pico8")))
    except pico8.Pico8NotFound:
        out("pico-8    not found (set PICO8_PATH)")
    return 0


# -------------------------------------------------------------------- watch

def cmd_watch(args):
    project = Project.find()
    syncer = syncmod.Syncer(project)
    return _watch_loop(syncer, project, launch=False, interval=args.interval, reload_=False)


def cmd_run(args):
    project = Project.find()
    syncer = syncmod.Syncer(project)
    if args.no_watch:
        report(syncer.tick(prefer="src"))
        exe = pico8.locate(project.manifest.get("pico8"))
        return pico8.run(exe, ["-run", project.cart_path], wait=True)
    return _watch_loop(syncer, project, launch=True, interval=args.interval,
                       reload_=not args.no_reload, in_place=not args.relaunch)


def _watch_loop(syncer, project, launch, interval, reload_, in_place=True):
    proc = None
    exe = None
    if launch:
        exe = pico8.locate(project.manifest.get("pico8"))

    out("watching %s" % project.root)
    out("  cart: %s" % project.manifest["cart"])
    if launch:
        how = "reload in place" if in_place else "relaunch"
        out("  pico-8: %s%s" % (exe, "  (auto-%s on edit)" % how if reload_ else ""))
    out("  edit files here, or save inside PICO-8 with Ctrl-S - both directions sync")
    out("  Ctrl-C to stop")
    out()

    def start():
        nonlocal proc
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen([exe, "-run", project.cart_path], **kwargs)

    def relaunch():
        nonlocal proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        start()

    fell_back = False

    def refresh():
        """Pick the new cart up in the window that is already open, if we can."""
        nonlocal fell_back
        if not launch:
            return
        if proc is None or proc.poll() is not None:
            start()
            return
        if in_place:
            try:
                # PICO-8 re-reads a cart that changed on disk when it gets
                # Ctrl-R, which keeps the window exactly where it is.
                hotkey.send_reload(proc.pid)
                return
            except hotkey.HotkeyError as exc:
                if not fell_back:
                    warn("could not reload in place (%s) - relaunching instead" % exc)
                    fell_back = True
        relaunch()

    reported_conflict = False
    try:
        result = syncer.tick(prefer=None)
        if result.kind == syncmod.CONFLICT:
            out("[%s] conflict" % _stamp())
            report(result, "         ")
            reported_conflict = True
        else:
            report(result, "[%s] " % _stamp())
        syncer.changed_since_last_look()
        refresh()

        while True:
            time.sleep(interval)
            if launch and proc is not None and proc.poll() is not None:
                out("[%s] PICO-8 closed - stopping" % _stamp())
                break
            if not syncer.changed_since_last_look():
                continue
            try:
                result = syncer.tick(prefer=None)
            except Exception as exc:
                out("[%s] %s" % (_stamp(), exc))
                continue

            if result.kind == syncmod.CONFLICT:
                if not reported_conflict:
                    out("[%s] conflict" % _stamp())
                    report(result, "         ")
                    reported_conflict = True
                continue
            reported_conflict = False

            if result.kind == syncmod.IDLE:
                for w in result.warnings:
                    warn(w)
                continue

            report(result, "[%s] " % _stamp())
            if result.kind == syncmod.PACKED and reload_:
                refresh()
            # Refresh the fingerprint so our own writes do not re-trigger.
            syncer.changed_since_last_look()
    except KeyboardInterrupt:
        out()
        out("stopped")
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
    return 0


# ------------------------------------------------------------------- export

def cmd_export(args):
    project = Project.find()
    syncer = syncmod.Syncer(project)
    report(syncer.tick(prefer="src"))

    exe = pico8.locate(project.manifest.get("pico8"))
    target = os.path.abspath(args.target)
    outdir = os.path.dirname(target) or "."
    os.makedirs(outdir, exist_ok=True)

    # PICO-8 sandboxes writes and ignores absolute output paths, so run it with
    # its working directory set to the destination and hand it a bare filename.
    basename = os.path.basename(target)
    param = " ".join([basename] + list(args.extra))
    before = set(os.listdir(outdir))

    code = subprocess.call([exe, project.cart_path, "-export", param], cwd=outdir)
    produced = sorted(set(os.listdir(outdir)) - before)

    if produced:
        for name in produced:
            out("  exported %s" % os.path.join(outdir, name))
        return 0

    out("  PICO-8 produced no file for %s" % basename)
    if basename.endswith((".html", ".bin", ".zip")):
        out("  PICO-8 0.2.7 only exports .p8.png from the command line;")
        out("  html and bin exports have to be run from the PICO-8 console:")
        out("    load %s" % project.cart_path)
        out("    export %s" % basename)
    return code or 1


# --------------------------------------------------------------------- tabs

def cmd_tab(args):
    project = Project.find()
    files = project._tab_files()

    if args.action == "list":
        for i, f in enumerate(files):
            out("  %d  src/%s" % (i, f))
        if not files:
            out("  (no tabs)")
        return 0

    if args.action == "new":
        name = args.name or "tab"
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        path = project.path("src", "%02d_%s.lua" % (len(files), safe))
        if os.path.exists(path):
            out("error: %s already exists" % path)
            return 1
        os.makedirs(project.path("src"), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("-- %s\n\n" % name)
        out("  created %s" % os.path.relpath(path, project.root))
        report(syncmod.Syncer(project).force_pack())
        return 0
    return 1


# ------------------------------------------------------------------- doctor

def cmd_doctor(args):
    ok = True
    out("checking environment")
    try:
        out("  pico-8:  %s" % pico8.locate())
    except pico8.Pico8NotFound as exc:
        out("  pico-8:  NOT FOUND")
        warn(str(exc).splitlines()[-1])
        ok = False

    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        out("  git:     available")
    except (OSError, subprocess.CalledProcessError):
        out("  git:     not found (version control commands will not work)")

    try:
        project = Project.find()
    except ProjectError:
        out("  project: none in this directory")
        return 0 if ok else 1

    out("  project: %s" % project.root)
    syncer = syncmod.Syncer(project)
    text, src_hash, warnings = syncer.build()
    for w in warnings:
        warn(w)

    # Round-trip: cart -> exploded files -> cart must land back on canonical form.
    reparsed = cartmod.Cart.parse(text)
    expected = reparsed.canonical()
    scratch = tempfile.mkdtemp(prefix="p8tool-doctor-")
    try:
        with open(os.path.join(scratch, MANIFEST), "w", encoding="utf-8") as fh:
            json.dump(dict(project.manifest, tabs=[]), fh)
        probe = Project(scratch)
        probe.unpack(reparsed)
        again, _ = probe.pack()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if again.to_text() == expected.to_text():
        out("  roundtrip: stable (no data lost through unpack/pack)")
    else:
        out("  roundtrip: MISMATCH - please report this cart")
        _diff_sections(expected, again)
        ok = False
    return 0 if ok else 1


def _diff_sections(a, b):
    for name in set(a.sections) | set(b.sections):
        if a.sections.get(name) != b.sections.get(name):
            warn("section __%s__ differs" % name)


# --------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="p8",
        description="PICO-8 development with real version control and external editors.",
    )
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("init", help="create a new project")
    s.add_argument("name", nargs="?", help="project (and cartridge) name")
    s.add_argument("--here", action="store_true", help="use the current directory")
    s.add_argument("--from", dest="from_cart", metavar="CART.P8",
                   help="seed the project from an existing cartridge")
    s.add_argument("--no-git", action="store_true", help="skip `git init`")
    s.add_argument("--no-vscode", action="store_true", help="skip the .vscode task files")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("unpack", help="explode a cartridge into editable files")
    s.add_argument("cart", nargs="?", help="cartridge to unpack (default: this project's)")
    s.add_argument("--into", metavar="DIR", help="target project directory")
    s.add_argument("--force", action="store_true",
                   help="overwrite project files even if they also changed")
    s.set_defaults(func=cmd_unpack)

    s = sub.add_parser("pack", help="build the cartridge from the project files")
    s.add_argument("--force", action="store_true",
                   help="overwrite the cartridge even if it also changed")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("sync", help="sync whichever side changed")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("status", help="show what is out of sync")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("watch", help="sync continuously")
    s.add_argument("--interval", type=float, default=0.4, help="poll interval in seconds")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("run", help="build, launch PICO-8, and sync continuously")
    s.add_argument("--interval", type=float, default=0.4, help="poll interval in seconds")
    s.add_argument("--no-reload", action="store_true",
                   help="do not reload PICO-8 when the code changes")
    s.add_argument("--relaunch", action="store_true",
                   help="restart PICO-8 on each change instead of reloading it in place")
    s.add_argument("--no-watch", action="store_true", help="just launch PICO-8 once")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("export", help="export via PICO-8 (html, bin, p8.png, ...)")
    s.add_argument("target", help="output file, e.g. dist/game.html or dist/game.bin")
    s.add_argument("extra", nargs="*", help="extra PICO-8 export flags")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("tab", help="list or add code tabs")
    s.add_argument("action", choices=["list", "new"])
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_tab)

    s = sub.add_parser("doctor", help="check the setup and verify a lossless round-trip")
    s.set_defaults(func=cmd_doctor)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (ProjectError, cartmod.CartError, pico8.Pico8NotFound) as exc:
        out("error: %s" % exc)
        return 1
    except FileNotFoundError as exc:
        out("error: file not found: %s" % exc)
        return 1
    except KeyboardInterrupt:
        return 130
