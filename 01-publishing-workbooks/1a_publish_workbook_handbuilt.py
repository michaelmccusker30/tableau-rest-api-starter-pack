"""Publish a workbook to Tableau Cloud or Server with Tableau Server Client (TSC).

Story 1, part 1a: simple and effective. Then 1b (robust) and 1c (bulk). See README.md here.

HUMAN-BUILT. Michael McCusker wrote this while he was learning the Tableau REST
API, before this repo existed. It's kept as a direct comparison with
1b_publish_workbook.py, which Claude wrote and Michael tested. Both do the
same core job; this entry's README.md sets them side by side.

Promoted into the repo on 2026-10-05 with as few changes as possible. Each change
is marked in the code with a "Promoted:" comment:
  1. Credentials come from this repo's .env through tcs.config.load_settings(),
     instead of the old config.connect_to_cloud(). It now works on Tableau Server too.
  2. --env-file picks a different settings file, like every script in the repo.
  3. Projects are read with TSC.Pager, so sites with more than 100 projects work.
  4. If two projects share the target name, it stops and lists them instead of
     publishing to whichever came first.

Needs one extra package:  pip install -r requirements-dev.txt  (or: pip install tableauserverclient)

Usage:
    python 01-publishing-workbooks/1a_publish_workbook_handbuilt.py "Sales.twbx" "Finance"
    python 01-publishing-workbooks/1a_publish_workbook_handbuilt.py "Sales.twbx" "Finance" --mode Overwrite
"""

import tableauserverclient as TSC
import argparse
import os
import sys
from pathlib import Path

# Promoted: load settings with the repo's tcs.config instead of the old config.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tcs.config import load_settings  # noqa: E402


def connect_to_tableau(env_file: str = None):
    """Promoted: replaces config.connect_to_cloud(). Signs in with the PAT from .env."""
    settings = load_settings(env_file)
    tableau_auth = TSC.PersonalAccessTokenAuth(
        settings.pat_name, settings.pat_secret, site_id=settings.site_name
    )
    server = TSC.Server(
        settings.server_url,
        use_server_version=True,
        http_options={"verify": settings.verify_ssl},
    )
    server.auth.sign_in(tableau_auth)
    return server


def publish_workbook(
    workbook_path: str,
    project_name: str,
    workbook_name: str = None,
    mode: str = "CreateNew",
    show_tabs: bool = True,
    env_file: str = None,
):
    """
    Publish a workbook to Tableau Cloud.

    Args:
        workbook_path: Path to the .twb or .twbx file
        project_name: Name of the target project on Tableau Cloud
        workbook_name: Optional custom name for the workbook (defaults to filename)
        mode: Publishing mode - 'CreateNew' or 'Overwrite' (default: 'CreateNew')
        show_tabs: Whether to show sheet tabs (default: True)
        env_file: Optional settings file to use instead of .env

    Returns:
        Published workbook object
    """

    # Validate file exists
    if not os.path.exists(workbook_path):
        raise FileNotFoundError(f"Workbook file not found: {workbook_path}")

    # Validate file extension
    if not workbook_path.lower().endswith((".twb", ".twbx")):
        raise ValueError("File must be a .twb or .twbx file")

    # Use filename as workbook name if not provided
    if workbook_name is None:
        workbook_name = os.path.splitext(os.path.basename(workbook_path))[0]

    print(f"Publishing workbook: {workbook_name}")
    print(f"File: {workbook_path}")
    print(f"Target project: {project_name}")
    print(f"Mode: {mode}")

    # Connect to Tableau Cloud
    server = connect_to_tableau(env_file)

    try:
        # Find the target project
        # Promoted: TSC.Pager reads every page. projects.get() only returned the first 100.
        all_projects = list(TSC.Pager(server.projects))
        matches = [p for p in all_projects if p.name == project_name]

        if not matches:
            available_projects = [p.name for p in all_projects]
            raise ValueError(
                f"Project '{project_name}' not found. Available projects: {available_projects}"
            )

        # Promoted: sub-projects can share a name (e.g. "Reporting" under Finance and
        # under Sales). Stop rather than publish to whichever one came back first.
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} projects are named '{project_name}' (ids: "
                f"{', '.join(p.id for p in matches)}). Rename one, or use "
                "1b_publish_workbook.py, which lets you pick by path."
            )
        target_project = matches[0]

        print(f"Found project: {target_project.name} (ID: {target_project.id})")

        # Create workbook item
        new_workbook = TSC.WorkbookItem(project_id=target_project.id)
        new_workbook.name = workbook_name
        new_workbook.show_tabs = show_tabs

        # Publish the workbook
        print("Uploading workbook...")
        published_workbook = server.workbooks.publish(
            workbook_item=new_workbook,
            file=workbook_path,
            mode=(
                TSC.Server.PublishMode.Overwrite
                if mode == "Overwrite"
                else TSC.Server.PublishMode.CreateNew
            ),
        )

        print(f"✓ Successfully published workbook!")
        print(f"  Name: {published_workbook.name}")
        print(f"  ID: {published_workbook.id}")
        print(f"  Project: {published_workbook.project_name}")
        print(f"  Web URL: {published_workbook.webpage_url}")

        return published_workbook

    finally:
        # Sign out when done
        server.auth.sign_out()
        print("Signed out from Tableau Cloud")


def main(argv=None):  # Promoted: argv lets the tests call main() directly
    parser = argparse.ArgumentParser(description="Publish a workbook to Tableau Cloud")
    parser.add_argument("workbook_path", help="Path to the .twb or .twbx file")
    parser.add_argument(
        "project_name", help="Name of the target project on Tableau Cloud"
    )
    parser.add_argument(
        "--name",
        help="Custom name for the workbook (defaults to filename)",
        default=None,
    )
    parser.add_argument(
        "--mode",
        choices=["CreateNew", "Overwrite"],
        default="CreateNew",
        help="Publishing mode (default: CreateNew)",
    )
    parser.add_argument(
        "--hide-tabs",
        action="store_true",
        help="Hide sheet tabs in the published workbook",
    )
    # Promoted: choose a settings file, as every script in this repo can.
    parser.add_argument("--env-file", help="Settings file to use instead of .env")

    args = parser.parse_args(argv)

    try:
        publish_workbook(
            workbook_path=args.workbook_path,
            project_name=args.project_name,
            workbook_name=args.name,
            mode=args.mode,
            show_tabs=not args.hide_tabs,
            env_file=args.env_file,
        )
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
