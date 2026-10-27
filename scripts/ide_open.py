"""Open an IDE agent's window on the review's project folder.

Antigravity asks the user before every write outside its workspace, and a review loop writes on every
tick (its own md file, <root>/multi-review/.state/). Opened on the project root, the session folder is
inside its workspace and the prompts stop.
"""
import os
import shutil
import subprocess
from pathlib import Path

# CLI names on PATH, then install locations under %LOCALAPPDATA%\Programs (Windows installers skip PATH).
IDES = {
    "antigravity": (["antigravity-ide", "antigravity"],
                    ["Antigravity IDE/bin/antigravity-ide.cmd", "Antigravity/bin/antigravity.cmd"]),
}
JOIN_TEXT = "multi-review oturumuna katil"


def find_cli(ide):
    names, installs = IDES[ide]
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    base = os.environ.get("LOCALAPPDATA")
    for rel in installs if base else []:
        p = Path(base) / "Programs" / rel
        if p.is_file():
            return str(p)
    return None


def inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def outside_hint(root, script):
    """Antigravity joined from another folder: every tick will ask the user for permission."""
    return (f"WORKSPACE_OUTSIDE_ROOT: your workspace is not {Path(root).resolve()}, so the IDE asks the user "
            f"before every write of this loop. Tell the user to run once: python \"{script}\" --root "
            f"\"{Path(root).resolve()}\" open-ide antigravity (it opens this folder in Antigravity), then "
            f"to type \"{JOIN_TEXT}\" in that window's agent chat.")


def open_ide(root, ide="antigravity"):
    """Start the IDE on root, detached so it outlives this command. True when it started."""
    cli = find_cli(ide)
    if not cli:
        print(f"IDE_NOT_FOUND: no {ide} command on PATH or under %LOCALAPPDATA%\\Programs; "
              f"open {Path(root).resolve()} in {ide} by hand (File > Open Folder).")
        return False
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([cli, str(Path(root).resolve())], **kw)
    except OSError as e:
        print(f"IDE_NOT_STARTED: {cli}: {e}")
        return False
    print(f"IDE_OPENED {ide} -> {Path(root).resolve()}. Tell the user to type \"{JOIN_TEXT}\" in that "
          "window's agent chat (its workspace is now the project, so no write prompts).")
    return True


def cmd_open_ide(args):
    open_ide(args.root, args.ide)
