"""End-to-end tests for 11-retry-failed-refreshes/11_retry_failed_refreshes.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.fixture
def failures(fake):
    """Ledger failed twice, Budget (a workbook) once, a flow once, and CRM's task has failed 3 times in a row."""
    ledger = fake.add_datasource("Ledger", "p-fin")
    crm = fake.add_datasource("CRM", "p-mkt")
    budget = fake.add_workbook("Budget", "p-fin-rep")
    fake.add_failed_job(ledger, hours_ago=6, error="Old error")
    fake.add_failed_job(ledger, hours_ago=2, error="Login failed for user svc_etl")
    fake.add_failed_job(budget, kind="workbook", hours_ago=3)
    fake.add_failed_job({"id": "flow-1", "name": "Nightly flow"}, job_type="run_flow")
    fake.add_failed_job(crm, hours_ago=30)  # outside a 14-hour window
    crm_task = fake.add_task(crm, failures_in_a_row=3)
    ledger_task = fake.add_task(ledger, failures_in_a_row=2)
    return {"ledger": ledger, "crm": crm, "budget": budget, "crm_task": crm_task, "ledger_task": ledger_task}


def tracking(folder: Path):
    [path] = folder.glob("refresh_retries_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return path, {r["Name"]: r for r in csv.DictReader(handle)}


def run(script, env_file, tmp_path, *extra):
    return script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), *extra])


def test_scan_and_retry(retry_script, fake, failures, env_file, tmp_path, clock, capsys):
    assert run(retry_script, env_file, tmp_path, "--yes") == 0
    _, rows = tracking(tmp_path)
    assert sorted(rows) == ["Budget", "CRM", "Ledger"]
    ledger = rows["Ledger"]
    assert (ledger["Type"], ledger["Project Path"], ledger["Failures"], ledger["Source"]) == ("Data source", "Finance", "2", "Job history")
    assert ledger["Last Error"] == "Login failed for user svc_etl"  # the latest failure's error
    assert ledger["Task ID"] == failures["ledger_task"]["id"]
    assert rows["CRM"]["Source"] == "Scheduled task (3 failures in a row)"
    assert rows["Budget"]["Type"] == "Workbook"
    assert all(r["Status"] == "Queued" and r["New Job ID"] for r in rows.values())
    assert sorted(fake.refreshes) == sorted([
        f"/datasources/{failures['ledger']['id']}/refresh",
        f"/datasources/{failures['crm']['id']}/refresh",
        f"/workbooks/{failures['budget']['id']}/refresh",
    ])
    assert "1 failed flow run not included" in capsys.readouterr().out
    assert fake.signed_out


def test_matches_by_the_jobs_record_not_by_name(retry_script, fake, env_file, tmp_path, clock):
    fake.add_datasource("Sales", "p-fin")
    marketing_sales = fake.add_datasource("Sales", "p-mkt")  # same name, different project
    fake.add_failed_job(marketing_sales)
    run(retry_script, env_file, tmp_path, "--yes")
    assert fake.refreshes == [f"/datasources/{marketing_sales['id']}/refresh"]


def test_calls_are_spaced_to_the_hourly_budget(retry_script, fake, failures, env_file, tmp_path, clock):
    run(retry_script, env_file, tmp_path, "--yes", "--calls-per-hour", "60")
    assert len(fake.refreshes) == 3
    assert sum(clock.sleeps) >= 2 * 60 - 1  # three calls, a minute apart


def test_429_backs_off_then_carries_on(retry_script, fake, failures, env_file, tmp_path, clock, capsys):
    fake.refresh_script[failures["ledger"]["id"]] = [429, 202]
    assert run(retry_script, env_file, tmp_path, "--yes") == 0
    _, rows = tracking(tmp_path)
    assert rows["Ledger"]["Status"] == "Queued"
    assert any(s >= 8 for s in clock.sleeps)  # "retry after 3 second(s)" + 5
    assert "pace 120/hour -> 90/hour" in capsys.readouterr().out


def test_still_rate_limited_is_left_for_later_and_retry_picks_it_up(retry_script, fake, failures, env_file, tmp_path, clock):
    fake.refresh_script[failures["ledger"]["id"]] = [429, 429, 429, 429]
    assert run(retry_script, env_file, tmp_path, "--yes") == 1
    path, rows = tracking(tmp_path)
    assert rows["Ledger"]["Status"] == "Rate limited"
    assert run(retry_script, env_file, tmp_path, "--retry", str(path), "--yes") == 0
    _, rows = tracking(tmp_path)
    assert rows["Ledger"]["Status"] == "Queued"
    assert len(fake.refreshes) == 3  # Ledger once, after the earlier two


def test_already_running_and_task_fallback(retry_script, fake, failures, env_file, tmp_path, clock):
    fake.refresh_script[failures["ledger"]["id"]] = [409]
    fake.refresh_script[failures["crm"]["id"]] = [400]  # direct refresh refused; CRM has a task
    assert run(retry_script, env_file, tmp_path, "--yes") == 0
    _, rows = tracking(tmp_path)
    assert rows["Ledger"]["Status"] == "Already queued or running"
    assert rows["CRM"]["Status"] == "Queued"
    assert f"/tasks/extractRefreshes/{failures['crm_task']['id']}/runNow" in fake.refreshes


def test_expired_session_signs_in_again(retry_script, fake, failures, env_file, tmp_path, clock):
    fake.refresh_script[failures["budget"]["id"]] = [401, 202]
    assert run(retry_script, env_file, tmp_path, "--yes") == 0
    _, rows = tracking(tmp_path)
    assert {r["Status"] for r in rows.values()} == {"Queued"}
    assert sum(1 for _, p in fake.requests if p.endswith("/auth/signin")) == 2


def test_skip_rows_are_left_alone(retry_script, fake, failures, env_file, tmp_path, clock):
    assert run(retry_script, env_file, tmp_path, "--scan-only", "--hours", "14") == 0
    path, rows = tracking(tmp_path)
    assert {r["Status"] for r in rows.values()} == {"Pending"} and not fake.refreshes
    text = path.read_text(encoding="utf-8-sig").replace(",Pending,", ",Skip,", 1)
    path.write_text(text, encoding="utf-8")
    run(retry_script, env_file, tmp_path, "--retry", str(path), "--yes")
    _, rows = tracking(tmp_path)
    assert sorted(r["Status"] for r in rows.values()) == ["Queued", "Queued", "Skip"]


def test_check_records_how_they_turned_out(retry_script, fake, failures, env_file, tmp_path, clock, capsys):
    run(retry_script, env_file, tmp_path, "--yes")
    path, rows = tracking(tmp_path)
    fake.job_details[rows["Ledger"]["New Job ID"]].update(finishCode="0", completedAt="2026-10-10T08:00:00Z")
    fake.job_details[rows["Budget"]["New Job ID"]].update(
        finishCode="1", completedAt="2026-10-10T08:00:00Z", statusNotes={"statusNote": [{"text": "Bad credentials"}]})
    assert run(retry_script, env_file, tmp_path, "--check", str(path)) == 1
    _, rows = tracking(tmp_path)
    assert (rows["Ledger"]["Status"], rows["Budget"]["Status"], rows["CRM"]["Status"]) == (
        "Succeeded", "Failed again", "Still running")
    assert rows["Budget"]["Detail"] == "Bad credentials"


def test_dry_run_and_nothing_to_do(retry_script, fake, env_file, tmp_path, clock, capsys):
    assert run(retry_script, env_file, tmp_path, "--yes") == 0
    assert "Nothing to retry" in capsys.readouterr().out
    item = fake.add_datasource("Ledger", "p-fin")
    fake.add_failed_job(item)
    assert run(retry_script, env_file, tmp_path, "--dry-run", "--yes") == 0
    assert not list(tmp_path.glob("refresh_retries_*")) and not fake.refreshes


def test_interactive_asks_hours_then_confirms(retry_script, fake, failures, env_file, tmp_path, clock, answers):
    answers(["40", "n"])  # 40 hours brings in CRM's old failure too; then decline the retry
    assert run(retry_script, env_file, tmp_path) == 0
    _, rows = tracking(tmp_path)
    assert rows["CRM"]["Source"] == "Job history" and not fake.refreshes


def test_needs_a_site_admin(retry_script, fake, env_file, tmp_path, clock, capsys):
    fake.fail[("GET", "/jobs")] = 403
    assert run(retry_script, env_file, tmp_path, "--yes") == 1
    assert "needs a site administrator" in capsys.readouterr().err


def test_pace_is_remembered_across_runs(retry_script, fake, failures, env_file, tmp_path, clock):
    run(retry_script, env_file, tmp_path, "--yes", "--calls-per-hour", "3")
    state = tmp_path / ".refresh_pace_acme.json"
    assert state.exists()
    clock.sleeps.clear()
    path, _ = tracking(tmp_path)
    text = path.read_text(encoding="utf-8-sig").replace(",Queued,", ",Pending,", 1)
    path.write_text(text, encoding="utf-8")
    run(retry_script, env_file, tmp_path, "--retry", str(path), "--yes", "--calls-per-hour", "3")
    assert sum(clock.sleeps) > 1000  # the hour's 3 calls were already spent: wait for the window to roll
