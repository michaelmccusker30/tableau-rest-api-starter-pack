#!/usr/bin/env python3
"""Every user and group on a Tableau site, who's in what, and who needs a look, as CSV.

Entry 05 of the starter pack. See README.md here.

It writes three files:
  * users_<site>_<date>.csv: one row per user, with site role, sign-in method,
    last sign-in and days since, their groups, and how many workbooks and
    data sources they own
  * groups_<site>_<date>.csv: one row per group, with member count and any
    licence-on-sign-in setting
  * memberships_<site>_<date>.csv: one row per user per group, ready to pivot

The review calls out the users worth a look: unlicensed, never signed in,
not signed in for --stale-days (90 by default), and in no group besides All
Users. Those are the usual starting points for a licence review.

It never changes anything on the site.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import group_members, list_datasources, list_groups, list_users, list_workbooks  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv  # noqa: E402

ALL_USERS = "All Users"  # the built-in group every user belongs to
UNLICENSED = "Unlicensed"
DEFAULT_STALE_DAYS = 90

USER_COLUMNS = [
    "Name",
    "Username",
    "Email",
    "Site Role",
    "Sign-in",
    "Last Login (UTC)",
    "Days Since Login",
    "Groups",
    "Group Count",
    "Workbooks Owned",
    "Data Sources Owned",
    "User ID",
]
GROUP_COLUMNS = ["Group", "Members", "Domain", "Licence On Sign-in", "Group ID"]
MEMBERSHIP_COLUMNS = ["Group", "Name", "Username", "Email", "Site Role", "Group ID", "User ID"]

EXAMPLES = """\
examples:
  Interactive (one confirmation):
    python 05-users-and-groups/05_users_and_groups.py

  No prompts, a year as the stale threshold:
    python 05-users-and-groups/05_users_and_groups.py --stale-days 365 --yes
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="05_users_and_groups.py",
        description="Every user and group, who's in what, and who needs a look, as CSV.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        metavar="N",
        help=f"call out users who haven't signed in for N days or more (default: {DEFAULT_STALE_DAYS})",
    )
    parser.add_argument(
        "--output-dir", metavar="PATH", help=f"folder for the CSV files (default: ./{DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts at all")
    parser.add_argument("--dry-run", action="store_true", help="show the review, but write no files")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)
    if args.stale_days < 1:
        parser.error("--stale-days must be 1 or more")
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
    ui.heading("Users and groups")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        users = list_users(client)
        groups = list_groups(client)
        members = {g["id"]: group_members(client, g["id"]) for g in groups}  # one call per group
        workbooks_owned = Counter((wb.get("owner") or {}).get("id") for wb in list_workbooks(client))
        datasources_owned = Counter((ds.get("owner") or {}).get("id") for ds in list_datasources(client))
    ui.success(f"Read {_n(len(users), 'user')} and {_n(len(groups), 'group')}")

    now = datetime.now(timezone.utc)
    user_rows = build_user_rows(users, groups, members, workbooks_owned, datasources_owned, now)
    group_rows = build_group_rows(groups, members)
    membership_rows = build_membership_rows(groups, members)

    folder = clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    stamp = datetime.now()
    files = {
        stem: report_path(folder, stem, settings.site_label, stamp) for stem in ("users", "groups", "memberships")
    }
    _print_review(settings, user_rows, group_rows, membership_rows, args.stale_days, files)
    if args.dry_run:
        ui.warn("Dry run: no files were written.")
        return 0
    if not args.yes and not ui.confirm("Write these 3 files?", default=True):
        raise ui.Cancelled()

    write_csv(files["users"], user_rows, USER_COLUMNS)
    write_csv(files["groups"], group_rows, GROUP_COLUMNS)
    write_csv(files["memberships"], membership_rows, MEMBERSHIP_COLUMNS)
    for stem, path in files.items():
        ui.success(f"{stem.capitalize()}: {display_path(path)}")
    return 0


def build_user_rows(
    users: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    members: Dict[str, List[Dict[str, Any]]],
    workbooks_owned: Counter,
    datasources_owned: Counter,
    now: datetime,
) -> List[Dict[str, Any]]:
    """One row per user, sorted by name."""
    groups_of: Dict[str, List[str]] = defaultdict(list)
    for group in groups:
        if group.get("name") == ALL_USERS:
            continue
        for member in members.get(group["id"], []):
            groups_of[member["id"]].append(group.get("name", ""))
    rows = []
    for user in users:
        names = sorted(groups_of.get(user["id"], []), key=str.casefold)
        rows.append(
            {
                "Name": user.get("fullName") or user.get("name", ""),
                "Username": user.get("name", ""),
                "Email": user.get("email", ""),
                "Site Role": user.get("siteRole", ""),
                "Sign-in": user.get("authSetting", ""),
                "Last Login (UTC)": (user.get("lastLogin") or "").replace("T", " ").rstrip("Z"),
                "Days Since Login": _days_since(user.get("lastLogin"), now),
                "Groups": ", ".join(names),
                "Group Count": len(names),
                "Workbooks Owned": workbooks_owned[user["id"]],
                "Data Sources Owned": datasources_owned[user["id"]],
                "User ID": user["id"],
            }
        )
    rows.sort(key=lambda r: (r["Name"].casefold(), r["Username"].casefold()))
    return rows


def build_group_rows(groups: List[Dict[str, Any]], members: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for group in sorted(groups, key=lambda g: g.get("name", "").casefold()):
        grant = group.get("import") or {}
        licence = f"{grant.get('siteRole')} ({grant.get('grantLicenseMode')})" if grant.get("siteRole") else ""
        rows.append(
            {
                "Group": group.get("name", ""),
                "Members": len(members.get(group["id"], [])),
                "Domain": (group.get("domain") or {}).get("name", ""),
                "Licence On Sign-in": licence,
                "Group ID": group["id"],
            }
        )
    return rows


def build_membership_rows(groups: List[Dict[str, Any]], members: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for group in sorted(groups, key=lambda g: g.get("name", "").casefold()):
        for member in sorted(members.get(group["id"], []), key=lambda u: (u.get("fullName") or u.get("name", "")).casefold()):
            rows.append(
                {
                    "Group": group.get("name", ""),
                    "Name": member.get("fullName") or member.get("name", ""),
                    "Username": member.get("name", ""),
                    "Email": member.get("email", ""),
                    "Site Role": member.get("siteRole", ""),
                    "Group ID": group["id"],
                    "User ID": member["id"],
                }
            )
    return rows


def _print_review(
    settings: Settings,
    users: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
    memberships: List[Dict[str, Any]],
    stale_days: int,
    files: Dict[str, Path],
) -> None:
    roles = Counter(u["Site Role"] for u in users)
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Users", f"{len(users)}: " + ", ".join(f"{count} {role}" for role, count in roles.most_common())),
            ("Groups", f"{len(groups)} ({len(memberships)} memberships)"),
            ("Files", "\n".join(display_path(p) for p in files.values())),
        ]
    )
    checks = [
        ("unlicensed", [u for u in users if u["Site Role"] == UNLICENSED]),
        ("never signed in", [u for u in users if u["Days Since Login"] == ""]),
        (f"not signed in for {stale_days}+ days", [u for u in users if u["Days Since Login"] != "" and u["Days Since Login"] >= stale_days]),
        (f"in no group besides {ALL_USERS}", [u for u in users if not u["Group Count"]]),
    ]
    print()
    ui.detail("  Worth a look:")
    for label, found in checks:
        line = f"{_n(len(found), 'user')} {label}"
        if found:
            names = ", ".join(u["Name"] for u in found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
            ui.warn(f"{line}: {names}")
        else:
            ui.info(f"  {line}")


def _days_since(stamp: Optional[str], now: datetime) -> Any:
    if not stamp:
        return ""
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return max((now - when).days, 0)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
