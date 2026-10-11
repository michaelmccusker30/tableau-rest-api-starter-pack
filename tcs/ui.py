"""Terminal helpers shared by every script: colors, prompts and numbered menus."""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, List, Optional, Sequence, Tuple, Union

MenuKey = Tuple[str, str]  # (key the user types, label)

SELECTION_HELP = "e.g. 3 / 1,4,7 / 2-5 / a for all, q to quit"


class Cancelled(Exception):
    """The user quit, pressed Ctrl-C or closed the input stream."""


# ------------------------------------------------------------------ styling

_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def _color_enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def _unicode_ok() -> bool:
    return (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "").startswith("utf")


def symbol(fancy: str, plain: str) -> str:
    """Use a Unicode symbol when the terminal can print it, else an ASCII fallback."""
    return fancy if _unicode_ok() else plain


def style(text: str, *styles: str) -> str:
    if not styles or not _color_enabled():
        return text
    return f"\033[{';'.join(_CODES[s] for s in styles)}m{text}\033[0m"


def heading(text: str) -> None:
    print()
    print(style(text, "bold"))


def info(text: str = "") -> None:
    print(text)


def detail(text: str) -> None:
    print(style(text, "dim"))


def success(text: str) -> None:
    print(style(f"{symbol('✓', '+')} {text}", "green"))


def warn(text: str) -> None:
    print(style(f"{symbol('!', '!')} {text}", "yellow"))


def error(text: str) -> None:
    print(style(f"{symbol('✗', 'x')} {text}", "red"), file=sys.stderr)


def table(rows: Sequence[Tuple[str, str]], indent: int = 2) -> None:
    """Print aligned label/value rows. Continuation lines in a value stay aligned."""
    width = max(len(label) for label, _ in rows)
    pad = " " * indent
    for label, value in rows:
        lines = str(value).splitlines() or [""]
        print(f"{pad}{style(label.ljust(width), 'dim')}  {lines[0]}")
        for line in lines[1:]:
            print(f"{pad}{' ' * width}  {line}")


# ------------------------------------------------------------------ prompts

def ask(prompt: str, default: Optional[str] = None) -> str:
    """Read one line. Returns the default when the user just presses Enter."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{style('?', 'cyan')} {prompt}{suffix}: ").strip()
    except EOFError:
        raise Cancelled() from None
    return value or (default or "")


def ask_required(prompt: str, default: Optional[str] = None) -> str:
    while True:
        value = ask(prompt, default)
        if value:
            return value
        warn("A value is required.")


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        value = ask(f"{prompt} ({hint})").lower()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        warn("Please answer y or n.")


def menu(
    items: Sequence[str],
    *,
    top: Sequence[MenuKey] = (),
    bottom: Sequence[MenuKey] = (),
    prompt: str = "Select",
) -> Union[int, str]:
    """Show a numbered menu.

    ``items`` are numbered from 1. ``top`` and ``bottom`` add lettered (or "0")
    choices above and below them. Returns the 0-based index of a numbered item,
    or the key of a top/bottom choice. Choosing "q" raises Cancelled.
    """
    keys: List[MenuKey] = [*top, *bottom]
    width = max([len(str(len(items)))] + [len(k) for k, _ in keys])

    def line(key: str, label: str) -> None:
        print(f"  {style(key.rjust(width), 'cyan')}. {label}")

    for key, label in top:
        line(key, label)
    for number, label in enumerate(items, start=1):
        line(str(number), label)
    for key, label in bottom:
        line(key, label)

    valid = {k.lower() for k, _ in keys}
    while True:
        raw = ask(prompt).lower()
        if raw in valid:
            if raw == "q":
                raise Cancelled()
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return int(raw) - 1
        choices = f"1-{len(items)}" if items else ""
        extras = ", ".join(k for k, _ in keys)
        warn(f"Enter {' or '.join(x for x in (choices, extras) if x)}.")


def pick_many(
    items: Sequence[str],
    *,
    preset: Optional[str] = None,
    check: Optional[Callable[[List[int]], None]] = None,
    prompt: str = "Select",
) -> List[int]:
    """Show a numbered list and let the user choose one, some or all of it.

    Returns sorted 0-based indexes. ``preset`` answers the prompt up front (a
    command-line flag); a bad preset raises ValueError instead of asking again.
    ``check`` can reject a selection by raising ValueError with a message.
    """
    width = len(str(len(items)))
    for number, label in enumerate(items, start=1):
        print(f"  {style(str(number).rjust(width), 'cyan')}. {label}")

    while True:
        raw = preset if preset is not None else ask_required(f"{prompt} ({SELECTION_HELP})")
        if raw.strip().lower() == "q":
            raise Cancelled()
        try:
            picked = parse_selection(raw, len(items))
            if check:
                check(picked)
            return picked
        except ValueError as exc:
            if preset is not None:
                raise
            warn(str(exc))


def parse_selection(text: str, count: int) -> List[int]:
    """'3', '1,4,7', '2-5', '1-3 8' or 'a'/'all' -> sorted 0-based indexes, no repeats."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return list(range(count))
    picked = set()
    for token in re.split(r"[,\s]+", text):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(f'"{token}" isn\'t a number or a range like 2-5.')
        low, high = int(match.group(1)), int(match.group(2) or match.group(1))
        low, high = min(low, high), max(low, high)  # "5-2" means 2-5
        if low < 1 or high > count:
            raise ValueError(f"{token} is outside 1-{count}.")
        picked.update(range(low - 1, high))
    if not picked:
        raise ValueError("Nothing selected.")
    return sorted(picked)
