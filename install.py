#!/usr/bin/env python3
"""Install (or update / remove) the multi-review skill into every AI tool found on this machine.

Only the skill itself is copied: SKILL.md, PROTOCOL.md and scripts/. Repo files
(.git, tests, README, examples) never end up in a skill directory.

  python install.py                 install or update for every detected tool
  python install.py --dry-run       show what would happen
  python install.py --target codex  only one tool (repeatable)
  python install.py --uninstall     remove the skill from the detected tools
  python install.py --list          show tools, paths and installed versions
"""
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

NAME = "multi-review"
REPO = Path(__file__).resolve().parent
FILES = ["SKILL.md", "PROTOCOL.md", "scripts/multi_loop.py", "scripts/chat_events.py", "scripts/chat_view.py",
         "scripts/chat_hub.py", "scripts/chat_page.html"]
# Thin slash-command routers (the rules stay in SKILL.md/PROTOCOL.md), only for tools that load them.
COMMANDS = {"claude": ["commands/multi-review-join.md"]}


def command_dir(tool):
    return {"claude": HOME / ".claude" / "commands"}.get(tool)
HOME = Path.home()
# tool -> (folder that proves the tool is installed, skill directory)
TOOLS = {
    "claude": (HOME / ".claude", HOME / ".claude" / "skills" / NAME),
    "antigravity": (HOME / ".gemini", HOME / ".gemini" / "config" / "skills" / NAME),
    "codex": (HOME / ".codex", HOME / ".codex" / "skills" / NAME),
    "agents": (HOME / ".agents", HOME / ".agents" / "skills" / NAME),
}


def version_of(script):
    try:
        m = re.search(r'^__version__ = "([^"]+)"', script.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else "unknown"


def selected(targets):
    if targets:
        unknown = sorted(set(targets) - set(TOOLS))
        if unknown:
            sys.exit(f"ERROR: unknown target(s) {unknown}; choose from {sorted(TOOLS)}")
        return {t: TOOLS[t] for t in targets}
    return {t: v for t, v in TOOLS.items() if v[0].is_dir()}


def install(tool, dest, dry):
    old = version_of(dest / "scripts" / "multi_loop.py")
    new = version_of(REPO / "scripts" / "multi_loop.py")
    action = "install" if old is None else f"update {old} -> {new}"
    print(f"{tool:<12} {action:<24} {dest}")
    if dry:
        return
    if dest.exists():
        # Back up outside every skills folder: a second SKILL.md there would load as a duplicate skill.
        backup = HOME / f".{NAME}" / "backups" / f"{tool}-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(dest, backup, ignore=shutil.ignore_patterns("__pycache__"))
    # Overwrite in place, then drop leftovers: an rmtree that fails halfway (Windows: a terminal sitting
    # in the folder, an open editor) would leave a half-deleted skill.
    for rel in FILES:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, target)
    keep = {(dest / rel).resolve() for rel in FILES}
    for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_file() and path.resolve() not in keep:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError as e:
            print(f"{'':<12} warning: could not remove leftover {path} ({e})")


def install_commands(tool, dry):
    for rel in COMMANDS.get(tool, []):
        target = command_dir(tool) / Path(rel).name
        print(f"{'':<12} {'command':<24} {target}")
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, target)


def uninstall(tool, dest, dry):
    for rel in COMMANDS.get(tool, []):
        target = command_dir(tool) / Path(rel).name
        if target.exists():
            print(f"{tool:<12} {'remove command':<24} {target}")
            if not dry:
                target.unlink()
    if not dest.exists():
        print(f"{tool:<12} {'not installed':<24} {dest}")
        return
    print(f"{tool:<12} {'remove':<24} {dest}")
    if not dry:
        shutil.rmtree(dest)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version",
                   version=f"multi-review {version_of(REPO / 'scripts' / 'multi_loop.py')} (install.py)")
    p.add_argument("--target", action="append", help=f"only this tool: {', '.join(TOOLS)}")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()
    missing = [f for f in FILES + [c for cs in COMMANDS.values() for c in cs] if not (REPO / f).is_file()]
    if missing:
        sys.exit(f"ERROR: run this from a full checkout; missing {missing}")
    if args.list:
        for tool, (home, dest) in TOOLS.items():
            state = version_of(dest / "scripts" / "multi_loop.py") or "-"
            print(f"{tool:<12} tool={'yes' if home.is_dir() else 'no ':<4} installed={state:<8} {dest}")
        return
    tools = selected(args.target)
    if not tools:
        sys.exit("No supported tool found (~/.claude, ~/.gemini, ~/.codex, ~/.agents). Use --target to force one.")
    for tool, (_, dest) in tools.items():
        if args.uninstall:
            uninstall(tool, dest, args.dry_run)
        else:
            install(tool, dest, args.dry_run)
            install_commands(tool, args.dry_run)
    if args.dry_run:
        print("(dry run, nothing changed)")


if __name__ == "__main__":
    main()
