"""End-to-end tests for 05-users-and-groups/05_users_and_groups.py."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest


@pytest.fixture
def people(fake):
    """Test User (admin), Jo (Creator), Sam (Unlicensed, last in 2025), Nia (never signed in); two groups."""
    nia = fake.add_user("nia@example.com", "Nia Never", "Viewer")
    finance = fake.add_group("Finance Team", ["user-2"])
    sales = fake.add_group("Sales", ["user-2", "user-3"])
    sales["import"] = {"domainName": "local", "siteRole": "Explorer", "grantLicenseMode": "onLogin"}
    fake.add_workbook("Budget", "p-fin", owner_id="user-2")
    fake.add_workbook("Plan", "p-fin", owner_id="user-2")
    fake.add_datasource("Ledger", "p-fin", owner_id="user-2")
    return {"nia": nia, "finance": finance, "sales": sales}


def read(folder: Path, stem: str):
    [path] = folder.glob(f"{stem}_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_three_files_with_groups_and_ownership(people_script, fake, people, env_file, tmp_path):
    assert people_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    users = {r["Username"]: r for r in read(tmp_path, "users")}
    jo = users["jo@example.com"]
    assert (jo["Name"], jo["Site Role"], jo["Groups"], jo["Group Count"]) == ("Jo Müller", "Creator", "Finance Team, Sales", "2")
    assert (jo["Workbooks Owned"], jo["Data Sources Owned"]) == ("2", "1")
    assert users["nia@example.com"]["Last Login (UTC)"] == "" and users["nia@example.com"]["Days Since Login"] == ""
    assert int(users["sam@example.com"]["Days Since Login"]) > 200
    assert users["test@example.com"]["Groups"] == ""  # All Users doesn't count

    groups = {r["Group"]: r for r in read(tmp_path, "groups")}
    assert groups["All Users"]["Members"] == "4"
    assert groups["Sales"]["Licence On Sign-in"] == "Explorer (onLogin)"

    memberships = read(tmp_path, "memberships")
    assert sum(1 for m in memberships if m["Group"] == "Sales") == 2
    assert fake.signed_out


def test_review_calls_out_who_needs_a_look(people_script, fake, people, env_file, tmp_path, capsys):
    people_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert "1 user unlicensed: Sam Gone" in out
    assert "1 user never signed in: Nia Never" in out
    assert "not signed in for 90+ days: Sam Gone" in out
    assert "2 users in no group besides All Users: Nia Never, Test User" in out
    assert re.search(r"Users +4: ", out) and "1 Unlicensed" in out and "1 SiteAdministratorCreator" in out
    assert not list(tmp_path.glob("*.csv"))


def test_stale_days_threshold(people_script, fake, people, env_file, tmp_path, capsys):
    people_script.main(["--env-file", str(env_file), "--stale-days", "100000", "--dry-run"])
    assert "0 users not signed in for 100000+ days" in capsys.readouterr().out


def test_one_call_per_group_for_members(people_script, fake, people, env_file, tmp_path):
    people_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"])
    member_calls = {path.split("?")[0] for _, path in fake.requests if path.split("?")[0].endswith("/users") and "/groups/" in path}
    assert len(member_calls) == 3  # All Users, Finance Team, Sales


def test_interactive_confirm_and_decline(people_script, fake, people, env_file, tmp_path, answers):
    answers(["n"])
    assert people_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "no")]) == 130
    assert not (tmp_path / "no").exists()


def test_bad_stale_days_is_refused(people_script, env_file):
    with pytest.raises(SystemExit):
        people_script.main(["--env-file", str(env_file), "--stale-days", "0"])
