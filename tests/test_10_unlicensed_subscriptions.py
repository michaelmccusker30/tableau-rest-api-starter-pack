"""End-to-end tests for 10-unlicensed-subscriptions/10_unlicensed_subscriptions.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.fixture
def subscribed(fake):
    """Sam (Unlicensed) has two subscriptions; Jo (Creator) has one."""
    budget = fake.add_workbook("Budget", "p-fin")
    overview = fake.add_view(budget, "Overview", 3)
    return {
        "sam_wb": fake.add_subscription("user-3", budget, "Workbook", "Budget daily"),
        "sam_view": fake.add_subscription("user-3", overview, "View", "Overview weekly"),
        "jo": fake.add_subscription("user-2", budget, "Workbook", "Jo's copy"),
    }


def backup(folder: Path):
    [path] = folder.glob("unlicensed_subscriptions_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {r["Subject"]: r for r in csv.DictReader(handle)}


def test_backs_up_then_deletes_only_unlicensed(subs_script, fake, subscribed, env_file, tmp_path):
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    assert [s["id"] for s in fake.subscriptions] == [subscribed["jo"]["id"]]
    rows = backup(tmp_path)
    assert set(rows) == {"Budget daily", "Overview weekly"}
    daily = rows["Budget daily"]
    assert (daily["User"], daily["Content Type"], daily["Content"], daily["Schedule"], daily["Status"]) == (
        "Sam Gone", "Workbook", "Budget", "Weekday mornings", "Deleted")
    assert rows["Overview weekly"]["Content"] == "Budget / Overview"
    assert daily["Subscription ID"] == subscribed["sam_wb"]["id"]


def test_only_subscribers_are_looked_up(subs_script, fake, subscribed, env_file, tmp_path):
    fake.add_user("bystander@example.com", "Not Subscribed", "Unlicensed")
    subs_script.main(["--env-file", str(env_file), "--dry-run"])
    lookups = sorted(p.split("/users/")[1] for _, p in fake.requests if "/users/" in p and "/groups/" not in p)
    assert lookups == ["user-2", "user-3"]
    assert not any(p.split("?")[0].endswith("/users") for _, p in fake.requests)


def test_dry_run_and_decline_delete_nothing(subs_script, fake, subscribed, env_file, tmp_path, answers, capsys):
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--dry-run"]) == 0
    assert len(fake.subscriptions) == 3 and not list(tmp_path.glob("unlicensed_*"))
    out = capsys.readouterr().out
    assert "Sam Gone" in out and "Budget daily" in out and "2, owned by 1 unlicensed user" in out
    answers([""])  # the default is No
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 130
    assert len(fake.subscriptions) == 3


def test_a_failed_delete_is_recorded(subs_script, fake, subscribed, env_file, tmp_path):
    fake.fail[("DELETE", f"/subscriptions/{subscribed['sam_view']['id']}")] = 403
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 1
    rows = backup(tmp_path)
    assert rows["Overview weekly"]["Status"] == "Failed" and rows["Budget daily"]["Status"] == "Deleted"


def test_nothing_to_do(subs_script, fake, env_file, tmp_path, capsys):
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    assert "No subscriptions on this site" in capsys.readouterr().out
    fake.add_subscription("user-2", fake.add_workbook("X", "p-mkt"))
    assert subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    assert "No subscriptions belong to unlicensed users" in capsys.readouterr().out
    assert len(fake.subscriptions) == 1


def test_a_user_that_cant_be_found_is_not_treated_as_unlicensed(subs_script, fake, subscribed, env_file, tmp_path):
    fake.subscriptions[0]["user"]["id"] = "user-gone"
    subs_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"])
    assert subscribed["sam_wb"]["id"] in [s["id"] for s in fake.subscriptions]
