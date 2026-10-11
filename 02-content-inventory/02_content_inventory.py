#!/usr/bin/env python3
"""List every workbook on a Tableau site with its full project path and owner, as CSV.

Entry 02 of the starter pack. See README.md here.

It writes two files:
  * workbooks_<site>_<date>.csv: one row per workbook, with the full project
    path ("Finance / Reporting / Monthly"), the top-level project, the owner's
    name, email, site role and last sign-in, total views, dates, size, tags
    and links
  * projects_<site>_<date>.csv: the project tree, with each project's depth,
    owner, workbook counts and content permissions

Run it with no arguments for the interactive flow:
  1. sign in; the project tree, workbook list and view counts are read (read-only)
  2. choose the scope: every project, every project except some branches, or
     only some branches
  3. review the counts and a preview, then write the files (to ./output
     unless --output-dir says otherwise)

Any step can be answered up front with a flag, and --yes skips all prompts.
It never changes anything on the site.

Admin Insights or this script? On Tableau Cloud, site admins already get most
of this from the Site Content data source in the Admin Insights project. Use
this script when you're on Tableau Server (which has no Admin Insights), when
you have no access to Admin Insights (it lists whatever your permissions let
you see), when you need it as of now rather than as of the last scheduled
refresh, or when the CSV is the input to another script.

Safeguards, each there because of a way an inventory can mislead:
  * A workbook whose project you can't see, or one in someone's Personal
    Space, is kept and labelled, never silently dropped, so the counts add up.
  * Names that start with =, +, - or @ are written so that spreadsheets show
    them as text instead of running them as formulas.
  * Files are never overwritten: a second run in the same minute gets _2.
  * Because of the formula guard, a name in the file can differ from the
    name on the site. Scripts that read this CSV should match on Workbook ID.
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
from tcs.content import list_views, list_workbooks, placement, views_by_workbook  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.projects import ProjectTree, fetch_project_tree  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv  # noqa: E402
from tcs.scope import Scope, choose_scope  # noqa: E402

WORKBOOK_COLUMNS = [
    "Workbook",
    "Project Path",
    "Top-Level Project",
    "Project",
    "Location",
    "Owner Name",
    "Owner Email",
    "Owner Username",
    "Owner Site Role",
    "Owner Last Login (UTC)",
    "Total Views",
    "Created (UTC)",
    "Updated (UTC)",
    "Size (MB)",
    "Tags",
    "Webpage URL",
    "Workbook ID",
    "Project ID",
]
PROJECT_COLUMNS = [
    "Project",
    "Full Path",
    "Top-Level Project",
    "Depth",
    "Workbooks",
    "Workbooks Incl. Sub-projects",
    "Owner Name",
    "Owner Email",
    "Content Permissions",
    "Description",
    "Parent Project ID",
    "Project ID",
]

UNLICENSED = "Unlicensed"
PREVIEW_ROWS = 5


EXAMPLES = """\
examples:
  Interactive:
    python 02-content-inventory/02_content_inventory.py

  Everything, no prompts (scheduled jobs):
    python 02-content-inventory/02_content_inventory.py --yes

  Only two branches, into a folder of your choice:
    python 02-content-inventory/02_content_inventory.py --include Finance --include "Sales/EMEA" \\
        --output-dir ~/Desktop/inventory --yes

  Everything except the archive, just to see the counts:
    python 02-content-inventory/02_content_inventory.py --exclude Archive --dry-run
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="02_content_inventory.py",
        description="List every workbook with its full project path and owner, plus the project tree, as CSV.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--include",
        metavar="PATH",
        action="append",
        help='only this project and everything under it, e.g. "Finance/Reporting"; repeat for more; '
        "skips the scope prompt",
    )
    scope.add_argument(
        "--exclude",
        metavar="PATH",
        action="append",
        help="every project except this one and everything under it; repeat for more; skips the scope prompt",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help=f"folder for the CSV files (default: ./{DEFAULT_OUTPUT_DIR}, created if needed)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="no prompts at all; the scope is every project unless --include or --exclude is given",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the review and preview, but write no files")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    for flag in ("include", "exclude"):
        if any(not path.strip() for path in getattr(args, flag) or []):
            parser.error(f"--{flag} can't be empty")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. No files were written.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Inventory of workbooks and projects")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    with TableauClient(settings) as client:
        user = client.current_user_name()
        ui.success(
            f"Signed in to {settings.site_label}"
            + (f" as {user}" if user else "")
            + f" (REST API {client.api_version})"
        )

        # 1. Read the site. Nothing here changes anything.
        tree = fetch_project_tree(client, with_owner=True)
        workbooks = list_workbooks(client)
        view_counts = views_by_workbook(list_views(client))
    ui.success(f"Read {_n(len(tree), 'project')} and {_n(len(workbooks), 'workbook')}")

    # 2. Scope. Signed out by now: the rest is all local.
    scope = choose_scope(
        tree,
        include=args.include,
        exclude=args.exclude,
        ask=not args.yes,
        counts=Counter((wb.get("project") or {}).get("id") for wb in workbooks),
        noun="workbook",
    )
    projects = [p for p in tree.walk() if scope.covers(p.id)]
    rows = workbook_rows(tree, workbooks, scope, view_counts)

    # 3. Where the files go. Shown in the review, so no prompt for it.
    folder = clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    now = datetime.now()
    workbooks_file = report_path(folder, "workbooks", settings.site_label, now)
    projects_file = report_path(folder, "projects", settings.site_label, now)

    # 4. Review, then write.
    _print_review(settings, user, scope, projects, rows, workbooks_file, projects_file)
    if args.dry_run:
        ui.warn("Dry run: no files were written.")
        return 0
    if not args.yes and not ui.confirm("Write these 2 files?", default=True):
        raise ui.Cancelled()

    write_csv(workbooks_file, rows, WORKBOOK_COLUMNS)
    write_csv(projects_file, project_rows(tree, projects, rows), PROJECT_COLUMNS)
    ui.success(f"{_n(len(rows), 'workbook')}: {display_path(workbooks_file)}")
    ui.success(f"{_n(len(projects), 'project')}: {display_path(projects_file)}")
    return 0


def workbook_rows(
    tree: ProjectTree, workbooks: List[Dict[str, Any]], scope: Scope, view_counts: Counter
) -> List[Dict[str, Any]]:
    """One CSV row per workbook in scope, sorted by project path, then name.

    Workbooks outside the visible project tree (Personal Space, or a project
    your account can't see) are kept when the scope is everything or
    everything-except, and labelled in Location. An include scope names
    projects, so they fall outside it.
    """
    rows = []
    for wb in workbooks:
        place = placement(tree, wb)
        if not scope.covers(place.project_id, place.visible):
            continue
        owner = wb.get("owner") or {}
        rows.append(
            {
                "Workbook": wb.get("name", ""),
                "Project Path": place.path,
                "Top-Level Project": place.top_level,
                "Project": place.project,
                "Location": place.location,
                "Owner Name": owner.get("fullName") or owner.get("name", ""),
                "Owner Email": owner.get("email", ""),
                "Owner Username": owner.get("name", ""),
                "Owner Site Role": owner.get("siteRole", ""),
                "Owner Last Login (UTC)": _timestamp(owner.get("lastLogin")),
                "Total Views": view_counts[wb.get("id", "")],
                "Created (UTC)": _timestamp(wb.get("createdAt")),
                "Updated (UTC)": _timestamp(wb.get("updatedAt")),
                "Size (MB)": _int(wb.get("size")),
                "Tags": ", ".join(t.get("label", "") for t in (wb.get("tags") or {}).get("tag", [])),
                "Webpage URL": wb.get("webpageUrl", ""),
                "Workbook ID": wb.get("id", ""),
                "Project ID": place.project_id,
            }
        )
    rows.sort(key=lambda r: (r["Location"] != "Project", r["Project Path"].casefold(), r["Workbook"].casefold()))
    return rows


def project_rows(tree: ProjectTree, projects: List[Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    direct = Counter(r["Project ID"] for r in rows)
    out = []
    for project in projects:
        lineage = tree.lineage(project.id)
        out.append(
            {
                "Project": project.name,
                "Full Path": tree.path(project.id),
                "Top-Level Project": lineage[0].name,
                "Depth": len(lineage),
                "Workbooks": direct[project.id],
                "Workbooks Incl. Sub-projects": sum(direct[i] for i in tree.subtree_ids(project.id)),
                "Owner Name": project.owner_name,
                "Owner Email": project.owner_email,
                "Content Permissions": project.content_permissions,
                "Description": project.description,
                "Parent Project ID": project.parent_id or "",
                "Project ID": project.id,
            }
        )
    return out


def _print_review(
    settings: Settings,
    user: Optional[str],
    scope: Scope,
    projects: List[Any],
    rows: List[Dict[str, Any]],
    workbooks_file: Path,
    projects_file: Path,
) -> None:
    outside = Counter(r["Location"] for r in rows if r["Location"] != "Project")
    detail = ", ".join(f"{count} in {where.lower()}" for where, count in sorted(outside.items()))
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Signed in as", user or "(name not available)"),
            ("Scope", scope.label),
            ("Projects", str(len(projects))),
            ("Workbooks", str(len(rows)) + (f" ({detail})" if detail else "")),
            ("Files", f"{display_path(workbooks_file)}\n{display_path(projects_file)}"),
        ]
    )
    if not rows:
        ui.warn("No workbooks in this scope. The workbooks file will have its header row only.")
        return
    unlicensed = sum(1 for r in rows if r["Owner Site Role"] == UNLICENSED)
    if unlicensed:
        # Content stays on the site after its owner loses their licence. Worth knowing before anything else.
        ui.warn(
            f"{_n(unlicensed, 'workbook')} owned by unlicensed users. "
            f'Filter Owner Site Role for "{UNLICENSED}" to see which.'
        )
    print()
    ui.detail(f"  First {min(PREVIEW_ROWS, len(rows))} of {len(rows)}:")
    for row in rows[:PREVIEW_ROWS]:
        owner = ui.style(f"owner {row['Owner Name'] or '?'}", "dim")
        print(f"    {row['Workbook']}  {ui.style(row['Project Path'], 'cyan')}  {owner}")


def _timestamp(value: Optional[str]) -> str:
    """'2026-09-12T18:20:11Z' -> '2026-09-12 18:20:11' (Excel reads the second as a date)."""
    return (value or "").replace("T", " ").rstrip("Z")


def _int(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
