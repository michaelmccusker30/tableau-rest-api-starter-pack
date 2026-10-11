#!/usr/bin/env python3
"""Bulk-publish .twbx workbooks from a folder on your computer into one Tableau project.

Story 1, part 1c: the AI-built bulk uploader. See README.md here.

The companion to 1b_publish_workbook.py: where that script publishes one file,
this one publishes a whole folder tree's worth in a single run.

Run it with no arguments for the interactive flow:
  1. pick a folder; it and all its subfolders are scanned for .twbx files
  2. pick one, some or all of them from a numbered list (3 / 1,4,7 / 2-5 / a)
  3. pick the target project, drilling into sub-projects as deep as they go
  4. review the batch, settle any name clashes once for all of them, then publish

Every workbook keeps its file name as its name on Tableau and is published with
tabbed views turned on. Any step can be answered up front with a flag, and
--yes skips all prompts.

Safeguards, each there because of a way a batch can go wrong:
  * Two selected files with the same name (say Finance/Sales.twbx and
    Archive/Sales.twbx) would land on the same workbook in one project, so the
    later one silently replaces the earlier. Such selections are refused.
  * Name clashes with workbooks already on the site are found before anything
    is sent, and settled with one decision: overwrite all or skip all.
  * Only the workbooks known to clash are published with overwrite=true. The
    rest go up with overwrite=false, so a workbook someone else publishes
    mid-run makes Tableau answer 409 instead of being replaced without warning.
  * One failure (a permission error, a broken file) doesn't stop the batch.
    The summary lists what was published, skipped and failed, and the exit
    code is 1 if anything failed, so a scheduled job notices.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, load_settings  # noqa: E402
from tcs.files import (  # noqa: E402
    clean_path_input,
    default_start_dir,
    display_path,
    file_summary,
    find_workbook_files,
    human_size,
    pick_folder,
    validate_folder,
)
from tcs.projects import Project, fetch_project_tree, resolve_project  # noqa: E402
from tcs.publishing import PublishPlan, describe_existing, find_workbook, publish_workbook  # noqa: E402

EXAMPLES = """\
examples:
  Interactive:
    python 01-publishing-workbooks/1c_bulk_publish_workbooks.py

  Pre-fill some answers and get asked for the rest:
    python 01-publishing-workbooks/1c_bulk_publish_workbooks.py --folder ~/Desktop/Exports

  Fully non-interactive (CI, scheduled jobs):
    python 01-publishing-workbooks/1c_bulk_publish_workbooks.py --folder ~/Desktop/Exports --select all \\
        --project "Finance/Reporting" --skip-existing --yes

  See what a run would do without publishing anything:
    python 01-publishing-workbooks/1c_bulk_publish_workbooks.py --folder ~/Desktop/Exports --select all \\
        --project "Finance/Reporting" --dry-run

selections (--select or at the prompt):
  3          one workbook
  1,4,7      several
  2-5        a range; mix freely, e.g. "1-3, 8 10"
  a or all   everything found
"""

# A local file paired with the same-named workbook already in the project, if any.
BatchItem = Tuple[Path, Optional[Dict[str, Any]]]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="1c_bulk_publish_workbooks.py",
        description="Publish .twbx workbooks found in a folder and its subfolders to one Tableau project, "
        "with tabbed views on.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--folder", metavar="PATH", help="folder to scan, subfolders included; skips the folder picker"
    )
    parser.add_argument(
        "--select", metavar="LIST", help='which of the found workbooks to publish, e.g. "1,4,7", "2-5" or "all"'
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--project",
        metavar="PATH",
        help='target project as a path, e.g. "Finance/Reporting/Monthly"; skips the project picker',
    )
    where.add_argument("--project-id", metavar="ID", help="target project by its id instead of its path")
    clash = parser.add_mutually_exclusive_group()
    clash.add_argument(
        "--overwrite", action="store_true", help="replace workbooks that already exist in the project"
    )
    clash.add_argument(
        "--skip-existing", action="store_true", help="leave workbooks that already exist alone"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="no prompts at all; needs --folder, --select and --project/--project-id, and stops "
        "instead of asking if any name is taken and neither --overwrite nor --skip-existing was given",
    )
    parser.add_argument("--dry-run", action="store_true", help="do everything except publish")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    if args.yes and not (args.folder and args.select and (args.project or args.project_id)):
        parser.error("--yes needs --folder, --select and either --project or --project-id")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. Nothing more was published.")  # "more": earlier ones in the batch may be up
        return 130
    except (ConfigError, TableauError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Bulk-publish workbooks to Tableau")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    # 1-2. The folder and the files. Chosen before signing in: it's all local,
    # and a typo in --select shouldn't cost a sign-in round trip.
    if args.folder:
        folder = validate_folder(clean_path_input(args.folder))
    else:
        folder = pick_folder(default_start_dir(settings.default_workbook_dir))
    found = find_workbook_files(folder)
    if not found:
        raise LookupError(f"No .twbx files in {display_path(folder)} or its subfolders.")
    files = choose_files(found, folder, args.select)
    ui.success(f"{len(files)} workbook{_s(files)} selected")

    with TableauClient(settings) as client:
        user = client.current_user_name()
        ui.success(
            f"Signed in to {settings.site_label}"
            + (f" as {user}" if user else "")
            + f" (REST API {client.api_version})"
        )

        # 3. The target project.
        tree = fetch_project_tree(client)
        if not len(tree):
            raise LookupError("Your account can't see any projects on this site.")
        project = resolve_project(tree, args.project, args.project_id)
        project_path = tree.path(project.id)
        ui.success(f"Project: {project_path}")

        # 4. Look up every name first, so the review shows the whole picture
        # and clashes are settled before the first upload, not halfway through.
        ui.detail("  Checking for workbooks that already exist...")
        batch: List[BatchItem] = [(f, find_workbook(client, project, f.stem)) for f in files]
        _print_review(batch, folder, project_path, f"{settings.site_label} on {settings.server_url}")

        overwrite = _settle_clashes(args, batch, project_path)
        queue = [(f, e) for f, e in batch if overwrite or not e]
        skipped = [f for f, e in batch if e and not overwrite]
        if not queue:
            ui.warn("Every selected workbook already exists and you chose to skip them. Nothing to publish.")
            return 0
        if args.dry_run:
            ui.warn(
                f"Dry run: nothing was published. A real run would publish {len(queue)} workbook{_s(queue)}"
                + (f" and skip {len(skipped)}." if skipped else ".")
            )
            return 0
        if not args.yes and not ui.confirm(f"Publish {len(queue)} workbook{_s(queue)} to {project_path}?"):
            raise ui.Cancelled()

        ui.heading("Publishing")
        published, failed = _publish_all(client, queue, project, project_path)

    _print_summary(published, skipped, failed, folder, project_path)
    return 1 if failed else 0


def choose_files(found: List[Path], folder: Path, preset: Optional[str]) -> List[Path]:
    """Number the files found and let the user pick one, some or all of them."""
    ui.heading(f"Found {len(found)} .twbx file{_s(found)} in {display_path(folder)}")
    labels = [f"{p.relative_to(folder)}  {ui.style(file_summary(p), 'dim')}" for p in found]

    def unique_names(indexes: List[int]) -> None:
        check_unique_names([found[i] for i in indexes], folder)

    try:
        picked = ui.pick_many(labels, preset=preset, check=unique_names)
    except ValueError as exc:  # only a bad --select gets here; typed answers are asked again
        raise ValueError(f"--select: {exc}") from None
    return [found[i] for i in picked]


def check_unique_names(files: List[Path], folder: Path) -> None:
    """Refuse two files that would publish under the same name.

    Names are compared ignoring case: the safe side until it's confirmed how
    Tableau treats "Sales" and "sales" in one project.
    """
    by_name: Dict[str, List[Path]] = {}
    for path in files:
        by_name.setdefault(path.stem.casefold(), []).append(path)
    dupes = [paths for paths in by_name.values() if len(paths) > 1]
    if dupes:
        lines = [f'  "{paths[0].stem}": ' + ", ".join(str(p.relative_to(folder)) for p in paths) for paths in dupes]
        raise ValueError(
            "These files share a name, so they would overwrite each other in one project. "
            "Pick only one of each:\n" + "\n".join(lines)
        )


def _print_review(batch: List[BatchItem], folder: Path, project_path: str, site: str) -> None:
    clashes = sum(1 for _, e in batch if e)
    total_size = sum(f.stat().st_size for f, _ in batch)
    ui.heading("Review")
    ui.table(
        [
            ("Folder", display_path(folder)),
            ("Project", project_path),
            ("Site", site),
            ("Workbooks", f"{len(batch)} ({human_size(total_size)}): {len(batch) - clashes} new, {clashes} {_exist(clashes)}"),
            ("Tabbed views", "On for all"),
        ]
    )
    print()
    width = len(str(len(batch)))
    for number, (path, existing) in enumerate(batch, start=1):
        status = ui.style(f"exists{describe_existing(existing)}", "yellow") if existing else ui.style("new", "green")
        source = ui.style(f"from {path.relative_to(folder)} ({human_size(path.stat().st_size)})", "dim")
        print(f"  {str(number).rjust(width)}. {path.stem}  {status}  {source}")


def _settle_clashes(args: argparse.Namespace, batch: List[BatchItem], project_path: str) -> bool:
    """One decision for every clash: True overwrites them all, False skips them all."""
    clashes = sum(1 for _, e in batch if e)
    if not clashes or args.overwrite:
        return args.overwrite
    if args.skip_existing:
        return False
    if args.yes:
        raise ValueError(
            f"{clashes} of the selected workbooks {_exist(clashes)} in {project_path}. "
            "Add --overwrite to replace them or --skip-existing to leave them alone."
        )
    print()
    ui.warn(f"{clashes} of the selected workbooks {_exist(clashes)} in {project_path}.")
    new = len(batch) - clashes
    them = "it" if clashes == 1 else f"all {clashes}"
    rest = f"publish only the {new} new one{'s' if new != 1 else ''}" if new else "publish nothing"
    choice = ui.menu([f"Overwrite {them}", f"Skip {them} ({rest})"], bottom=[("q", "Cancel")])
    return choice == 0


def _publish_all(
    client: TableauClient, queue: List[BatchItem], project: Project, project_path: str
) -> Tuple[List[Tuple[Path, Dict[str, Any]]], List[Tuple[Path, str]]]:
    """Publish one at a time, carrying on past failures. Returns (published, failed)."""
    published: List[Tuple[Path, Dict[str, Any]]] = []
    failed: List[Tuple[Path, str]] = []
    for number, (path, existing) in enumerate(queue, start=1):
        ui.info(f"[{number}/{len(queue)}] {path.stem}" + (" (replacing)" if existing else ""))
        plan = PublishPlan(
            file=path,
            name=path.stem,
            project=project,
            project_path=project_path,
            # Only the known clashes overwrite; a name that appears mid-run gets a 409, not a silent replace.
            overwrite=existing is not None,
            show_tabs=True,
        )
        try:
            workbook = publish_workbook(client, plan, progress=ui.detail)
        except TableauError as exc:
            reason = (
                "a workbook with this name appeared in the project during the run"
                if exc.status == 409 and not existing
                else str(exc)
            )
            ui.error(reason)
            failed.append((path, reason))
            continue
        ui.success("Published")
        published.append((path, workbook))
    return published, failed


def _print_summary(
    published: List[Tuple[Path, Dict[str, Any]]],
    skipped: List[Path],
    failed: List[Tuple[Path, str]],
    folder: Path,
    project_path: str,
) -> None:
    ui.heading("Summary")
    ui.table(
        [
            ("Published", str(len(published))),
            ("Skipped", f"{len(skipped)} (already existed)" if skipped else "0"),
            ("Failed", str(len(failed))),
            ("Project", project_path),
        ]
    )
    for path, workbook in published:
        url = workbook.get("webpageUrl")
        ui.success(workbook.get("name", path.stem) + (f"  {ui.style(url, 'dim')}" if url else ""))
    for path in skipped:
        ui.warn(f"{path.stem}  {ui.style('skipped', 'dim')}")
    for path, reason in failed:
        ui.error(f"{path.stem} ({path.relative_to(folder)}): {reason}")


def _s(items: List[Any]) -> str:
    return "" if len(items) == 1 else "s"


def _exist(count: int) -> str:
    return "already exists" if count == 1 else "already exist"


if __name__ == "__main__":
    sys.exit(main())
