#!/usr/bin/env python3
"""Find extract refreshes that failed, then rerun them at a pace Tableau's rate limit allows.

Entry 11 of the starter pack. See README.md here.

Three steps, with a tracking CSV carrying the state between them:

  scan    Failed refresh jobs in the last --hours (14 by default: overnight),
          plus scheduled refresh tasks that have failed in a row. Each failed
          job is looked up to find exactly which data source or workbook it
          was, and the error it gave. One row per data source or workbook.
  retry   Start a refresh for each Pending row, drip-fed: calls are spaced
          to fit your --calls-per-hour, and a 429 from Tableau slows the pace
          down and waits as long as Tableau asks. The CSV is saved after
          every row, so an interrupted run picks up where it stopped.
  check   Later: look up each refresh this tool started and record whether
          it succeeded, failed again, or is still running.

Run it with no arguments to scan, review, and then decide whether to retry
straight away. Edit the CSV between steps if you like: set a row's Status to
Skip and retry will leave it alone.

Needs a site administrator account: only admins can list background jobs.

Safeguards, each there because of a way a mass retry goes wrong:
  * Pacing that fits your edition's hourly cap (120 on Standard; see the
    README), remembered across runs in the same hour, and backing off on 429.
  * Each failure is matched to its data source or workbook by the job's own
    record, never by name: names aren't unique, and a name match can refresh
    the wrong thing.
  * Flow runs are listed but not retried: a flow isn't an extract refresh.
  * "Already queued or running" (409) is recorded as such, not as a failure.
  * An expired session signs in again and carries on.
  * A started refresh is recorded as Queued, not Succeeded. Only check can say
    whether it worked.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs import pacing  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_datasources, list_workbooks, placement  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.projects import fetch_project_tree  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, read_csv, report_path, write_csv  # noqa: E402

DEFAULT_HOURS = 14
DEFAULT_PER_HOUR = 120  # Standard edition's cap: safe on every edition
SESSION_REFRESH = 50 * 60  # sign in again before a long run's session goes stale
MAX_ATTEMPTS = 4

DATASOURCE, WORKBOOK = "Data source", "Workbook"
KINDS = {DATASOURCE: "datasources", WORKBOOK: "workbooks"}
PENDING, SKIP, LIMITED = "Pending", "Skip", "Rate limited"
QUEUED, RUNNING_ALREADY, NOT_STARTED = "Queued", "Already queued or running", "Failed to start"
SUCCEEDED, FAILED_AGAIN, CANCELLED, STILL_RUNNING = "Succeeded", "Failed again", "Cancelled", "Still running"
RETRYABLE = (PENDING, LIMITED)

COLUMNS = [
    "Type",
    "Name",
    "Project Path",
    "Failures",
    "Last Failed (UTC)",
    "Source",
    "Last Error",
    "Status",
    "Detail",
    "New Job ID",
    "Started (UTC)",
    "Content ID",
    "Task ID",
    "Failed Job ID",
]

EXAMPLES = """\
examples:
  Interactive (scan, review, then retry if you say so):
    python 11-retry-failed-refreshes/11_retry_failed_refreshes.py

  The overnight run as a scheduled job, on an Enterprise site:
    python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --hours 14 --calls-per-hour 240 --yes

  Scan only, edit the CSV, then retry it:
    python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --scan-only
    python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --retry output/refresh_retries_acme_2026-10-10_0700.csv

  An hour later, see which retries worked:
    python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --check output/refresh_retries_acme_2026-10-10_0700.csv
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="11_retry_failed_refreshes.py",
        description="Find failed extract refreshes and rerun them, paced to Tableau's rate limit.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan-only", action="store_true", help="scan and write the tracking CSV; don't retry")
    mode.add_argument("--retry", metavar="CSV", help="retry the Pending rows of an earlier scan's CSV")
    mode.add_argument("--check", metavar="CSV", help="record how the refreshes this tool started turned out")
    parser.add_argument("--hours", type=float, default=None, metavar="N", help=f"scan this far back (default {DEFAULT_HOURS})")
    parser.add_argument(
        "--calls-per-hour",
        type=int,
        default=DEFAULT_PER_HOUR,
        metavar="N",
        help=f"refreshes to start per hour at most (default {DEFAULT_PER_HOUR}, Tableau Cloud Standard's cap)",
    )
    parser.add_argument("--output-dir", metavar="PATH", help=f"folder for the tracking CSV (default: ./{DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-y", "--yes", action="store_true", help="no prompts: scan and retry (or just --retry / --check)")
    parser.add_argument("--dry-run", action="store_true", help="scan and review, but write nothing and start nothing")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be more than 0")
    if args.calls_per_hour < 1:
        parser.error("--calls-per-hour must be 1 or more")
    if args.hours is not None and (args.retry or args.check):
        parser.error("--hours is for scanning; --retry and --check use the CSV's rows")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Stopped. The tracking CSV has every row's status so far; --retry it to carry on.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Retry failed extract refreshes")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    tracking: Optional[Path] = None
    rows: List[Dict[str, Any]] = []
    if args.retry or args.check:
        tracking = clean_path_input(args.retry or args.check)
        rows = read_tracking(tracking)

    with TableauClient(settings) as client:
        ui.success(f"Signed in to {settings.site_label} (REST API {client.api_version})")
        if args.check:
            assert tracking is not None
            return check(client, tracking, rows)
        if not args.retry:
            hours = args.hours if args.hours is not None else (DEFAULT_HOURS if args.yes else _ask_hours())
            rows, flows = scan(client, hours)
            folder = clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
            tracking = report_path(folder, "refresh_retries", settings.site_label, datetime.now())
            _print_scan_review(settings, hours, rows, flows, tracking)
            if not rows:
                ui.success("Nothing to retry.")
                return 0
            if args.dry_run:
                ui.warn("Dry run: no CSV was written and nothing was retried.")
                return 0
            write_csv(tracking, rows, COLUMNS)
            ui.success(f"Tracking CSV: {display_path(tracking)}")
            if args.scan_only:
                ui.info(f"  Retry later with: --retry \"{tracking}\"")
                return 0
            if not args.yes and not ui.confirm(f"Start refreshes for {_n(len(rows), 'item')} now, paced at up to {args.calls_per_hour}/hour?"):
                ui.info(f"  Not retried. Later: --retry \"{tracking}\"")
                return 0
        else:
            todo = [r for r in rows if r["Status"] in RETRYABLE]
            ui.success(f"{_n(len(todo), 'row')} to retry in {display_path(tracking)}")
            if args.dry_run or not todo:
                ui.warn("Dry run: nothing was retried." if args.dry_run else "Nothing to retry.")
                return 0
            if not args.yes and not ui.confirm(f"Start {_n(len(todo), 'refresh', 'refreshes')} now, paced at up to {args.calls_per_hour}/hour?"):
                raise ui.Cancelled()

        assert tracking is not None
        state = tracking.parent / f".refresh_pace_{settings.site_label}.json"
        budget = pacing.RateBudget(args.calls_per_hour, state)
        return retry(client, tracking, rows, budget)


# ------------------------------------------------------------------ scan

def scan(client: TableauClient, hours: float) -> Tuple[List[Dict[str, Any]], int]:
    """Failed refresh jobs since the cutoff, plus tasks failing in a row. Returns (rows, flows seen)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        jobs = list(
            client.paginate(
                "/jobs", "backgroundJobs", "backgroundJob", action="List failed jobs",
                params={"filter": f"status:eq:Failed,createdAt:gt:{stamp}"},
            )
        )
    except TableauError as exc:
        if exc.status == 403:
            raise TableauError("Listing background jobs needs a site administrator account.") from None
        raise
    refresh_jobs = [j for j in jobs if "extract" in (j.get("jobType") or "").lower()]
    flows = sum(1 for j in jobs if "flow" in (j.get("jobType") or "").lower())
    ui.success(f"{_n(len(jobs), 'failed job')} since {cutoff:%Y-%m-%d %H:%M} UTC; {len(refresh_jobs)} were extract refreshes")

    tree = fetch_project_tree(client)
    catalog: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for ds in list_datasources(client):
        catalog[ds["id"]] = (DATASOURCE, ds)
    for wb in list_workbooks(client):
        catalog[wb["id"]] = (WORKBOOK, wb)
    tasks = refresh_tasks(client)

    targets: Dict[str, Dict[str, Any]] = {}
    unresolved: List[Dict[str, Any]] = []
    for number, job in enumerate(sorted(refresh_jobs, key=lambda j: j.get("createdAt", ""), reverse=True), start=1):
        if number % 25 == 0:
            ui.detail(f"  ... {number} of {len(refresh_jobs)}")
        kind, content_id, name, error = job_target(client, job["id"])
        if not content_id:
            unresolved.append(job)
            continue
        row = targets.get(content_id)
        if row is None:  # newest failure first, so the first one seen is the latest
            row = targets[content_id] = _row(kind, content_id, name, tree, catalog, tasks)
            row.update({"Last Failed (UTC)": _ts(job.get("createdAt")), "Source": "Job history",
                        "Last Error": error, "Failed Job ID": job["id"]})
        row["Failures"] += 1

    for content_id, (kind, task_id, in_a_row) in tasks.items():
        if in_a_row and content_id not in targets:
            row = targets[content_id] = _row(kind, content_id, "", tree, catalog, tasks)
            row["Source"] = f"Scheduled task ({in_a_row} failures in a row)"
            row["Failures"] = in_a_row
    for job in unresolved:
        ui.warn(f"Couldn't tell which item failed job {job['id']} ({job.get('title', '')}) was for; not included.")
    rows = sorted(targets.values(), key=lambda r: (r["Project Path"].casefold(), r["Name"].casefold()))
    return rows, flows


def job_target(client: TableauClient, job_id: str) -> Tuple[str, str, str, str]:
    """GET /jobs/{id}: (type, content id, name, error text). The job's own record, not a name match."""
    try:
        job = client.get_json(f"/jobs/{job_id}", action="Look up job").get("job") or {}
    except TableauError:
        return "", "", "", ""
    detail = job.get("extractRefreshJob") or {}
    notes = job.get("statusNotes") or {}
    note_list = notes.get("statusNote") if isinstance(notes, dict) else notes
    error = " ".join(n.get("text", "") for n in (note_list or []) if isinstance(n, dict)).strip() or detail.get("notes", "")
    for kind, key in ((DATASOURCE, "datasource"), (WORKBOOK, "workbook")):
        item = detail.get(key)
        if item and item.get("id"):
            return kind, item["id"], item.get("name", ""), error[:300]
    return "", "", "", error[:300]


def refresh_tasks(client: TableauClient) -> Dict[str, Tuple[str, str, int]]:
    """GET /tasks/extractRefreshes: content id -> (type, task id, failures in a row)."""
    try:
        data = client.get_json("/tasks/extractRefreshes", action="List refresh tasks")
    except TableauError as exc:
        ui.warn(f"Couldn't list scheduled refresh tasks ({exc}); retries will use direct refreshes only.")
        return {}
    found: Dict[str, Tuple[str, str, int]] = {}
    for task in (data.get("tasks") or {}).get("task") or []:
        refresh = task.get("extractRefresh") or {}
        for kind, key in ((DATASOURCE, "datasource"), (WORKBOOK, "workbook")):
            target = refresh.get(key) or {}
            if target.get("id"):
                in_a_row = int(refresh.get("consecutiveFailedCount") or 0)
                previous = found.get(target["id"])
                if previous is None or in_a_row > previous[2]:
                    found[target["id"]] = (kind, refresh.get("id", ""), in_a_row)
    return found


def _row(kind: str, content_id: str, name: str, tree: Any, catalog: Dict[str, Tuple[str, Dict[str, Any]]],
         tasks: Dict[str, Tuple[str, str, int]]) -> Dict[str, Any]:
    known_kind, item = catalog.get(content_id, (kind, {}))
    return {
        "Type": known_kind or kind,
        "Name": item.get("name") or name,
        "Project Path": placement(tree, item).path if item else "",
        "Failures": 0,
        "Last Failed (UTC)": "",
        "Source": "",
        "Last Error": "",
        "Status": PENDING,
        "Detail": "",
        "New Job ID": "",
        "Started (UTC)": "",
        "Content ID": content_id,
        "Task ID": tasks.get(content_id, ("", "", 0))[1],
        "Failed Job ID": "",
    }


# ------------------------------------------------------------------ retry

def retry(client: TableauClient, tracking: Path, rows: List[Dict[str, Any]], budget: pacing.RateBudget) -> int:
    todo = [r for r in rows if r["Status"] in RETRYABLE]
    ui.heading(f"Starting {_n(len(todo), 'refresh', 'refreshes')}")
    signed_in_at = pacing.now()
    try:
        for number, row in enumerate(todo, start=1):
            if pacing.now() - signed_in_at > SESSION_REFRESH:
                client.sign_out()
                client.sign_in()
                signed_in_at = pacing.now()
            start_refresh(client, row, budget)
            label = f"[{number}/{len(todo)}] {row['Type'].lower()} {row['Name']}"
            if row["Status"] == QUEUED:
                ui.success(f"{label}  {ui.style('job ' + row['New Job ID'], 'dim')}")
            elif row["Status"] == RUNNING_ALREADY:
                ui.info(f"  {label}: already queued or running")
            else:
                ui.error(f"{label}: {row['Detail']}")
            write_csv(tracking, rows, COLUMNS)  # after every row, so a stop loses nothing
    finally:
        write_csv(tracking, rows, COLUMNS)

    counts = Counter(r["Status"] for r in todo)
    ui.heading("Summary")
    ui.table([(status, str(count)) for status, count in counts.most_common()] + [("Tracking CSV", display_path(tracking))])
    ui.info(f"  See how they turned out later with: --check \"{tracking}\"")
    return 1 if counts[NOT_STARTED] or counts[LIMITED] else 0


def start_refresh(client: TableauClient, row: Dict[str, Any], budget: pacing.RateBudget) -> None:
    """POST /{datasources|workbooks}/{id}/refresh; if that's refused and there's a task, run the task.

    Every attempt waits for the budget, including the task fallback.
    """
    routes = []
    if row["Content ID"] and row["Type"] in KINDS:
        routes.append(f"/{KINDS[row['Type']]}/{row['Content ID']}/refresh")
    if row["Task ID"]:
        routes.append(f"/tasks/extractRefreshes/{row['Task ID']}/runNow")
    if not routes:
        row["Status"], row["Detail"] = NOT_STARTED, "no data source, workbook or task id"
        return

    last_error = ""
    for route in routes:
        attempts, relogged = 0, False
        while attempts < MAX_ATTEMPTS:
            attempts += 1
            budget.wait_for_slot()
            try:
                data = client.request("POST", route, action="Start refresh", json={}).json()
            except TableauError as exc:
                if exc.status == 429:
                    budget.penalise(pacing.retry_after_seconds(str(exc)))
                    continue
                if exc.status == 409:
                    row["Status"], row["Detail"] = RUNNING_ALREADY, ""
                    return
                if exc.status == 401 and not relogged:
                    client.sign_out()
                    client.sign_in()
                    relogged = True
                    attempts -= 1
                    continue
                last_error = str(exc)
                break  # try the next route, if any
            budget.reward()
            row["Status"], row["Detail"] = QUEUED, ""
            row["New Job ID"] = ((data or {}).get("job") or {}).get("id", "")
            row["Started (UTC)"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return
        else:
            row["Status"], row["Detail"] = LIMITED, f"still rate limited after {MAX_ATTEMPTS} tries; --retry later"
            return
    row["Status"], row["Detail"] = NOT_STARTED, last_error[:300]


# ------------------------------------------------------------------ check

def check(client: TableauClient, tracking: Path, rows: List[Dict[str, Any]]) -> int:
    started = [r for r in rows if r.get("New Job ID") and r["Status"] in (QUEUED, STILL_RUNNING)]
    ui.heading(f"Checking {_n(len(started), 'refresh', 'refreshes')} this tool started")
    for row in started:
        try:
            job = client.get_json(f"/jobs/{row['New Job ID']}", action="Look up job").get("job") or {}
        except TableauError as exc:
            row["Detail"] = f"couldn't look up the job: {exc}"
            continue
        code = str(job.get("finishCode", ""))
        if not job.get("completedAt") and code not in ("0", "1", "2"):
            row["Status"] = STILL_RUNNING
        else:
            row["Status"] = {"0": SUCCEEDED, "1": FAILED_AGAIN, "2": CANCELLED}.get(code, FAILED_AGAIN)
            if row["Status"] == FAILED_AGAIN:
                _, _, _, error = job_target(client, row["New Job ID"])
                row["Detail"] = error or row["Detail"]
    write_csv(tracking, rows, COLUMNS)
    counts = Counter(r["Status"] for r in started)
    ui.heading("Results")
    ui.table([(status, str(count)) for status, count in counts.most_common()] + [("Tracking CSV", display_path(tracking))])
    for row in [r for r in started if r["Status"] == FAILED_AGAIN][:10]:
        ui.warn(f"{row['Name']}: {row['Detail'] or 'failed again'}")
    return 1 if counts[FAILED_AGAIN] else 0


# ------------------------------------------------------------------ files and review

def read_tracking(path: Path) -> List[Dict[str, Any]]:
    columns, rows = read_csv(path)
    missing = [c for c in ("Status", "Content ID", "Type") if c not in columns]
    if missing:
        raise ValueError(f"{display_path(path)} isn't a tracking CSV from this script (no {', '.join(missing)} column).")
    for row in rows:
        for column in COLUMNS:
            row.setdefault(column, "")
        row["Failures"] = int(row["Failures"] or 0) if str(row["Failures"]).isdigit() else row["Failures"]
    return rows


def _print_scan_review(settings: Settings, hours: float, rows: List[Dict[str, Any]], flows: int, tracking: Path) -> None:
    ui.heading("Review")
    kinds = Counter(r["Type"] for r in rows)
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Window", f"last {hours:g} hours"),
            ("To retry", ", ".join(_n(c, k.lower()) for k, c in kinds.items()) or "nothing"),
            ("Tracking CSV", display_path(tracking)),
        ]
    )
    if flows:
        ui.detail(f"  {_n(flows, 'failed flow run')} not included: flows aren't extract refreshes.")
    if rows:
        print()
        for row in rows[:15]:
            error = f"  {ui.style(row['Last Error'][:90], 'dim')}" if row["Last Error"] else ""
            print(f"  {row['Name']}  {ui.style(row['Project Path'] + ' · ' + row['Source'], 'cyan')}{error}")
        if len(rows) > 15:
            ui.detail(f"  ...and {len(rows) - 15} more in the CSV")


def _ask_hours() -> float:
    while True:
        raw = ui.ask("Look back how many hours", default=str(DEFAULT_HOURS))
        try:
            hours = float(raw)
            if hours > 0:
                return hours
        except ValueError:
            pass
        ui.warn("Enter a number of hours, e.g. 14.")


def _ts(value: Optional[str]) -> str:
    return (value or "").replace("T", " ").rstrip("Z")


def _n(count: int, noun: str, plural: Optional[str] = None) -> str:
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


if __name__ == "__main__":
    sys.exit(main())
