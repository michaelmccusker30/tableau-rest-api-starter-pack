"""Reports (CSV, and pages such as HTML): where they go, what they're called, and writing them safely."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_OUTPUT_DIR = Path("output")

# A cell starting with one of these is read as a formula by Excel and friends.
# Workbook and project names are typed by anyone who can publish, so a name like
# =HYPERLINK(...) would run when an admin opens the report. Such cells get a
# leading apostrophe, which spreadsheets show as plain text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ReportError(Exception):
    """A report couldn't be written, or an input file couldn't be read."""


def report_path(folder: Path, stem: str, site: str, now: Optional[datetime] = None, suffix: str = ".csv") -> Path:
    """folder/<stem>_<site>_<YYYY-MM-DD_HHMM><suffix>, with _2, _3 ... added rather than overwrite a file."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M")
    base = f"{stem}_{_safe_part(site)}_{stamp}"
    path = folder / f"{base}{suffix}"
    number = 2
    while path.exists():
        path = folder / f"{base}_{number}{suffix}"
        number += 1
    return path


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> None:
    """Write rows (dicts keyed by column name) to a new CSV file.

    The file is UTF-8 with a byte-order mark, so Excel shows accented and
    non-Latin names correctly instead of guessing the encoding.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _cell(value) for key, value in row.items()})
    except PermissionError:
        raise ReportError(f"Can't write {path}: permission denied. If it's open in Excel, close it and try again.") from None
    except OSError as exc:
        raise ReportError(f"Can't write {path}: {exc.strerror or exc}") from None


def write_text(path: Path, text: str) -> None:
    """Write a page (HTML, Markdown...) as UTF-8, with the same errors as write_csv."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except PermissionError:
        raise ReportError(f"Can't write {path}: permission denied.") from None
    except OSError as exc:
        raise ReportError(f"Can't write {path}: {exc.strerror or exc}") from None


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a CSV someone may have saved from Excel: returns (column names, rows).

    Tries UTF-8 (with or without a byte-order mark) first, then Windows-1252,
    which is what Excel on Windows writes by default. Column names and values
    are trimmed, and Excel's non-breaking spaces become plain spaces. A leading
    apostrophe added by write_csv's formula guard is left alone: match on IDs.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ReportError(f"File not found: {path}") from None
    except OSError as exc:
        raise ReportError(f"Can't read {path}: {exc.strerror or exc}") from None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise ReportError(f"Can't read {path}: unknown text encoding. Save it as CSV UTF-8 from Excel.")
    reader = csv.reader(io.StringIO(text.replace("\u00a0", " ")))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ReportError(f"{path} is empty.")
    columns = [c.strip() for c in rows[0]]
    records = [{columns[i]: cell.strip() for i, cell in enumerate(row) if i < len(columns)} for row in rows[1:]]
    return columns, records


def find_column(columns: Sequence[str], wanted: Sequence[str]) -> Optional[str]:
    """The first column whose name matches one of ``wanted``, ignoring case, spaces and underscores."""
    norm = {c.casefold().replace(" ", "").replace("_", ""): c for c in columns}
    for name in wanted:
        hit = norm.get(name.casefold().replace(" ", "").replace("_", ""))
        if hit:
            return hit
    return None


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _safe_part(text: str) -> str:
    """Keep a site name usable in a file name on any OS."""
    return re.sub(r"[^\w.-]+", "-", text).strip("-") or "site"
