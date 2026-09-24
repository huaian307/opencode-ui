#!/usr/bin/env python3
"""Start OpenCode (if it is not already running) and then the opencode-ui watcher.

This is what the "OpenCode (with panel)" desktop shortcut runs. It is meant to
run under pythonw.exe so that no console window flashes.

ASCII-only on purpose (see AGENTS.md pitfall #28 for the .vbs/.ps1 variant).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WATCH = os.path.join(ROOT, "backend", "watch.py")

# Candidate paths of the OpenCode desktop executable; first existing one wins.
OPENCODE_CANDIDATES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                 "@opencodedesktop", "OpenCode.exe"),
    r"C:\Users\TWG\AppData\Local\Programs\@opencodedesktop\OpenCode.exe",
]

CREATE_NO_WINDOW = 0x08000000


def find_opencode():
    for p in OPENCODE_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def opencode_running():
    """Cheap, dependency-free check (tasklist is filtered by image name only)."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq OpenCode.exe", "/NH"],
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        ).decode(errors="ignore")
        return "OpenCode.exe" in out
    except Exception:
        return False


def main():
    exe = find_opencode()
    if exe and not opencode_running():
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
    # The watcher exits by itself if another one is already running (port lock).
    if os.path.isfile(WATCH):
        subprocess.Popen([sys.executable, WATCH], cwd=ROOT,
                         creationflags=CREATE_NO_WINDOW)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
