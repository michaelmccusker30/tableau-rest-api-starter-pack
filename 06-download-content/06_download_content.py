#!/usr/bin/env python3
"""Download workbooks and published data sources from a Tableau site: a backup you can republish.

Entry 06 of the starter pack. See README.md here.

Run it with no arguments for the interactive flow:
  1. choose what to download: workbooks, data sources, or both
  2. choose the scope: every project, every project except some branches, or
     only some branches
  3. pick one, some or all of what's in scope (3 / 1,4,7 / 2-5 / a)
  4. review the count, size and folder, then download

Files land in one folder per run, mirroring the project tree:
  output/download_<site>_<date>/Finance/Reporting/Budget.twbx
with a manifest.csv beside them listing every item, its ID and what happened.
The IDs are what make a later republish or comparison reliable; names aren't
unique across projects.

Any step can be answered up front with a flag, and --yes skips all prompts.
It never changes anything on the site.

Safeguards:
  * Each file is written as .part and renamed when complete, so an
    interrupted download never looks like a finished one.
  * A data source the API hands back with a workbook extension (.twb/.twbx)
    is saved as .tds/.tdsx, so it reopens as what it is.
  * One failed download doesn't stop the rest; the exit code is 1 if anything
    failed, and the manifest says which.
  * --no-extract leaves extract data out, for a fast, small backup of the
    definitions alone.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.batch import DONE, run_batch, summary  # noqa: E402
from tcs.client import UPLOAD_TIMEOUT, TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_datasources, list_workbooks, placement  # noqa: E402
from tcs.files import clean_path_input, display_path, human_size, safe_name  # noqa: E402
from tcs.projects import fetch_project_tree  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv  # noqa: E402
from tcs.scope import choose_scope  # noqa: E402

WORKBOOK, DATASOURCE = "Workbook", "Data source"
KINDS = {WORKBOOK: "workbooks", DATASOURCE: "datasources"}
DEFAULT_SUFFIX = {WORKBOOK: ".twbx", DATASOURCE: ".tdsx"}
# The API sometimes names a live data source's download like a workbook; keep it a data source.
DATASOURCE_SUFFIX = {".twb": ".tds", ".twbx": ".tdsx"}
CHUNK = 1024 * 1024
MANIFEST_COLUMNS = ["Type", "Name", "Project Path", "File", "Size (bytes)", "Extract Included", "Status", "Detail", "ID"]

EXAMPLES = """\
examples:
  Interactive:
    python 06-download-content/06_download_content.py

  Back up every workbook and data source, definitions only:
    python 06-download-content/06_download_content.py --type all --no-extract --yes

  Every data source under Finance, with extracts:
    python 06-download-content/06_download_content.py --type datasources --include Finance --yes
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="06_download_content.py",
        description="Download workbooks and published data sources, mirroring the project tree.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--type", choices=["workbooks", "datasources", "all"], help="what to download; skips the first prompt"
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--include", metavar="PATH", action="append", help="only this project and everything under it")
    scope.add_argument("--exclude", metavar="PATH", action="append", help="every project except this one and its sub-projects")
    parser.add_argument("--select", metavar="LIST", help="which items, numbered as listed (3 / 1,4,7 / 2-5 / all)")
    parser.add_argument("--no-extract", action="store_true", help="leave extract data out (smaller, faster)")
    parser.add_argument(
        "--output-dir", metavar="PATH", help=f"where the run's folder goes (default: ./{DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="no prompts; downloads everything of --type (default all) in scope unless --select"
    )
    parser.add_argument("--dry-run", action="store_true", help="show the review, but download nothing")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. Files already downloaded are kept; the manifest lists them if the run had started.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Download workbooks and data sources")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    kinds = _choose_kinds(args)
    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        tree = fetch_project_tree(client)
        items: List[Dict[str, Any]] = []
        if WORKBOOK in kinds:
            items += [dict(wb, _type=WORKBOOK) for wb in list_workbooks(client)]
        if DATASOURCE in kinds:
            items += [dict(ds, _type=DATASOURCE) for ds in list_datasources(client)]
        ui.success(f"Read {len(tree)} projects and " + ", ".join(f"{_n(c, t.lower())}" for t, c in Counter(i["_type"] for i in items).items()))

        scope = choose_scope(
            tree,
            include=args.include,
            exclude=args.exclude,
            ask=not args.yes,
            counts=Counter((i.get("project") or {}).get("id") for i in items),
            noun="item",
        )
        in_scope = []
        for item in items:
            place = placement(tree, item)
            if scope.covers(place.project_id, place.visible):
                item["_path"] = place.path
                in_scope.append(item)
        in_scope.sort(key=lambda i: (i["_path"].casefold(), i["_type"], i.get("name", "").casefold()))
        if not in_scope:
            ui.warn("Nothing to download in this scope.")
            return 0

        chosen = _choose_items(in_scope, args)
        folder = report_path(
            clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR,
            "download", settings.site_label, datetime.now(), suffix="",
        )
        _print_review(settings, scope.label, chosen, args.no_extract, folder)
        if args.dry_run:
            ui.warn("Dry run: nothing was downloaded.")
            return 0
        if not args.yes and not ui.confirm(f"Download {_n(len(chosen), 'item')}?", default=True):
            raise ui.Cancelled()

        records: Dict[str, Dict[str, Any]] = {}
        ui.heading("Downloading")
        try:
            outcomes = run_batch(
                chosen,
                lambda i: download(client, i, folder, not args.no_extract, records),
                lambda i: f"{i['_type']}: {i['_path']} / {i.get('name', '')}",
            )
        finally:
            write_manifest(folder, chosen, records, not args.no_extract)

    for outcome in outcomes:
        if outcome.status != DONE:
            records.setdefault(outcome.item["id"], {})["Detail"] = outcome.detail
    write_manifest(folder, chosen, records, not args.no_extract)
    code = summary(outcomes, "Downloaded")
    ui.info(f"  Folder: {display_path(folder)}")
    return code


def _choose_kinds(args: argparse.Namespace) -> List[str]:
    choice = args.type or ("all" if args.yes else None)
    if choice is None:
        ui.heading("What to download?")
        picked = ui.menu(["Workbooks", "Data sources", "Both"], bottom=[("q", "Quit")])
        choice = ["workbooks", "datasources", "all"][int(picked)]
    return {"workbooks": [WORKBOOK], "datasources": [DATASOURCE], "all": [WORKBOOK, DATASOURCE]}[choice]


def _choose_items(items: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.yes and not args.select:
        return items
    ui.heading(f"{_n(len(items), 'item')} in scope")
    labels = [
        f"{i['_path']} / {i.get('name', '')}  " + ui.style(f"{i['_type'].lower()}, {_mb(i)}", "dim") for i in items
    ]
    picked = ui.pick_many(labels, preset=args.select, prompt="Download which")
    return [items[n] for n in picked]


def download(
    client: TableauClient, item: Dict[str, Any], folder: Path, with_extract: bool, records: Dict[str, Dict[str, Any]]
) -> str:
    """GET /{workbooks|datasources}/{id}/content, streamed to disk in 1 MB chunks."""
    kind = KINDS[item["_type"]]
    params = None if with_extract else {"includeExtract": "false"}
    response = client.request(
        "GET", f"/{kind}/{item['id']}/content", action=f"Download {item['_type'].lower()}",
        params=params, stream=True, timeout=UPLOAD_TIMEOUT,
    )
    target = target_path(folder, item, response.headers.get("Content-Disposition", ""))
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    size = 0
    try:
        with partial.open("wb") as handle:
            for chunk in response.iter_content(CHUNK):
                handle.write(chunk)
                size += len(chunk)
        partial.replace(target)
    except OSError as exc:
        raise TableauError(f"couldn't save {display_path(target)}: {exc.strerror or exc}") from None
    finally:
        response.close()
        if partial.exists():
            partial.unlink()
    records[item["id"]] = {"File": str(target.relative_to(folder)), "Size (bytes)": size}
    return human_size(size)


def target_path(folder: Path, item: Dict[str, Any], disposition: str) -> Path:
    """folder/<project path>/<name><suffix>, with the suffix Tableau gave the file."""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    server_name = unquote_plus(match.group(1)) if match else ""
    suffix = Path(server_name).suffix.lower() or DEFAULT_SUFFIX[item["_type"]]
    if item["_type"] == DATASOURCE:
        suffix = DATASOURCE_SUFFIX.get(suffix, suffix)
    parts = [safe_name(p.strip("()")) for p in item["_path"].split(" / ")]
    return folder.joinpath(*parts) / f"{safe_name(item.get('name', ''))}{suffix}"


def write_manifest(folder: Path, items: List[Dict[str, Any]], records: Dict[str, Dict[str, Any]], with_extract: bool) -> None:
    rows = []
    for item in items:
        record = records.get(item["id"], {})
        status = "Downloaded" if record.get("File") else ("Failed" if record.get("Detail") else "Not reached")
        rows.append(
            {
                "Type": item["_type"],
                "Name": item.get("name", ""),
                "Project Path": item["_path"],
                "File": record.get("File", ""),
                "Size (bytes)": record.get("Size (bytes)", ""),
                "Extract Included": "Yes" if with_extract else "No",
                "Status": status,
                "Detail": record.get("Detail", ""),
                "ID": item["id"],
            }
        )
    write_csv(folder / "manifest.csv", rows, MANIFEST_COLUMNS)


def _print_review(settings: Settings, scope_label: str, items: List[Dict[str, Any]], no_extract: bool, folder: Path) -> None:
    counts = Counter(i["_type"] for i in items)
    total_mb = sum(_size_mb(i) for i in items)
    extracts = sum(1 for i in items if i.get("hasExtracts") is True)
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Scope", scope_label),
            ("Items", ", ".join(_n(c, t.lower()) for t, c in counts.items())),
            ("Size", f"about {total_mb:,} MB as Tableau reports it" + (" (less without extracts)" if no_extract else "")),
            ("Extracts", "Left out (--no-extract)" if no_extract else "Included" + (f"; {extracts} data source(s) have one" if extracts else "")),
            ("Folder", display_path(folder)),
        ]
    )


def _size_mb(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _mb(item: Dict[str, Any]) -> str:
    return f"{_size_mb(item)} MB"


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
