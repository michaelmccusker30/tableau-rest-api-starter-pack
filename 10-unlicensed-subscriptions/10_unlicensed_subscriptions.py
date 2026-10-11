#!/usr/bin/env python3
"""Delete the subscriptions that belong to unlicensed users, after backing every one up to CSV.

Entry 10 of the starter pack. See README.md here.

When someone loses their licence, their subscriptions stay behind: scheduled
emails that can no longer be delivered, still taking a slot in the schedule.
This finds them and, once you've reviewed them, deletes them.

How it keeps API calls down: it lists the subscriptions first, then looks up
only the users who own one, instead of every user on the site.

Run it with no arguments for the interactive flow:
  1. subscriptions are listed, and their owners' site roles checked (read-only)
  2. review: which unlicensed users have which subscriptions
  3. confirm; a backup CSV of every subscription to delete is written first,
     then they're deleted one at a time

--dry-run stops at the review; --yes backs up and deletes without asking.

Safeguards:
  * The backup is written before the first delete, with everything needed to
    recreate a subscription by hand: subject, message, content, schedule,
    format. Deleted subscriptions can't be restored through the API.
  * Only Unlicensed owners. A user who simply hasn't signed in for a while
    keeps their subscriptions.
  * The confirmation defaults to No.
  * One failure doesn't stop the rest; the exit code is 1 if anything failed,
    and the backup records each outcome.
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
from tcs.batch import run_batch, summary  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_subscriptions, list_views, list_workbooks  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv  # noqa: E402

UNLICENSED = "Unlicensed"
BACKUP_COLUMNS = [
    "Subject",
    "User",
    "Username",
    "Content Type",
    "Content",
    "Schedule",
    "Attach Image",
    "Attach PDF",
    "Suspended",
    "Message",
    "Status",
    "Detail",
    "Subscription ID",
    "User ID",
    "Content ID",
    "Schedule ID",
]

EXAMPLES = """\
examples:
  See what would be deleted:
    python 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py --dry-run

  Interactive (review, then confirm):
    python 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py

  Scheduled clean-up, no prompts:
    python 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py --yes
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="10_unlicensed_subscriptions.py",
        description="Delete subscriptions owned by unlicensed users, after backing them up to CSV.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", metavar="PATH", help=f"folder for the backup CSV (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts: back up, then delete")
    parser.add_argument("--dry-run", action="store_true", help="show the review, but delete nothing and write no file")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. If deleting had started, the backup CSV shows what was deleted.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Subscriptions owned by unlicensed users")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        subscriptions = list_subscriptions(client)
        if not subscriptions:
            ui.success("No subscriptions on this site. Nothing to do.")
            return 0
        subscriber_ids = sorted({(s.get("user") or {}).get("id", "") for s in subscriptions} - {""})
        ui.success(f"{_n(len(subscriptions), 'subscription')} from {_n(len(subscriber_ids), 'user')}; checking their licences")
        users = lookup_users(client, subscriber_ids)

        doomed = [s for s in subscriptions if users.get((s.get("user") or {}).get("id"), {}).get("siteRole") == UNLICENSED]
        names = content_names(client) if doomed else {}
        rows = [backup_row(s, users, names) for s in doomed]

        backup = report_path(clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR,
                             "unlicensed_subscriptions", settings.site_label, datetime.now())
        _print_review(settings, subscriptions, subscriber_ids, rows, backup)
        if not rows:
            ui.success("No subscriptions belong to unlicensed users.")
            return 0
        if args.dry_run:
            ui.warn("Dry run: nothing was deleted and no file was written.")
            return 0
        if not args.yes and not ui.confirm(f"Back up and delete {_n(len(rows), 'subscription')}?"):
            raise ui.Cancelled()

        for row in rows:
            row["Status"] = "To delete"
        write_csv(backup, rows, BACKUP_COLUMNS)  # before the first delete
        ui.detail(f"  Backup: {display_path(backup)}")
        ui.heading("Deleting")
        try:
            outcomes = run_batch(rows, lambda r: delete(client, r), lambda r: f"{r['User']}: {r['Subject']}")
        finally:
            write_csv(backup, rows, BACKUP_COLUMNS)

    code = summary(outcomes, "Deleted")
    ui.info(f"  Backup: {display_path(backup)}")
    return code


def lookup_users(client: TableauClient, user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """GET /users/{id} for each subscriber only. A user that's gone is left out (and so isn't treated as unlicensed)."""
    found: Dict[str, Dict[str, Any]] = {}
    for user_id in user_ids:
        try:
            found[user_id] = client.get_json(f"/users/{user_id}", action="Look up user").get("user") or {}
        except TableauError as exc:
            ui.warn(f"Couldn't look up user {user_id}: {exc}")
    return found


def content_names(client: TableauClient) -> Dict[str, str]:
    """Workbook and view names by id, for a readable backup. Only fetched when there's something to delete."""
    names = {wb["id"]: wb.get("name", "") for wb in list_workbooks(client)}
    workbook_names = dict(names)
    for view in list_views(client):
        workbook = workbook_names.get((view.get("workbook") or {}).get("id"), "")
        names[view["id"]] = f"{workbook} / {view.get('name', '')}" if workbook else view.get("name", "")
    return names


def backup_row(sub: Dict[str, Any], users: Dict[str, Dict[str, Any]], names: Dict[str, str]) -> Dict[str, Any]:
    user = users.get((sub.get("user") or {}).get("id"), {})
    content = sub.get("content") or {}
    schedule = sub.get("schedule") or {}
    return {
        "Subject": sub.get("subject", ""),
        "User": user.get("fullName") or user.get("name", ""),
        "Username": user.get("name", ""),
        "Content Type": content.get("type", ""),
        "Content": names.get(content.get("id", ""), content.get("name", "")),
        "Schedule": schedule.get("name") or _frequency(sub),
        "Attach Image": _yes(sub.get("attachImage")),
        "Attach PDF": _yes(sub.get("attachPdf")),
        "Suspended": _yes(sub.get("suspended")),
        "Message": sub.get("message", ""),
        "Status": "",
        "Detail": "",
        "Subscription ID": sub.get("id", ""),
        "User ID": user.get("id", ""),
        "Content ID": content.get("id", ""),
        "Schedule ID": schedule.get("id", ""),
    }


def delete(client: TableauClient, row: Dict[str, Any]) -> str:
    """DELETE /sites/{site-id}/subscriptions/{id}."""
    try:
        client.request("DELETE", f"/subscriptions/{row['Subscription ID']}", action="Delete subscription")
    except TableauError as exc:
        row["Status"], row["Detail"] = "Failed", str(exc)
        raise
    row["Status"] = "Deleted"
    return ""


def _print_review(
    settings: Settings, subscriptions: List[Dict[str, Any]], subscriber_ids: List[str], rows: List[Dict[str, Any]], backup: Path
) -> None:
    per_user = Counter(r["User"] for r in rows)
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Subscriptions", f"{len(subscriptions)} from {_n(len(subscriber_ids), 'user')}"),
            ("To delete", f"{len(rows)}, owned by {_n(len(per_user), 'unlicensed user')}"),
            ("Backup", display_path(backup)),
        ]
    )
    if rows:
        print()
        for user, count in per_user.most_common():
            print(f"  {user}  {ui.style(_n(count, 'subscription'), 'dim')}")
            for row in [r for r in rows if r["User"] == user][:5]:
                print(f"      {row['Subject']}  {ui.style(row['Content'] + (' · ' + row['Schedule'] if row['Schedule'] else ''), 'dim')}")


def _frequency(sub: Dict[str, Any]) -> str:
    """Newer Tableau Cloud subscriptions carry their own frequency instead of a named schedule."""
    details = sub.get("frequencyDetails") or {}
    return " ".join(x for x in (sub.get("frequency", ""), details.get("start", "")) if x)


def _yes(value: Any) -> str:
    return "Yes" if str(value).lower() == "true" else "No"


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
