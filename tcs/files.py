"""Finding workbooks on your computer: numbered folder browsers, paste-a-path and folder scans."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from . import ui

WORKBOOK_SUFFIXES = (".twbx", ".twb")
TABLEAU_REPOSITORY = Path.home() / "Documents" / "My Tableau Repository" / "Workbooks"


def default_start_dir(configured: Optional[Path] = None) -> Path:
    """DEFAULT_WORKBOOK_DIR, else Tableau's own Workbooks folder, else the current folder."""
    if configured:
        if configured.is_dir():
            return configured
        ui.warn(f"DEFAULT_WORKBOOK_DIR doesn't exist: {display_path(configured)}")
    if TABLEAU_REPOSITORY.is_dir():
        return TABLEAU_REPOSITORY
    return Path.cwd()


def clean_path_input(raw: str) -> Path:
    """Turn typed or drag-and-dropped text into a Path.

    Handles surrounding quotes, "~", $VARS and the backslash-escaped spaces
    that macOS Terminal adds when you drag a file in.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    elif os.name != "nt":
        text = re.sub(r"\\(.)", r"\1", text)
    return Path(os.path.expandvars(text)).expanduser()


def validate_workbook_file(path: Path) -> Path:
    if not path.exists():
        raise ValueError(f"File not found: {display_path(path)}")
    if not path.is_file():
        raise ValueError(f"Not a file: {display_path(path)}")
    if path.suffix.lower() not in WORKBOOK_SUFFIXES:
        raise ValueError(f"Not a Tableau workbook (.twb or .twbx): {path.name}")
    return path.resolve()


def pick_workbook(start_dir: Path) -> Path:
    """Browse folders with a numbered list; folders first, then .twb/.twbx files."""
    folder = start_dir.expanduser().resolve()
    while True:
        ui.heading("Choose a workbook")
        ui.detail(f"  Folder: {display_path(folder)}")
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            ui.warn(f"Can't open that folder ({exc.strerror}). Going up one level.")
            folder = folder.parent
            continue

        dirs = [e for e in entries if e.is_dir() and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and e.suffix.lower() in WORKBOOK_SUFFIXES]
        labels = [f"{d.name}/" for d in dirs] + [f"{f.name}  {ui.style(file_summary(f), 'dim')}" for f in files]
        if not files:
            ui.detail("  (no .twb or .twbx files in this folder)")

        bottom = [("u", "Up one folder"), ("p", "Paste a path to a workbook or folder"), ("q", "Quit")]
        choice = ui.menu(labels, bottom=bottom)

        if choice == "u":
            folder = folder.parent
        elif choice == "p":
            path = clean_path_input(ui.ask_required("Path"))
            if path.is_dir():
                folder = path.resolve()
                continue
            try:
                return validate_workbook_file(path)
            except ValueError as exc:
                ui.warn(str(exc))
        else:
            assert isinstance(choice, int)
            if choice < len(dirs):
                folder = dirs[choice]
            else:
                return files[choice - len(dirs)].resolve()


def pick_folder(start_dir: Path) -> Path:
    """Browse folders with a numbered list; 0 chooses the folder you're in."""
    folder = start_dir.expanduser().resolve()
    while True:
        ui.heading("Choose a folder to scan")
        ui.detail(f"  Folder: {display_path(folder)}")
        try:
            dirs = sorted(
                (e for e in folder.iterdir() if e.is_dir() and not e.name.startswith(".")),
                key=lambda p: p.name.casefold(),
            )
        except OSError as exc:
            ui.warn(f"Can't open that folder ({exc.strerror}). Going up one level.")
            folder = folder.parent
            continue

        top = [("0", ui.style(f"Scan this folder and its subfolders ({folder.name or folder})", "bold"))]
        bottom = [("u", "Up one folder"), ("p", "Paste a path to a folder"), ("q", "Quit")]
        choice = ui.menu([f"{d.name}/" for d in dirs], top=top, bottom=bottom)

        if choice == "0":
            return folder
        if choice == "u":
            folder = folder.parent
        elif choice == "p":
            path = clean_path_input(ui.ask_required("Path"))
            if path.is_dir():
                folder = path.resolve()
            else:
                ui.warn(f"Not a folder: {display_path(path)}")
        else:
            assert isinstance(choice, int)
            folder = dirs[choice]


def validate_folder(path: Path) -> Path:
    if not path.is_dir():
        raise ValueError(f"Not a folder: {display_path(path)}")
    return path.resolve()


def find_workbook_files(folder: Path, suffixes: Sequence[str] = (".twbx",)) -> List[Path]:
    """Every workbook under ``folder``, at any depth, sorted by path within the folder.

    Hidden folders (.git, .Trash and the like) are skipped, as are hidden files and
    "~" lock and temp files. Folders you can't read are skipped quietly.
    """
    found: List[Path] = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [
            Path(root) / n
            for n in names
            if n.lower().endswith(tuple(suffixes)) and not n.startswith((".", "~"))
        ]
    return sorted(found, key=lambda p: str(p.relative_to(folder)).casefold())


def file_summary(path: Path) -> str:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
    return f"{human_size(stat.st_size)}, modified {modified}"


def safe_name(text: str, limit: int = 100) -> str:
    """A name usable as a file or folder name on Windows, macOS and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")
    return cleaned[:limit].rstrip(" .") or "unnamed"


def human_size(size: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def display_path(path: Path) -> str:
    """Show paths under your home folder as ~/..."""
    try:
        return "~/" + str(path.expanduser().resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)
