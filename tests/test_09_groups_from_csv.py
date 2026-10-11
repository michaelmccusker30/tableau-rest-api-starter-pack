"""End-to-end tests for 09-groups-from-csv/09_groups_from_csv.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.fixture
def team(fake):
    finance = fake.add_group("Finance Team", ["user-2"])
    return {"finance": finance}


def results(folder: Path):
    [path] = folder.glob("groups_from_csv_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {(r["Group"], r["User"]): r for r in csv.DictReader(handle)}


def members_of(fake, name):
    group = next(g for g in fake.groups if g["name"] == name)
    return set(fake.members[group["id"]])


def test_adds_by_username_or_email_and_skips_existing(groups_script, fake, team, env_file, tmp_path):
    source = tmp_path / "people.csv"
    source.write_text("Email,Group\nJO@example.com,Finance Team\nsam@example.com,finance team\nghost@example.com,Finance Team\n")
    args = ["--env-file", str(env_file), "--csv", str(source), "--output-dir", str(tmp_path), "-y"]
    assert groups_script.main(args) == 0
    assert members_of(fake, "Finance Team") == {"user-2", "user-3"}
    found = results(tmp_path)
    assert found[("Finance Team", "JO@example.com")]["Status"] == "Already a member"
    assert found[("Finance Team", "sam@example.com")]["Status"] == "Added"
    assert found[("Finance Team", "ghost@example.com")]["Status"] == "User not on site"
    assert source.read_text().startswith("Email,Group")  # the input is never rewritten


def test_one_group_for_everyone_and_creating_it_needs_permission(groups_script, fake, team, env_file, tmp_path, capsys):
    source = tmp_path / "pilot.csv"
    source.write_text("username\ntest@example.com\nsam@example.com\n")
    args = ["--env-file", str(env_file), "--csv", str(source), "--group", "Pilot", "--output-dir", str(tmp_path), "-y"]
    assert groups_script.main(args) == 1
    assert "Add --create-groups" in capsys.readouterr().err
    assert not any(g["name"] == "Pilot" for g in fake.groups)
    assert groups_script.main(args + ["--create-groups"]) == 0
    assert members_of(fake, "Pilot") == {"user-1", "user-3"}


def test_never_removes_anyone(groups_script, fake, team, env_file, tmp_path):
    source = tmp_path / "one.csv"
    source.write_text("user,group\nsam@example.com,Finance Team\n")
    groups_script.main(["--env-file", str(env_file), "--csv", str(source), "--output-dir", str(tmp_path), "-y"])
    assert "user-2" in members_of(fake, "Finance Team")
    assert not any(method == "DELETE" for method, _ in fake.requests)


def test_excel_windows_csv_with_non_breaking_spaces(groups_script, fake, team, env_file, tmp_path):
    source = tmp_path / "excel.csv"
    source.write_bytes("User Name,Group Name\nsam@example.com\xa0,Finance Team\n".encode("cp1252"))
    assert groups_script.main(["--env-file", str(env_file), "--csv", str(source), "--output-dir", str(tmp_path), "-y"]) == 0
    assert "user-3" in members_of(fake, "Finance Team")


def test_licence_granting_group_is_called_out(groups_script, fake, team, env_file, tmp_path, capsys):
    team["finance"]["import"] = {"siteRole": "Explorer", "grantLicenseMode": "onLogin"}
    source = tmp_path / "people.csv"
    source.write_text("user,group\nsam@example.com,Finance Team\n")
    groups_script.main(["--env-file", str(env_file), "--csv", str(source), "--dry-run", "-y"])
    out = capsys.readouterr().out
    assert "grants the Explorer role when members sign in" in out
    assert "user-3" not in members_of(fake, "Finance Team")


def test_failure_is_recorded(groups_script, fake, team, env_file, tmp_path):
    fake.fail[("POST", f"/groups/{team['finance']['id']}/users")] = 403
    source = tmp_path / "people.csv"
    source.write_text("user,group\nsam@example.com,Finance Team\n")
    assert groups_script.main(["--env-file", str(env_file), "--csv", str(source), "--output-dir", str(tmp_path), "-y"]) == 1
    assert results(tmp_path)[("Finance Team", "sam@example.com")]["Status"] == "Failed"


def test_interactive_asks_for_file_and_group(groups_script, fake, team, env_file, tmp_path, answers):
    source = tmp_path / "people.csv"
    source.write_text("email\nsam@example.com\n")
    answers([str(source), "Finance Team", "y"])
    assert groups_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert "user-3" in members_of(fake, "Finance Team")


def test_missing_user_column_fails_before_sign_in(groups_script, fake, env_file, tmp_path, capsys):
    source = tmp_path / "bad.csv"
    source.write_text("Department\nSales\n")
    assert groups_script.main(["--env-file", str(env_file), "--csv", str(source), "--group", "X", "-y"]) == 1
    assert "No user column" in capsys.readouterr().err
    assert not any("signin" in p for _, p in fake.requests)
