#!/usr/bin/env python3
"""Add users to groups from a CSV, creating any groups that don't exist yet. It never removes anyone.

Entry 09 of the starter pack. See README.md here.

The CSV needs a column of users (usernames or email addresses) and, optionally,
a column of group names. Without a group column, every user goes into one
group you name. Excel's CSV encodings are read correctly.

Run it with no arguments for the interactive flow:
  1. point it at the CSV (and name the group if the CSV has no group column)
  2. users and groups are matched against the site (read-only)
  3. review: who'll be added to which group, who's already a member, who
     isn't on the site, and which groups will be created; then confirm
  4. groups are created, then members added, one at a time

A results CSV records what happened to every row. The input file is never
modified.

Any step can be answered up front with a flag, and --yes skips all prompts.

Safeguards:
  * Only adds. Nobody is removed from any group, so a wrong CSV can't empty one.
  * Creating groups needs your confirmation, or --create-groups with --yes:
    a typo in a group name shouldn't quietly create a new group.
  * Users who aren't on the site are reported, not created.
  * A group set to grant a licence on sign-in is called out, because adding
    people to it can change what they're licensed for.
  * One failure doesn't stop the rest; the exit code is 1 if anything failed.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.batch import run_batch, summary  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import group_members, list_groups, list_users  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, find_column, read_csv, report_path, write_csv  # noqa: E402

USER_COLUMNS = ["username", "user", "user name", "email", "email address", "name"]
GROUP_COLUMNS = ["group", "group name", "groupname"]
ADD, MEMBER, NO_USER = "Add", "Already a member", "User not on site"
ADDED, FAILED, GROUP_FAILED = "Added", "Failed", "Group not created"
RESULT_COLUMNS = ["Group", "User", "Matched User", "Status", "Detail", "Group ID", "User ID"]

EXAMPLES = """\
examples:
  Interactive:
    python 09-groups-from-csv/09_groups_from_csv.py

  Everyone in a list into one group:
    python 09-groups-from-csv/09_groups_from_csv.py --csv pilot_users.csv --group "Pilot Users" --dry-run

  A CSV with user and group columns, creating missing groups, no prompts:
    python 09-groups-from-csv/09_groups_from_csv.py --csv access.csv --create-groups --yes
"""


@dataclass
class Pair:
    group: str  # as written in the CSV (or --group)
    user: str  # as written in the CSV
    status: str = ADD
    user_id: str = ""
    matched: str = ""
    group_id: str = ""
    detail: str = ""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="09_groups_from_csv.py",
        description="Add users to groups from a CSV. Never removes anyone.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", metavar="PATH", help="the CSV of users (and optionally groups)")
    parser.add_argument("--group", metavar="NAME", help="put every user into this group (when the CSV has no group column)")
    parser.add_argument("--user-column", metavar="NAME", help="the column holding usernames or emails (default: detected)")
    parser.add_argument("--group-column", metavar="NAME", help="the column holding group names (default: detected)")
    parser.add_argument("--create-groups", action="store_true", help="with --yes: allow creating groups that don't exist")
    parser.add_argument("--output-dir", metavar="PATH", help=f"folder for the results CSV (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts; needs --csv")
    parser.add_argument("--dry-run", action="store_true", help="show the review, but change nothing and write no file")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)
    if args.yes and not args.csv:
        parser.error("--yes needs --csv")
    if args.group is not None and not args.group.strip():
        parser.error("--group can't be empty")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. If the run had started, the results file shows what was added.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Add users to groups from a CSV")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    # The file first: a bad CSV should fail before signing in.
    pairs = read_pairs(args)

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        users = list_users(client)
        groups = {g.get("name", "").casefold(): g for g in list_groups(client)}
        wanted = {p.group.casefold() for p in pairs}
        members = {
            groups[name]["id"]: {u["id"] for u in group_members(client, groups[name]["id"])}
            for name in wanted if name in groups
        }
        new_groups = match(pairs, users, groups, members)

        out_file = report_path(clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR,
                               "groups_from_csv", settings.site_label, datetime.now())
        _print_review(settings, pairs, groups, new_groups, out_file)
        to_add = [p for p in pairs if p.status == ADD]
        if not to_add:
            ui.warn("Nothing to add.")
            return 0
        if args.dry_run:
            ui.warn("Dry run: nothing was changed and no file was written.")
            return 0
        if new_groups and args.yes and not args.create_groups:
            raise ValueError(
                f"{_n(len(new_groups), 'group')} would be created ({', '.join(new_groups)}). "
                "Add --create-groups to allow it, or fix the names."
            )
        if not args.yes and not ui.confirm(f"Add {_n(len(to_add), 'membership')}" + (f" and create {_n(len(new_groups), 'group')}" if new_groups else "") + "?"):
            raise ui.Cancelled()

        try:
            outcomes = []
            if new_groups:
                ui.heading("Creating groups")
                created = run_batch(new_groups, lambda name: create_group(client, name, pairs), lambda name: name)
                outcomes += created
                for outcome in created:
                    if not outcome.ok:
                        for pair in pairs:
                            if pair.group.casefold() == outcome.item.casefold() and pair.status == ADD:
                                pair.status, pair.detail = GROUP_FAILED, outcome.detail
            to_add = [p for p in pairs if p.status == ADD]
            ui.heading("Adding members")
            outcomes += run_batch(to_add, lambda p: add_member(client, p), lambda p: f"{p.matched} -> {p.group}")
        finally:
            write_results(out_file, pairs)

    code = summary(outcomes, "Done")
    ui.info(f"  Results: {display_path(out_file)}")
    return code


def read_pairs(args: argparse.Namespace) -> List[Pair]:
    path = clean_path_input(args.csv or ui.ask_required("Path to the CSV file"))
    columns, rows = read_csv(path)
    user_col = args.user_column or find_column(columns, USER_COLUMNS)
    if not user_col or user_col not in columns:
        raise ValueError(f"No user column found in {display_path(path)}. Columns: {', '.join(columns)}. Use --user-column.")
    group_col = args.group_column or (None if args.group else find_column(columns, GROUP_COLUMNS))
    if args.group_column and args.group_column not in columns:
        raise ValueError(f"No column named {args.group_column!r} in {display_path(path)}.")
    fixed_group = args.group
    if not group_col and not fixed_group:
        if args.yes:
            raise ValueError("The CSV has no group column. Pass --group NAME.")
        fixed_group = ui.ask_required("The CSV has no group column. Group to add everyone to")
    seen = set()
    pairs = []
    for row in rows:
        user = row.get(user_col, "").strip()
        group = (fixed_group or row.get(group_col or "", "")).strip()
        if not user or not group or (group.casefold(), user.casefold()) in seen:
            continue
        seen.add((group.casefold(), user.casefold()))
        pairs.append(Pair(group, user))
    if not pairs:
        raise ValueError(f"{display_path(path)} has no rows with both a user and a group.")
    ui.success(f"{_n(len(pairs), 'user-group pair')} in {display_path(path)}")
    return pairs


def match(
    pairs: List[Pair], users: List[Dict[str, Any]], groups: Dict[str, Dict[str, Any]], members: Dict[str, set]
) -> List[str]:
    """Fill in each pair's user and group; return the names of groups to create."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for user in users:  # username wins over email if both could match
        if user.get("email"):
            by_key.setdefault(user["email"].casefold(), user)
    for user in users:
        by_key[user.get("name", "").casefold()] = user
    new_groups: List[str] = []
    for pair in pairs:
        user = by_key.get(pair.user.casefold())
        group = groups.get(pair.group.casefold())
        if group:
            pair.group, pair.group_id = group["name"], group["id"]
        elif pair.group.casefold() not in {g.casefold() for g in new_groups}:
            new_groups.append(pair.group)
        if not user:
            pair.status, pair.detail = NO_USER, "no user with this username or email"
            continue
        pair.user_id, pair.matched = user["id"], user.get("name", "")
        if group and user["id"] in members.get(group["id"], set()):
            pair.status = MEMBER
    return new_groups


def create_group(client: TableauClient, name: str, pairs: List[Pair]) -> str:
    """POST /sites/{site-id}/groups. A local group, no licence-on-sign-in setting."""
    data = client.request("POST", "/groups", action="Create group", json={"group": {"name": name}}).json()
    group_id = data["group"]["id"]
    for pair in pairs:
        if pair.group.casefold() == name.casefold():
            pair.group_id = group_id
    return "created"


def add_member(client: TableauClient, pair: Pair) -> str:
    """POST /sites/{site-id}/groups/{group-id}/users. A 409 means someone else added them first: fine."""
    try:
        client.request("POST", f"/groups/{pair.group_id}/users", action="Add user to group", json={"user": {"id": pair.user_id}})
    except TableauError as exc:
        if exc.status == 409:
            pair.status = MEMBER
            return "already a member"
        pair.status, pair.detail = FAILED, str(exc)
        raise
    pair.status = ADDED
    return ""


def write_results(path: Path, pairs: List[Pair]) -> None:
    write_csv(
        path,
        [
            {
                "Group": p.group,
                "User": p.user,
                "Matched User": p.matched,
                "Status": p.status if p.status != ADD else "Not reached",
                "Detail": p.detail,
                "Group ID": p.group_id,
                "User ID": p.user_id,
            }
            for p in pairs
        ],
        RESULT_COLUMNS,
    )


def _print_review(
    settings: Settings, pairs: List[Pair], groups: Dict[str, Dict[str, Any]], new_groups: List[str], out_file: Path
) -> None:
    per_group: Dict[str, Counter] = defaultdict(Counter)
    for pair in pairs:
        per_group[pair.group][pair.status] += 1
    missing = [p.user for p in pairs if p.status == NO_USER]
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("To add", str(sum(1 for p in pairs if p.status == ADD))),
            ("Already members", str(sum(1 for p in pairs if p.status == MEMBER))),
            ("Not on site", str(len(missing))),
            ("New groups", ", ".join(new_groups) or "none"),
            ("Results", display_path(out_file)),
        ]
    )
    print()
    for group, counts in sorted(per_group.items(), key=lambda kv: kv[0].casefold()):
        tag = ui.style(" (new)", "yellow") if group in new_groups else ""
        print(f"  {group}{tag}: {counts[ADD]} to add, {counts[MEMBER]} already in, {counts[NO_USER]} not on site")
        grant = (groups.get(group.casefold()) or {}).get("import") or {}
        if grant.get("siteRole"):
            ui.warn(
                f"{group} grants the {grant['siteRole']} role when members sign in ({grant.get('grantLicenseMode')}). "
                "Adding people can change their licence."
            )
    if missing:
        shown = ", ".join(missing[:8]) + (f" and {len(missing) - 8} more" if len(missing) > 8 else "")
        ui.warn(f"Not on this site: {shown}")


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
