#!/usr/bin/env python3
"""Move workbooks to another project, with a log that can undo the move.

Entry 04 of the starter pack. See README.md here.

Choose the workbooks one of three ways:
  * from a project: pick one, some or all of the workbooks in it
  * from a CSV with a Workbook ID column, such as an entry 02 inventory
    filtered in Excel
  * --undo LOG: put back every workbook an earlier run moved

Then choose the destination project, review every move, and confirm.

Before anything moves, a log is written: each workbook, where it was, where
it's going. It's updated with the outcome at the end. Feed it to --undo to
move everything back. An undo writes a log of its own, so it can be undone too.

Any step can be answered up front with a flag, and --yes skips all prompts.

Safeguards, each there because of a way a bulk move goes wrong:
  * The log is written before the first move, so even an interrupted run can
    be undone.
  * Workbooks already in the destination are skipped, not "moved" again.
  * A workbook whose name is taken in the destination is skipped before the
    run: Tableau would refuse it anyway. So is the second of two selected
    workbooks with the same name.
  * --undo only moves a workbook back if it's still where this tool put it.
    One that has been moved again since is left alone and reported.
  * IDs that aren't on the site (a CSV from another site, say) are listed in
    the review, not silently ignored.
  * One failure doesn't stop the rest; the exit code is 1 if anything failed.
  * Moving into a project whose permissions are locked changes who can see the
    workbook. The review says so.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.batch import run_batch, summary  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_workbooks, owner_label  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.projects import Project, ProjectTree, fetch_project_tree, resolve_project  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, find_column, read_csv, report_path, write_csv  # noqa: E402

LOG_COLUMNS = [
    "Workbook",
    "Workbook ID",
    "From Project",
    "From Project ID",
    "To Project",
    "To Project ID",
    "Status",
    "Detail",
    "When (UTC)",
]
PLANNED, MOVED, FAILED, SKIPPED = "Planned", "Moved", "Failed", "Skipped"
ID_COLUMNS = ["Workbook ID", "workbook_id", "id", "luid"]
LOCKED = ("LockedToProject", "LockedToProjectWithoutNested")

EXAMPLES = """\
examples:
  Interactive:
    python 04-move-workbooks/04_move_workbooks.py

  Three workbooks from one project to another:
    python 04-move-workbooks/04_move_workbooks.py --from-project "Sandbox" --select 1,4,7 \\
        --to-project "Finance/Reporting"

  Everything listed in a CSV (e.g. an entry 02 inventory filtered in Excel):
    python 04-move-workbooks/04_move_workbooks.py --from-csv to_archive.csv --to-project Archive --yes

  Put them all back:
    python 04-move-workbooks/04_move_workbooks.py --undo output/moves_acme_2026-10-10_1432.csv
"""


@dataclass
class Move:
    workbook: Dict[str, Any]
    from_id: str
    to_id: str
    skip: str = ""  # reason it won't be moved, if any
    status: str = PLANNED
    detail: str = field(default="")

    @property
    def name(self) -> str:
        return self.workbook.get("name", "")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="04_move_workbooks.py",
        description="Move workbooks to another project, with a log that can undo the move.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-project", metavar="PATH", help='pick workbooks from this project, e.g. "Sandbox/Drafts"')
    source.add_argument("--from-csv", metavar="PATH", help="move the workbooks listed in a CSV with a Workbook ID column")
    source.add_argument("--undo", metavar="LOG", help="move back everything an earlier run moved, using its log")
    parser.add_argument(
        "--select",
        metavar="LIST",
        help="with --from-project: which workbooks, numbered as listed (3 / 1,4,7 / 2-5 / all)",
    )
    dest = parser.add_mutually_exclusive_group()
    dest.add_argument("--to-project", metavar="PATH", help="destination project as a path; skips the picker")
    dest.add_argument("--to-project-id", metavar="ID", help="destination project by id")
    parser.add_argument(
        "--output-dir", metavar="PATH", help=f"folder for the move log (default: ./{DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="no prompts at all; needs a source (--from-project with --select, --from-csv, or --undo) "
        "and, except for --undo, a destination",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the review, but move nothing and write no log")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    if args.select and not args.from_project:
        parser.error("--select goes with --from-project")
    if args.undo and (args.to_project or args.to_project_id):
        parser.error("--undo moves workbooks back where they came from; leave out --to-project")
    if args.yes:
        if not (args.from_csv or args.undo or (args.from_project and args.select)):
            parser.error("--yes needs --from-csv, --undo, or --from-project with --select")
        if not args.undo and not (args.to_project or args.to_project_id):
            parser.error("--yes needs --to-project or --to-project-id")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. Nothing more will be moved. If a run had started, its log shows what moved.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Move workbooks")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    # Read the CSV or log first: a bad file should fail before signing in.
    csv_ids: List[str] = []
    undo_rows: List[Dict[str, str]] = []
    mode = "undo" if args.undo else "csv" if args.from_csv else "project" if args.from_project else ""
    if not mode:
        ui.heading("Which workbooks?")
        choice = ui.menu(
            ["Pick them from a project", "The ones listed in a CSV file (Workbook ID column)", "Undo an earlier move"],
            bottom=[("q", "Quit")],
        )
        mode = ["project", "csv", "undo"][int(choice)]
    if mode == "csv":
        csv_ids = read_workbook_ids(clean_path_input(args.from_csv or ui.ask_required("Path to the CSV file")))
    elif mode == "undo":
        undo_rows = read_undo_log(clean_path_input(args.undo or ui.ask_required("Path to the move log")))

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        tree = fetch_project_tree(client)
        workbooks = {wb["id"]: wb for wb in list_workbooks(client)}
        ui.success(f"Read {len(tree)} projects and {len(workbooks)} workbooks")

        missing: List[str] = []
        if mode == "undo":
            moves, missing = plan_undo(undo_rows, workbooks)
            destination_label = "Back where each one came from"
            destination: Optional[Project] = None
        else:
            if mode == "csv":
                chosen = [workbooks[i] for i in csv_ids if i in workbooks]
                missing = [i for i in csv_ids if i not in workbooks]
            else:
                chosen = pick_from_project(tree, workbooks, args)
            destination = resolve_project(
                tree, args.to_project, args.to_project_id, here="Move here", title="Move them to which project?"
            )
            destination_label = tree.path(destination.id)
            moves = [Move(wb, (wb.get("project") or {}).get("id", ""), destination.id) for wb in chosen]
        check_moves(moves, workbooks)

        ready = [m for m in moves if not m.skip]
        folder = clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
        log_file = report_path(folder, "moves", settings.site_label)
        _print_review(settings, tree, moves, missing, destination, destination_label, log_file)
        if not ready:
            ui.warn("Nothing to move.")
            return 0
        if args.dry_run:
            ui.warn("Dry run: nothing was moved and no log was written.")
            return 0
        if not args.yes and not ui.confirm(f"Move {_n(len(ready), 'workbook')} now?"):
            raise ui.Cancelled()

        # The log goes down before the first move, so an interrupted run can still be undone.
        write_log(log_file, tree, moves)
        ui.detail(f"  Log: {display_path(log_file)}")
        ui.heading("Moving")
        try:
            outcomes = run_batch(
                ready,
                lambda m: move_workbook(client, m),
                lambda m: f"{m.name}  {ui.style(_path(tree, m.from_id) + ' -> ' + _path(tree, m.to_id), 'dim')}",
                explain=_explain,
            )
        finally:
            write_log(log_file, tree, moves)  # final statuses, even after Ctrl-C

    code = summary(outcomes, "Moved")
    ui.info(f"  Log: {display_path(log_file)}")
    if any(o.ok for o in outcomes):
        ui.info(f"  To put them back:  python 04-move-workbooks/04_move_workbooks.py --undo \"{log_file}\"")
    return code


# ------------------------------------------------------------------ choosing workbooks

def pick_from_project(tree: ProjectTree, workbooks: Dict[str, Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    source = resolve_project(tree, args.from_project, here="Move from here", title="Move workbooks from which project?")
    here = sorted(
        (wb for wb in workbooks.values() if (wb.get("project") or {}).get("id") == source.id),
        key=lambda wb: wb.get("name", "").casefold(),
    )
    if not here:
        raise LookupError(f"{tree.path(source.id)} has no workbooks you can see (sub-projects aren't included).")
    ui.heading(f"{_n(len(here), 'workbook')} in {tree.path(source.id)}")
    labels = [
        f"{wb.get('name', '')}  "
        + ui.style(f"updated {(wb.get('updatedAt') or '')[:10]}, owner {owner_label(wb.get('owner') or {})}", "dim")
        for wb in here
    ]
    picked = ui.pick_many(labels, preset=args.select, prompt="Move which")
    return [here[i] for i in picked]


def read_workbook_ids(path: Path) -> List[str]:
    columns, rows = read_csv(path)
    column = find_column(columns, ID_COLUMNS)
    if not column:
        raise ValueError(f"{display_path(path)} has no Workbook ID column. Columns found: {', '.join(columns)}")
    ids = list(dict.fromkeys(r.get(column, "") for r in rows if r.get(column)))
    if not ids:
        raise ValueError(f"{display_path(path)} lists no workbook ids.")
    ui.success(f"{_n(len(ids), 'workbook id')} in {display_path(path)}")
    return ids


def read_undo_log(path: Path) -> List[Dict[str, str]]:
    columns, rows = read_csv(path)
    needed = ["Workbook ID", "From Project ID", "To Project ID", "Status"]
    absent = [c for c in needed if c not in columns]
    if absent:
        raise ValueError(f"{display_path(path)} isn't a move log from this script (no {', '.join(absent)} column).")
    # Planned rows count too: the run may have stopped before it could record the outcome.
    undoable = [r for r in rows if r.get("Status") in (MOVED, PLANNED)]
    ui.success(f"{_n(len(undoable), 'move')} to undo in {display_path(path)}")
    return undoable


def plan_undo(rows: List[Dict[str, str]], workbooks: Dict[str, Dict[str, Any]]) -> Tuple[List[Move], List[str]]:
    moves, missing = [], []
    for row in rows:
        wb = workbooks.get(row["Workbook ID"])
        if wb is None:
            missing.append(row["Workbook ID"])
            continue
        move = Move(wb, row["To Project ID"], row["From Project ID"])
        current = (wb.get("project") or {}).get("id")
        if current == row["From Project ID"]:
            move.skip = "already back"
        elif current != row["To Project ID"]:
            move.skip = "moved again since; left alone"
        moves.append(move)
    return moves, missing


def check_moves(moves: List[Move], workbooks: Dict[str, Dict[str, Any]]) -> None:
    """Mark the moves that can't or needn't happen, before anything is sent."""
    taken = {
        ((wb.get("project") or {}).get("id"), wb.get("name", "").casefold()): wb["id"] for wb in workbooks.values()
    }
    claimed: Dict[Tuple[str, str], str] = {}
    for move in moves:
        if move.skip:
            continue
        if (move.workbook.get("project") or {}).get("id") == move.to_id:
            move.skip = "already there"
            continue
        key = (move.to_id, move.name.casefold())
        if taken.get(key, move.workbook["id"]) != move.workbook["id"]:
            move.skip = "name taken in the destination"
        elif claimed.get(key, move.workbook["id"]) != move.workbook["id"]:
            move.skip = "same name as another workbook in this batch"
        else:
            claimed[key] = move.workbook["id"]
    for move in moves:
        if move.skip:
            move.status, move.detail = SKIPPED, move.skip


# ------------------------------------------------------------------ moving

def move_workbook(client: TableauClient, move: Move) -> str:
    """PUT /sites/{site-id}/workbooks/{id} with the new project. Nothing else about the workbook changes."""
    try:
        client.request(
            "PUT",
            f"/workbooks/{move.workbook['id']}",
            action="Move workbook",
            json={"workbook": {"project": {"id": move.to_id}}},
        )
    except TableauError as exc:
        move.status, move.detail = FAILED, _explain(move, exc)
        raise
    move.status, move.detail = MOVED, ""
    return ""


def _explain(move: Move, exc: TableauError) -> str:
    if exc.status == 409:
        return "a workbook with this name is now in the destination"
    if exc.status == 403:
        return f"your account isn't allowed to move it ({exc})"
    return str(exc)


def write_log(path: Path, tree: ProjectTree, moves: List[Move]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    write_csv(
        path,
        [
            {
                "Workbook": m.name,
                "Workbook ID": m.workbook.get("id", ""),
                "From Project": _path(tree, m.from_id),
                "From Project ID": m.from_id,
                "To Project": _path(tree, m.to_id),
                "To Project ID": m.to_id,
                "Status": m.status,
                "Detail": m.detail,
                "When (UTC)": now,
            }
            for m in moves
        ],
        LOG_COLUMNS,
    )


# ------------------------------------------------------------------ review

def _print_review(
    settings: Settings,
    tree: ProjectTree,
    moves: List[Move],
    missing: List[str],
    destination: Optional[Project],
    destination_label: str,
    log_file: Path,
) -> None:
    ready = [m for m in moves if not m.skip]
    skipped = [m for m in moves if m.skip]
    ui.heading("Review")
    rows = [
        ("Site", f"{settings.site_label} on {settings.server_url}"),
        ("Destination", destination_label),
        ("To move", str(len(ready))),
    ]
    if skipped:
        rows.append(("Skipped", str(len(skipped))))
    if missing:
        rows.append(("Not found", f"{len(missing)} id{'s' if len(missing) != 1 else ''} not on this site"))
    rows.append(("Log", display_path(log_file)))
    ui.table(rows)
    if destination is not None and destination.content_permissions in LOCKED:
        ui.warn(
            f"{destination_label} has locked permissions: the moved workbooks will take its permissions, "
            "and their own will no longer apply."
        )
    print()
    for move in moves:
        route = f"{_path(tree, move.from_id)} -> {_path(tree, move.to_id)}"
        if move.skip:
            print(f"  {ui.style('skip', 'yellow')}  {move.name}  {ui.style(f'({move.skip})', 'dim')}")
        else:
            print(f"  {ui.style('move', 'green')}  {move.name}  {ui.style(route, 'dim')}")
    for workbook_id in missing[:10]:
        print(f"  {ui.style('????', 'red')}  {workbook_id}  {ui.style('(not on this site)', 'dim')}")
    if len(missing) > 10:
        ui.detail(f"  ...and {len(missing) - 10} more ids not found")


def _path(tree: ProjectTree, project_id: str) -> str:
    return tree.path(project_id) if project_id in tree.by_id else f"(project {project_id})"


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
