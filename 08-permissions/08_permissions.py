#!/usr/bin/env python3
"""Permissions: report every explicit rule, and grant, revoke or restore them safely.

Entry 08 of the starter pack. See README.md here.

Report (the default; read-only)
  Every explicit rule on projects, on projects' default rules for new
  workbooks, and on workbooks, with group and user names instead of IDs. One
  row per grantee per capability, ready to pivot. The review calls out rules
  granted to individual users rather than groups, and Deny rules.

Grant (--grant view|explore|publish|administer|custom)
  Give users (named, or listed in a CSV) and/or groups one of Tableau's
  permission templates, or named capabilities, on chosen workbooks, projects,
  or projects' default rules. A capability set the other way (Deny instead of
  Allow) is replaced.

Revoke (--revoke)
  Remove those users' or groups' rules from the chosen items: all of them, or
  only the --capability names given.

Restore (--restore BACKUP)
  Put the items in a backup back exactly as they were.

Every change works the same way: read each item's current rules, work out what
they should be, show you the difference, and on your go-ahead save a backup of
the current rules (in the report's own format) before deleting and adding only
what differs. The backup is what --restore reads.

Run it with no arguments to choose what to do, then answer the prompts. Any
step can be answered up front with a flag, and --yes skips all prompts.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs import permissions as perms  # noqa: E402
from tcs.batch import run_batch, summary  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_groups, list_users, list_workbooks, placement  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.permissions import PROJECT, PROJECT_DEFAULT, WORKBOOK, Rule  # noqa: E402
from tcs.projects import ProjectTree, fetch_project_tree, resolve_project  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, find_column, read_csv, report_path, write_csv  # noqa: E402
from tcs.scope import choose_scope  # noqa: E402

READ_NOUNS = {PROJECT: "project", PROJECT_DEFAULT: "project default", WORKBOOK: "workbook"}
LOCKED = ("LockedToProject", "LockedToProjectWithoutNested")
NO_RULES = "(no explicit rules)"
PROGRESS_EVERY = 25
USER_COLUMNS = ["username", "user", "user name", "email", "email address", "name"]
COLUMNS = [
    "Content Type",
    "Name",
    "Project Path",
    "Project Permissions",
    "Grantee Type",
    "Grantee",
    "Capability",
    "Mode",
    "Content ID",
    "Grantee ID",
]
CHANGE_COLUMNS = [
    "Content Type", "Name", "Project Path", "Change", "Grantee Type", "Grantee", "Capability", "Mode",
    "Status", "Detail", "Content ID", "Grantee ID",
]

Target = Tuple[str, str, str, str]  # (content type, id, name, project path)

EXAMPLES = """\
examples:
  Report every rule (read-only):
    python 08-permissions/08_permissions.py --yes

  Give everyone in a CSV the View template on one workbook:
    python 08-permissions/08_permissions.py --grant view --users-csv team.csv \\
        --workbook "Finance/Reporting/Budget" --dry-run

  Give a group Explore on every new workbook in a project:
    python 08-permissions/08_permissions.py --grant explore --group "Finance Team" --project-defaults Finance

  Take away a user's download rights on a workbook:
    python 08-permissions/08_permissions.py --revoke --user jo@example.com --capability ExportData \\
        --workbook "Finance/Reporting/Budget"

  Put things back:
    python 08-permissions/08_permissions.py --restore output/permissions_backup_acme_2026-10-10_1432.csv
"""


# ------------------------------------------------------------------ arguments

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="08_permissions.py",
        description="Report every explicit permission rule; grant, revoke or restore them with a backup first.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--grant", choices=sorted(perms.TEMPLATES) + ["custom"], help="grant a template (custom: only --capability)")
    action.add_argument("--revoke", action="store_true", help="remove the grantees' rules from the items")
    action.add_argument("--restore", metavar="BACKUP", help="put the items in a backup back exactly as they were")

    change = parser.add_argument_group("what to change (grant and revoke)")
    change.add_argument("--workbook", metavar="PATH", action="append", help='a workbook as "Project/Sub/Workbook"; repeatable')
    change.add_argument("--workbooks-csv", metavar="FILE", help="workbooks listed by ID in a CSV (e.g. an entry 02 inventory)")
    change.add_argument("--project", metavar="PATH", action="append", help="a project's own rules; repeatable")
    change.add_argument("--project-defaults", metavar="PATH", action="append", help="a project's default rules for new workbooks; repeatable")
    change.add_argument("--group", metavar="NAME", action="append", help="a group to grant to or revoke from; repeatable")
    change.add_argument("--user", metavar="NAME", action="append", help="a user (username or email); repeatable")
    change.add_argument("--users-csv", metavar="FILE", help="users listed in a CSV (a Username or Email column)")
    change.add_argument(
        "--capability", metavar="NAME[:MODE]", action="append",
        help="grant: extra capabilities (mode Allow or Deny, default Allow); revoke: only these capabilities",
    )

    report = parser.add_argument_group("report")
    report.add_argument("--type", choices=["projects", "workbooks", "all"], help="whose rules to report")
    scope = report.add_mutually_exclusive_group()
    scope.add_argument("--include", metavar="PATH", action="append", help="report only this project and everything under it")
    scope.add_argument("--exclude", metavar="PATH", action="append", help="report every project except this one")

    parser.add_argument("--output-dir", metavar="PATH", help=f"folder for the CSV files (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts")
    parser.add_argument("--dry-run", action="store_true", help="read and review; change nothing and write no file")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    changing = args.grant or args.revoke
    target_flags = args.workbook or args.workbooks_csv or args.project or args.project_defaults
    grantee_flags = args.group or args.user or args.users_csv
    if (target_flags or grantee_flags or args.capability) and not changing:
        parser.error("--workbook, --project, --group, --user and --capability go with --grant or --revoke")
    if changing and (args.type or args.include or args.exclude):
        parser.error("--type, --include and --exclude are for the report")
    if args.grant == "custom" and not args.capability:
        parser.error("--grant custom needs at least one --capability")
    if changing and args.yes and not (target_flags and grantee_flags):
        parser.error("--yes with --grant or --revoke needs the items (--workbook/--project/...) and the grantees (--group/--user/--users-csv)")
    args.capabilities = _parse_capabilities(parser, args.capability or [])
    return args


def _parse_capabilities(parser: argparse.ArgumentParser, values: List[str]) -> Dict[str, str]:
    caps: Dict[str, str] = {}
    known = {c.casefold(): c for c in perms.CAPABILITIES}
    for value in values:
        name, _, mode = value.partition(":")
        mode = (mode or perms.ALLOW).capitalize()
        if mode not in (perms.ALLOW, perms.DENY):
            parser.error(f"--capability {value}: the mode must be Allow or Deny")
        caps[known.get(name.strip().casefold(), name.strip())] = mode
    return caps


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. If changes had started, the backup and changes files show where it stopped.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Permissions")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    action = "grant" if args.grant else "revoke" if args.revoke else "restore" if args.restore else None
    if action is None and not args.yes and not args.type:
        ui.heading("What would you like to do?")
        choice = ui.menu(
            [
                "Report every explicit rule (read-only)",
                "Grant users or groups a template",
                "Revoke users' or groups' rules",
                "Restore rules from a backup",
            ],
            bottom=[("q", "Quit")],
        )
        action = [None, "grant", "revoke", "restore"][int(choice)]
    if action is None:
        return run_report(args, settings)
    return run_change(args, settings, action)


# ------------------------------------------------------------------ report

def run_report(args: argparse.Namespace, settings: Settings) -> int:
    choice = args.type or ("all" if args.yes else None)
    if choice is None:
        ui.heading("Whose rules?")
        choice = ["projects", "workbooks", "all"][int(ui.menu(["Projects (and their defaults)", "Workbooks", "Both"], bottom=[("q", "Quit")]))]

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        tree = fetch_project_tree(client)
        workbooks = list_workbooks(client) if choice in ("workbooks", "all") else []
        names = grantee_names(client)

        scope = choose_scope(
            tree,
            include=args.include,
            exclude=args.exclude,
            ask=not args.yes,
            counts=Counter((wb.get("project") or {}).get("id") for wb in workbooks) if workbooks else None,
            noun="workbook",
        )
        targets: List[Target] = []
        if choice in ("projects", "all"):
            for project in tree.walk():
                if scope.covers(project.id):
                    path = tree.path(project.id)
                    targets.append((PROJECT, project.id, project.name, path))
                    targets.append((PROJECT_DEFAULT, project.id, project.name, path))
        for wb in workbooks:
            place = placement(tree, wb)
            if scope.covers(place.project_id, place.visible):
                targets.append((WORKBOOK, wb["id"], wb.get("name", ""), place.path))

        ui.heading(f"Reading {_n(len(targets), 'rule set')}")
        rows, failures = report_rows(client, tree, workbooks, targets, names)

    out_file = report_path(_folder(args), "permissions", settings.site_label, datetime.now())
    _print_report_review(settings, scope.label, targets, rows, failures, out_file)
    if args.dry_run:
        ui.warn("Dry run: no file was written.")
        return 0
    if not args.yes and not ui.confirm("Write the CSV?", default=True):
        raise ui.Cancelled()
    write_csv(out_file, rows, COLUMNS)
    ui.success(f"{_n(len(rows), 'rule')}: {display_path(out_file)}")
    return 1 if failures else 0


def report_rows(
    client: TableauClient,
    tree: ProjectTree,
    workbooks: List[Dict[str, Any]],
    targets: List[Target],
    names: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    project_of = {wb["id"]: (wb.get("project") or {}).get("id", "") for wb in workbooks}
    rows: List[Dict[str, Any]] = []
    failures: List[str] = []
    for number, target in enumerate(targets, start=1):
        if number % PROGRESS_EVERY == 0:
            ui.detail(f"  ... {number} of {len(targets)}")
        kind, item_id, name, path = target
        try:
            rules = perms.read_rules(client, kind, item_id)
        except TableauError as exc:
            where = f"{path} / {name}" if kind == WORKBOOK else path
            failures.append(f"{kind.lower()} {where}: {exc}")
            continue
        rows += rule_rows(target, _lock(tree, kind, item_id, project_of), rules, names)
    return rows, failures


def rule_rows(target: Target, lock: str, rules: Set[Rule], names: Dict[str, str], marker: bool = False) -> List[Dict[str, Any]]:
    """Report rows for one item. ``marker`` adds a row for an item with no rules, so a backup still lists it."""
    kind, item_id, name, path = target
    base = {"Content Type": kind, "Name": name, "Project Path": path, "Project Permissions": lock, "Content ID": item_id}
    rows = [
        dict(base, **{
            "Grantee Type": rule.grantee_type.capitalize(),
            "Grantee": names.get(rule.grantee_id, rule.grantee_id),
            "Capability": rule.capability,
            "Mode": rule.mode,
            "Grantee ID": rule.grantee_id,
        })
        for rule in sorted(rules, key=lambda r: (names.get(r.grantee_id, r.grantee_id).casefold(), r.capability))
    ]
    if not rows and marker:
        rows.append(dict(base, **{"Grantee Type": "", "Grantee": NO_RULES, "Capability": "", "Mode": "", "Grantee ID": ""}))
    return rows


def grantee_names(client: TableauClient) -> Dict[str, str]:
    """Group and user ids to names. Users may be off-limits to non-admins; ids then stand in."""
    names = {g["id"]: g.get("name", "") for g in list_groups(client)}
    try:
        names.update({u["id"]: u.get("fullName") or u.get("name", "") for u in list_users(client)})
    except TableauError:
        ui.warn("Your account can't list users, so users granted rules are shown by id.")
    return names


def _print_report_review(
    settings: Settings, scope_label: str, targets: List[Target], rows: List[Dict[str, Any]], failures: List[str], out_file: Path
) -> None:
    by_kind = Counter(t[0] for t in targets)
    grantees = {(r["Grantee Type"], r["Grantee ID"]) for r in rows}
    direct = {(r["Content ID"], r["Grantee ID"]) for r in rows if r["Grantee Type"] == "User"}
    denies = sum(1 for r in rows if r["Mode"] == perms.DENY)
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Scope", scope_label),
            ("Read", ", ".join(_n(c, READ_NOUNS[k]) for k, c in by_kind.items()) or "nothing"),
            ("Rules", f"{_n(len(rows), 'capability rule')} for {_n(len(grantees), 'grantee')}"),
            ("File", display_path(out_file)),
        ]
    )
    if direct:
        ui.warn(f"{_n(len(direct), 'rule set')} grant to individual users rather than groups. Groups are easier to keep right.")
    if denies:
        ui.warn(
            f"{_n(denies, 'Deny rule')}. A group's Deny overrides Allow from the user's other groups "
            "(only a rule on the user beats it), so check these are intended."
        )
    for failure in failures[:10]:
        ui.warn(f"Couldn't read {failure}")


# ------------------------------------------------------------------ grant, revoke, restore

class Change:
    """One item's planned change."""

    def __init__(self, target: Target, lock: str, current: Set[Rule], desired: Set[Rule]) -> None:
        self.target, self.lock, self.current = target, lock, current
        self.deletes, self.adds = perms.plan(current, desired)
        self.status, self.detail = "", ""


def run_change(args: argparse.Namespace, settings: Settings, action: str) -> int:
    # Files first: a bad CSV or backup should fail before signing in.
    backup_rows: List[Dict[str, str]] = []
    csv_users: List[str] = []
    if action == "restore":
        backup_rows = read_backup(clean_path_input(args.restore or ui.ask_required("Path to the permissions backup")))
    elif args.users_csv:
        csv_users = read_user_column(clean_path_input(args.users_csv))

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        tree = fetch_project_tree(client)
        workbooks = list_workbooks(client)
        groups = list_groups(client)
        users = list_users(client)
        names = {g["id"]: g.get("name", "") for g in groups}
        names.update({u["id"]: u.get("fullName") or u.get("name", "") for u in users})
        project_of = {wb["id"]: (wb.get("project") or {}).get("id", "") for wb in workbooks}

        missing: List[str] = []
        if action == "restore":
            targets, desired_by_item = restore_plan(backup_rows)
            capabilities: Dict[str, str] = {}
            grantees: List[Tuple[str, str]] = []
        else:
            targets = resolve_targets(args, tree, workbooks)
            grantees, missing = resolve_grantees(args, csv_users, groups, users)
            if not grantees:
                raise LookupError("None of the users or groups given are on this site.")
            capabilities = _capabilities(args) if action == "grant" else dict(args.capabilities)

        ui.heading(f"Reading the current rules of {_n(len(targets), 'item')}")
        changes: List[Change] = []
        for target in targets:
            kind, item_id = target[0], target[1]
            current = perms.read_rules(client, kind, item_id)
            if action == "grant":
                desired = perms.granted(current, grantees, capabilities)
            elif action == "revoke":
                desired = perms.revoked(current, {g[1] for g in grantees}, capabilities)
            else:
                desired = desired_by_item[(kind, item_id)]
            changes.append(Change(target, _lock(tree, kind, item_id, project_of), current, desired))

        stamp = datetime.now()
        backup_file = report_path(_folder(args), "permissions_backup", settings.site_label, stamp)
        changes_file = report_path(_folder(args), "permissions_changes", settings.site_label, stamp)
        todo = [c for c in changes if c.deletes or c.adds]
        _print_change_review(settings, action, args, changes, names, missing, backup_file)
        if not todo:
            ui.success("Nothing to change: every item already matches.")
            return 0
        if args.dry_run:
            ui.warn("Dry run: nothing was changed and no file was written.")
            return 0
        if not args.yes and not ui.confirm(f"Change the rules on {_n(len(todo), 'item')}?"):
            raise ui.Cancelled()

        # The backup goes down before the first change: it's what --restore reads.
        write_csv(backup_file, [row for c in todo for row in rule_rows(c.target, c.lock, c.current, names, marker=True)], COLUMNS)
        ui.detail(f"  Backup: {display_path(backup_file)}")
        ui.heading("Changing")
        try:
            outcomes = run_batch(
                todo,
                lambda c: _apply(client, c),
                lambda c: f"{c.target[0].lower()} {_where(c.target)}",
                explain=_explain,
            )
        finally:
            write_csv(changes_file, change_rows(todo, names), CHANGE_COLUMNS)

    code = summary(outcomes, "Changed")
    ui.info(f"  Backup: {display_path(backup_file)}")
    ui.info(f"  Changes: {display_path(changes_file)}")
    ui.info(f"  To undo:  python 08-permissions/08_permissions.py --restore \"{backup_file}\"")
    return code


def _apply(client: TableauClient, change: Change) -> str:
    try:
        perms.apply(client, change.target[0], change.target[1], change.deletes, change.adds)
    except TableauError as exc:
        change.status, change.detail = "Failed", _explain(change, exc)
        raise
    change.status = "Done"
    return f"+{len(change.adds)} -{len(change.deletes)}"


def _explain(change: Change, exc: TableauError) -> str:
    if exc.status == 403 and change.target[0] == WORKBOOK and change.lock in LOCKED:
        return "refused: the workbook's project has locked permissions; change the project's default rules instead"
    return str(exc)


def resolve_targets(args: argparse.Namespace, tree: ProjectTree, workbooks: List[Dict[str, Any]]) -> List[Target]:
    targets: List[Target] = []
    by_id = {wb["id"]: wb for wb in workbooks}

    def add_workbook(wb: Dict[str, Any]) -> None:
        targets.append((WORKBOOK, wb["id"], wb.get("name", ""), placement(tree, wb).path))

    for path in args.workbook or []:
        add_workbook(find_workbook(path, tree, workbooks))
    if args.workbooks_csv:
        columns, rows = read_csv(clean_path_input(args.workbooks_csv))
        column = find_column(columns, ["Workbook ID", "workbook_id", "id", "luid"])
        if not column:
            raise ValueError(f"{args.workbooks_csv} has no Workbook ID column.")
        ids = [r[column] for r in rows if r.get(column)]
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            ui.warn(f"{_n(len(unknown), 'workbook id')} in the CSV aren't on this site: {', '.join(unknown[:5])}")
        for workbook_id in dict.fromkeys(i for i in ids if i in by_id):
            add_workbook(by_id[workbook_id])
    for kind, paths in ((PROJECT, args.project), (PROJECT_DEFAULT, args.project_defaults)):
        for path in paths or []:
            project = tree.find_by_path(path)
            targets.append((kind, project.id, project.name, tree.path(project.id)))
    if not targets:
        targets = pick_targets(tree, workbooks)
    return list(dict.fromkeys(targets))


def find_workbook(path: str, tree: ProjectTree, workbooks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """'Finance/Reporting/Budget' -> the Budget workbook in Finance / Reporting. A bare name must be unique."""
    project_path, _, name = path.strip().rpartition("/")
    candidates = [wb for wb in workbooks if wb.get("name", "").casefold() == name.strip().casefold()]
    if project_path:
        project = tree.find_by_path(project_path)
        candidates = [wb for wb in candidates if (wb.get("project") or {}).get("id") == project.id]
        if not candidates:
            raise LookupError(f'No workbook named "{name}" in {tree.path(project.id)}.')
    elif len(candidates) != 1:
        where = ", ".join(placement(tree, wb).path for wb in candidates)
        raise LookupError(
            f'No workbook named "{name}".' if not candidates
            else f'"{name}" is in several projects ({where}). Give it as "Project/{name}".'
        )
    return candidates[0]


def pick_targets(tree: ProjectTree, workbooks: List[Dict[str, Any]]) -> List[Target]:
    ui.heading("Which items?")
    kind = int(ui.menu(["Workbooks in a project", "A project's own rules", "A project's default rules for new workbooks"], bottom=[("q", "Quit")]))
    project = resolve_project(tree, here="Choose this project", title="Which project?")
    path = tree.path(project.id)
    if kind == 1:
        return [(PROJECT, project.id, project.name, path)]
    if kind == 2:
        return [(PROJECT_DEFAULT, project.id, project.name, path)]
    here = sorted((wb for wb in workbooks if (wb.get("project") or {}).get("id") == project.id), key=lambda w: w.get("name", "").casefold())
    if not here:
        raise LookupError(f"{path} has no workbooks you can see.")
    ui.heading(f"Workbooks in {path}")
    picked = ui.pick_many([wb.get("name", "") for wb in here], prompt="Which")
    return [(WORKBOOK, here[i]["id"], here[i].get("name", ""), path) for i in picked]


def resolve_grantees(
    args: argparse.Namespace, csv_users: List[str], groups: List[Dict[str, Any]], users: List[Dict[str, Any]]
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """(grantees as (type, id), names given that aren't on the site)."""
    group_names = list(args.group or [])
    user_names = list(args.user or []) + csv_users
    if not group_names and not user_names:
        group_names, user_names = pick_grantees(groups, users)
    by_group = {g.get("name", "").casefold(): g["id"] for g in groups}
    by_user: Dict[str, str] = {}
    for user in users:
        if user.get("email"):
            by_user.setdefault(user["email"].casefold(), user["id"])
    for user in users:
        by_user[user.get("name", "").casefold()] = user["id"]
    found: List[Tuple[str, str]] = []
    missing: List[str] = []
    for kind, wanted, lookup in (("group", group_names, by_group), ("user", user_names, by_user)):
        for name in wanted:
            hit = lookup.get(name.strip().casefold())
            if hit:
                found.append((kind, hit))
            else:
                missing.append(f"group {name}" if kind == "group" else name)
    return list(dict.fromkeys(found)), missing


def pick_grantees(groups: List[Dict[str, Any]], users: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    ui.heading("Who?")
    choice = int(ui.menu(["Groups, from a list", "Users, from a CSV file", "Users, by username or email"], bottom=[("q", "Quit")]))
    if choice == 0:
        ordered = sorted(groups, key=lambda g: g.get("name", "").casefold())
        picked = ui.pick_many([g.get("name", "") for g in ordered], prompt="Which groups")
        return [ordered[i]["name"] for i in picked], []
    if choice == 1:
        return [], read_user_column(clean_path_input(ui.ask_required("Path to the CSV file")))
    typed = ui.ask_required("Usernames or emails, separated by commas")
    return [], [t.strip() for t in typed.split(",") if t.strip()]


def read_user_column(path: Path) -> List[str]:
    columns, rows = read_csv(path)
    column = find_column(columns, USER_COLUMNS)
    if not column:
        raise ValueError(f"No user column found in {display_path(path)}. Columns: {', '.join(columns)}")
    users = list(dict.fromkeys(r[column] for r in rows if r.get(column)))
    ui.success(f"{_n(len(users), 'user')} in {display_path(path)}")
    return users


def _capabilities(args: argparse.Namespace) -> Dict[str, str]:
    template = args.grant
    if template is None:  # interactive grant
        ui.heading("Which template?")
        options = list(perms.TEMPLATES)
        template = options[int(ui.menu([f"{t.capitalize()}: {', '.join(perms.TEMPLATES[t])}" for t in options], bottom=[("q", "Quit")]))]
    caps = {name: perms.ALLOW for name in perms.TEMPLATES.get(template, [])}
    caps.update(args.capabilities)
    return caps


def read_backup(path: Path) -> List[Dict[str, str]]:
    columns, rows = read_csv(path)
    needed = ["Content Type", "Content ID", "Grantee Type", "Grantee ID", "Capability", "Mode"]
    absent = [c for c in needed if c not in columns]
    if absent:
        raise ValueError(f"{display_path(path)} isn't a permissions backup or report from this script (no {', '.join(absent)}).")
    ui.success(f"Backup: {display_path(path)}")
    return rows


def restore_plan(rows: List[Dict[str, str]]) -> Tuple[List[Target], Dict[Tuple[str, str], Set[Rule]]]:
    targets: Dict[Tuple[str, str], Target] = {}
    desired: Dict[Tuple[str, str], Set[Rule]] = defaultdict(set)
    for row in rows:
        kind, item_id = row["Content Type"], row["Content ID"]
        if kind not in READ_NOUNS:
            raise ValueError(f"Unknown content type in the backup: {kind!r}")
        targets.setdefault((kind, item_id), (kind, item_id, row.get("Name", ""), row.get("Project Path", "")))
        desired[(kind, item_id)]  # an item with no rules restores to no rules
        if row.get("Grantee ID"):
            desired[(kind, item_id)].add(Rule(row["Grantee Type"].lower(), row["Grantee ID"], row["Capability"], row["Mode"]))
    return list(targets.values()), desired


def change_rows(changes: List[Change], names: Dict[str, str]) -> List[Dict[str, Any]]:
    rows = []
    for change in changes:
        kind, item_id, name, path = change.target
        for label, rules in (("Remove", change.deletes), ("Add", change.adds)):
            for rule in rules:
                rows.append({
                    "Content Type": kind, "Name": name, "Project Path": path, "Change": label,
                    "Grantee Type": rule.grantee_type.capitalize(), "Grantee": names.get(rule.grantee_id, rule.grantee_id),
                    "Capability": rule.capability, "Mode": rule.mode, "Status": change.status or "Not reached",
                    "Detail": change.detail, "Content ID": item_id, "Grantee ID": rule.grantee_id,
                })
    return rows


def _print_change_review(
    settings: Settings, action: str, args: argparse.Namespace, changes: List[Change], names: Dict[str, str],
    missing: List[str], backup_file: Path,
) -> None:
    todo = [c for c in changes if c.deletes or c.adds]
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Action", {"grant": f"Grant {args.grant or 'template'}", "revoke": "Revoke", "restore": f"Restore {args.restore or ''}"}[action].strip()),
            ("Items", f"{len(changes)} read, {len(todo)} to change"),
            ("Rules", f"{sum(len(c.adds) for c in todo)} to add, {sum(len(c.deletes) for c in todo)} to remove"),
            ("Backup", display_path(backup_file)),
        ]
    )
    for change in todo:
        print()
        print(f"  {change.target[0]}: {_where(change.target)}")
        for label, rules, color in (("-", change.deletes, "red"), ("+", change.adds, "green")):
            by_grantee: Dict[str, List[str]] = defaultdict(list)
            for rule in rules:
                who = f"{names.get(rule.grantee_id, rule.grantee_id)} ({rule.grantee_type})"
                by_grantee[who].append(rule.capability + ("" if rule.mode == perms.ALLOW else " Deny"))
            for who, caps in sorted(by_grantee.items()):
                print(f"    {ui.style(label, color)} {who}: {', '.join(caps)}")
        if change.target[0] == WORKBOOK and change.lock in LOCKED:
            ui.warn("This workbook's project has locked permissions: Tableau will refuse workbook-level changes.")
    if missing:
        ui.warn(f"Not on this site: {', '.join(missing[:8])}" + (f" and {len(missing) - 8} more" if len(missing) > 8 else ""))
    if action == "restore":
        ui.warn("Restore makes these items match the backup exactly, undoing any change made since, by anyone.")


# ------------------------------------------------------------------ helpers

def _lock(tree: ProjectTree, kind: str, item_id: str, project_of: Dict[str, str]) -> str:
    project_id = item_id if kind != WORKBOOK else project_of.get(item_id, "")
    return tree.by_id[project_id].content_permissions if project_id in tree.by_id else ""


def _where(target: Target) -> str:
    kind, _, name, path = target
    return f"{path} / {name}" if kind == WORKBOOK else path


def _folder(args: argparse.Namespace) -> Path:
    return clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
