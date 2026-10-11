#!/usr/bin/env python3
"""Every data connection on a Tableau site: what it connects to, and with which username.

Entry 07 of the starter pack. See README.md here.

For each published data source and/or workbook in scope, it lists every
connection: its type, server, port, username, whether a password is embedded,
and how it authenticates. The review sums up the connection types and the
usernames in use.

The question it answers: "We're rotating the password for svc_reporting.
What uses it?" Use --username to list only that account's connections.

Run it with no arguments for the interactive flow:
  1. choose data sources, workbooks, or both
  2. choose the scope: every project, all except some branches, or only some
  3. connections are read (one call per item), then review and write the CSV

Any step can be answered up front with a flag, and --yes skips all prompts.
It never changes anything on the site, and it never sees a password: the API
only says whether one is embedded.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_connections, list_datasources, list_workbooks, owner_label, placement  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.projects import fetch_project_tree  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv  # noqa: E402
from tcs.scope import choose_scope  # noqa: E402

WORKBOOK, DATASOURCE = "Workbook", "Data source"
KINDS = {WORKBOOK: "workbooks", DATASOURCE: "datasources"}
PROGRESS_EVERY = 25
COLUMNS = [
    "Content Type",
    "Name",
    "Project Path",
    "Owner",
    "Connection Type",
    "Server",
    "Port",
    "Username",
    "Password Embedded",
    "Authentication",
    "Data Source Name",
    "Content URL",
    "Content ID",
    "Connection ID",
]

EXAMPLES = """\
examples:
  Interactive:
    python 07-connections/07_connections.py

  Everything that signs in as one account:
    python 07-connections/07_connections.py --type all --username svc_reporting --yes

  Data sources under Finance only:
    python 07-connections/07_connections.py --type datasources --include Finance --yes
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="07_connections.py",
        description="Every data connection: what it connects to, and with which username.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--type", choices=["datasources", "workbooks", "all"], help="whose connections; skips the first prompt")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--include", metavar="PATH", action="append", help="only this project and everything under it")
    scope.add_argument("--exclude", metavar="PATH", action="append", help="every project except this one and its sub-projects")
    parser.add_argument("--username", metavar="NAME", help="keep only connections that sign in as NAME (any case)")
    parser.add_argument("--output-dir", metavar="PATH", help=f"folder for the CSV (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts; --type defaults to all")
    parser.add_argument("--dry-run", action="store_true", help="read and review, but write no file")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. No file was written.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Data connections")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    kinds = _choose_kinds(args)
    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        tree = fetch_project_tree(client)
        items: List[Dict[str, Any]] = []
        if DATASOURCE in kinds:
            items += [dict(ds, _type=DATASOURCE) for ds in list_datasources(client)]
        if WORKBOOK in kinds:
            items += [dict(wb, _type=WORKBOOK) for wb in list_workbooks(client)]

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

        ui.heading(f"Reading connections for {_n(len(in_scope), 'item')}")
        rows, failures = read_connections(client, in_scope)

    if args.username:
        wanted = args.username.casefold()
        rows = [r for r in rows if r["Username"].casefold() == wanted]
    rows.sort(key=lambda r: (r["Content Type"], r["Project Path"].casefold(), r["Name"].casefold(), r["Server"].casefold()))

    out_file = report_path(clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR,
                           "connections", settings.site_label, datetime.now())
    _print_review(settings, scope.label, in_scope, rows, failures, args.username, out_file)
    if args.dry_run:
        ui.warn("Dry run: no file was written.")
        return 0
    if not args.yes and not ui.confirm("Write the CSV?", default=True):
        raise ui.Cancelled()
    write_csv(out_file, rows, COLUMNS)
    ui.success(f"{_n(len(rows), 'connection')}: {display_path(out_file)}")
    return 1 if failures else 0


def read_connections(client: TableauClient, items: List[Dict[str, Any]]):
    """One GET per item. An item that can't be read is reported and skipped, not fatal."""
    rows: List[Dict[str, Any]] = []
    failures: List[str] = []
    for number, item in enumerate(items, start=1):
        if number % PROGRESS_EVERY == 0:
            ui.detail(f"  ... {number} of {len(items)}")
        try:
            connections = list_connections(client, KINDS[item["_type"]], item["id"])
        except TableauError as exc:
            failures.append(f"{item['_type']} {item.get('name', '')}: {exc}")
            continue
        for conn in connections:
            rows.append(
                {
                    "Content Type": item["_type"],
                    "Name": item.get("name", ""),
                    "Project Path": item["_path"],
                    "Owner": owner_label(item.get("owner") or {}),
                    "Connection Type": conn.get("type", ""),
                    "Server": conn.get("serverAddress", ""),
                    "Port": conn.get("serverPort", ""),
                    "Username": conn.get("userName", ""),
                    "Password Embedded": "Yes" if str(conn.get("embedPassword")).lower() == "true" else "No",
                    "Authentication": conn.get("authenticationType", ""),
                    "Data Source Name": (conn.get("datasource") or {}).get("name", "")
                    or (item.get("name", "") if item["_type"] == DATASOURCE else ""),
                    "Content URL": item.get("webpageUrl", ""),
                    "Content ID": item["id"],
                    "Connection ID": conn.get("id", ""),
                }
            )
    return rows, failures


def _choose_kinds(args: argparse.Namespace) -> List[str]:
    choice = args.type or ("all" if args.yes else None)
    if choice is None:
        ui.heading("Whose connections?")
        picked = ui.menu(["Published data sources", "Workbooks", "Both"], bottom=[("q", "Quit")])
        choice = ["datasources", "workbooks", "all"][int(picked)]
    return {"datasources": [DATASOURCE], "workbooks": [WORKBOOK], "all": [DATASOURCE, WORKBOOK]}[choice]


def _print_review(
    settings: Settings,
    scope_label: str,
    items: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    failures: List[str],
    username: Optional[str],
    out_file: Path,
) -> None:
    types = Counter(r["Connection Type"] for r in rows)
    users = Counter(r["Username"] for r in rows if r["Username"])
    embedded = sum(1 for r in rows if r["Password Embedded"] == "Yes")
    ui.heading("Review")
    table = [
        ("Site", f"{settings.site_label} on {settings.server_url}"),
        ("Scope", scope_label + (f"; username {username}" if username else "")),
        ("Items read", str(len(items) - len(failures))),
        ("Connections", f"{len(rows)} ({embedded} with an embedded password)"),
        ("Types", ", ".join(f"{t or '?'} {c}" for t, c in types.most_common(8)) or "none"),
        ("Usernames", ", ".join(f"{u} ({c})" for u, c in users.most_common(8)) or "none"),
        ("File", display_path(out_file)),
    ]
    ui.table(table)
    if len(users) > 8:
        ui.detail(f"  ...and {len(users) - 8} more usernames in the file")
    for failure in failures[:10]:
        ui.warn(f"Couldn't read {failure}")


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
