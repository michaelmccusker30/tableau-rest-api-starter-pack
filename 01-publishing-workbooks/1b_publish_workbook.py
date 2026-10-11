#!/usr/bin/env python3
"""Publish a Tableau workbook (.twb or .twbx) from your computer to Tableau Cloud or Server.

Story 1, part 1b: the AI-built, robust version of 1a. See README.md here.

Run it with no arguments for the interactive flow:
  1. pick a workbook file from a numbered folder list (or paste a path)
  2. pick a project, drilling into sub-projects as deep as they go
  3. name the workbook (and decide what to do if that name is taken)
  4. review everything, then publish

Any step can be answered up front with a flag, and --yes skips all prompts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.files import (  # noqa: E402
    clean_path_input,
    default_start_dir,
    display_path,
    human_size,
    pick_workbook,
    validate_workbook_file,
)
from tcs.projects import Project, fetch_project_tree, resolve_project  # noqa: E402
from tcs.publishing import PublishPlan, describe_existing, find_workbook, publish_workbook  # noqa: E402

EXAMPLES = """\
examples:
  Interactive:
    python 01-publishing-workbooks/1b_publish_workbook.py

  Pre-fill some answers and get asked for the rest:
    python 01-publishing-workbooks/1b_publish_workbook.py --file ~/Desktop/Sales.twbx

  Fully non-interactive (CI, scheduled jobs):
    python 01-publishing-workbooks/1b_publish_workbook.py --file Sales.twbx --project "Finance/Reporting" \\
        --name "Sales Overview" --overwrite --yes

  Use another site's credentials:
    python 01-publishing-workbooks/1b_publish_workbook.py --env-file .env.work
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="1b_publish_workbook.py",
        description="Publish a .twb or .twbx workbook to Tableau Cloud or Tableau Server.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file", metavar="PATH", help="workbook to publish; skips the file picker")
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--project",
        metavar="PATH",
        help='target project as a path, e.g. "Finance/Reporting/Monthly"; skips the project picker',
    )
    where.add_argument("--project-id", metavar="ID", help="target project by its id instead of its path")
    parser.add_argument("--name", help="name for the workbook on Tableau (default: the file name)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace a workbook with the same name in the target project without asking",
    )
    parser.add_argument("--show-tabs", action="store_true", help="show sheets as tabs on Tableau")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="no prompts at all; needs --file and --project/--project-id, and stops "
        "instead of asking if the name is taken and --overwrite wasn't given",
    )
    parser.add_argument("--dry-run", action="store_true", help="do everything except publish")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    if args.yes and not (args.file and (args.project or args.project_id)):
        parser.error("--yes needs --file and either --project or --project-id")
    if args.name is not None and not args.name.strip():
        parser.error("--name can't be empty")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. Nothing was published.")
        return 130
    except (ConfigError, TableauError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Publish a workbook to Tableau")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    # 1. The workbook file. Chosen before signing in: it's all local.
    if args.file:
        workbook_file = validate_workbook_file(clean_path_input(args.file))
    else:
        workbook_file = pick_workbook(default_start_dir(settings.default_workbook_dir))
    ui.success(f"Workbook file: {display_path(workbook_file)} ({human_size(workbook_file.stat().st_size)})")

    with TableauClient(settings) as client:
        user = client.current_user_name()
        ui.success(
            f"Signed in to {settings.site_label}"
            + (f" as {user}" if user else "")
            + f" (REST API {client.api_version})"
        )

        # 2. The target project.
        tree = fetch_project_tree(client)
        if not len(tree):
            raise LookupError("Your account can't see any projects on this site.")
        project = resolve_project(tree, args.project, args.project_id)
        project_path = tree.path(project.id)
        ui.success(f"Project: {project_path}")

        # 3. The workbook's name, and what to do if it's taken.
        name = (args.name or "").strip() or (
            workbook_file.stem
            if args.yes
            else ui.ask_required("Name for the workbook on Tableau", default=workbook_file.stem)
        )
        name, overwrite, existing = _settle_name(client, args, project, project_path, name)

        plan = PublishPlan(
            file=workbook_file,
            name=name,
            project=project,
            project_path=project_path,
            overwrite=overwrite,
            show_tabs=args.show_tabs,
        )

        # 4. Review, then publish.
        _print_review(plan, existing, settings)
        if args.dry_run:
            ui.warn("Dry run: nothing was published.")
            return 0
        if not args.yes and not ui.confirm("Publish now?"):
            raise ui.Cancelled()

        ui.heading("Publishing")
        try:
            workbook = publish_workbook(client, plan, progress=ui.detail)
        except TableauError as exc:
            if exc.status == 409 and not plan.overwrite:
                raise TableauError(
                    f'A workbook named "{plan.name}" now exists in {project_path} '
                    "(it may have just been published). Run again and choose to overwrite it."
                ) from None
            raise

    ui.success(f'Published "{workbook.get("name", plan.name)}" to {project_path}')
    if workbook.get("webpageUrl"):
        ui.info(f"  {workbook['webpageUrl']}")
    return 0


def _settle_name(
    client: TableauClient,
    args: argparse.Namespace,
    project: Project,
    project_path: str,
    name: str,
) -> Tuple[str, bool, Optional[Dict[str, Any]]]:
    """Check for a same-named workbook in the project. Returns (name, overwrite, existing)."""
    while True:
        existing = find_workbook(client, project, name)
        if not existing or args.overwrite:
            return name, args.overwrite, existing
        if args.yes:
            raise ValueError(
                f'A workbook named "{existing["name"]}" already exists in {project_path}. '
                "Add --overwrite to replace it, or pass a different --name."
            )
        ui.warn(f'A workbook named "{existing["name"]}" already exists in {project_path}{describe_existing(existing)}.')
        choice = ui.menu(["Overwrite it", "Use a different name"], bottom=[("q", "Cancel")])
        if choice == 0:
            return name, True, existing
        name = ui.ask_required("New name for the workbook")


def _print_review(plan: PublishPlan, existing: Optional[Dict[str, Any]], settings: Settings) -> None:
    ui.heading("Review")
    if existing:
        outcome = ui.style(f"Replaces the existing workbook{describe_existing(existing)}", "yellow")
    else:
        outcome = "Creates a new workbook"
    upload = (
        f"Chunked upload in {plan.chunk_count} parts of up to {human_size(plan.chunk_size)} (file is over 64 MB)"
        if plan.chunked
        else "Single request"
    )
    ui.table(
        [
            ("File", f"{display_path(plan.file)} ({human_size(plan.size)})"),
            ("Publish as", plan.name),
            ("Project", plan.project_path),
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Result", outcome),
            ("Sheets as tabs", "Yes" if plan.show_tabs else "No"),
            ("Upload", upload),
        ]
    )
    if plan.workbook_type == "twb":
        ui.warn(
            "This is a .twb, which doesn't package any data. It will only work on Tableau if its data "
            "comes from a database or published data source the server can reach. "
            "Save as .twbx to include extracts and local files."
        )


if __name__ == "__main__":
    sys.exit(main())
