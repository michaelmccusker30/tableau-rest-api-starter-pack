"""End-to-end tests for 08-permissions/08_permissions.py."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest


@pytest.fixture
def ruled(fake):
    finance = fake.add_group("Finance Team", ["user-2"])
    budget = fake.add_workbook("Budget", "p-fin-rep")
    fake.add_rule("projects/p-fin", ("group", finance["id"]), {"Read": "Allow", "Write": "Allow"})
    fake.add_rule("projects/p-fin/default", ("group", finance["id"]), {"Read": "Allow", "ExportData": "Deny"})
    fake.add_rule(f"workbooks/{budget['id']}", ("user", "user-3"), {"Read": "Allow"})
    fake.add_rule("projects/p-mkt", ("group", "g-all"), {"Read": "Allow"})
    return {"finance": finance, "budget": budget}


def rows(folder: Path):
    [path] = folder.glob("permissions_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_rules_with_names_one_row_per_capability(perm_script, fake, ruled, env_file, tmp_path):
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    found = rows(tmp_path)
    fin = [r for r in found if r["Content Type"] == "Project" and r["Name"] == "Finance"]
    assert sorted((r["Grantee"], r["Capability"], r["Mode"]) for r in fin) == [
        ("Finance Team", "Read", "Allow"), ("Finance Team", "Write", "Allow")]
    assert fin[0]["Project Permissions"] == "LockedToProject"
    default = [r for r in found if r["Content Type"] == "Project default for workbooks"]
    assert ("ExportData", "Deny") in {(r["Capability"], r["Mode"]) for r in default}
    wb = next(r for r in found if r["Content Type"] == "Workbook")
    assert (wb["Name"], wb["Project Path"], wb["Grantee Type"], wb["Grantee"]) == (
        "Budget", "Finance / Reporting", "User", "Sam Gone")
    assert fake.signed_out


def test_review_flags_direct_user_rules_and_denies(perm_script, fake, ruled, env_file, capsys):
    perm_script.main(["--env-file", str(env_file), "--dry-run", "-y"])
    out = capsys.readouterr().out
    assert "1 rule set grant to individual users" in out
    assert "1 Deny rule." in out
    assert re.search(r"Rules +6 capability rules for 3 grantees", out)
    assert re.search(r"Read +6 projects, 6 project defaults, 1 workbook", out)


def test_projects_only_reads_no_workbooks(perm_script, fake, ruled, env_file, tmp_path):
    perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--type", "projects", "-y"])
    assert {r["Content Type"] for r in rows(tmp_path)} == {"Project", "Project default for workbooks"}
    assert not any(p.split("?")[0].endswith("/site-1/workbooks") or "/site-1/workbooks/" in p for _, p in fake.requests)


def test_scope_include(perm_script, fake, ruled, env_file, tmp_path):
    perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--include", "Marketing", "-y"])
    assert {(r["Name"], r["Grantee"]) for r in rows(tmp_path)} == {("Marketing", "All Users")}


def test_users_off_limits_falls_back_to_ids(perm_script, fake, ruled, env_file, tmp_path, capsys):
    fake.fail[("GET", "/users")] = 403
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    wb = next(r for r in rows(tmp_path) if r["Content Type"] == "Workbook")
    assert wb["Grantee"] == "user-3"
    assert "can't list users" in capsys.readouterr().out


def test_failed_read_is_reported(perm_script, fake, ruled, env_file, tmp_path, capsys):
    fake.fail[("GET", f"/workbooks/{ruled['budget']['id']}/permissions")] = 403
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 1
    assert "Couldn't read workbook Finance / Reporting / Budget" in capsys.readouterr().out


def test_interactive(perm_script, fake, ruled, env_file, tmp_path, answers):
    answers(["1", "1", "1", ""])  # report; projects; every project; write
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert rows(tmp_path)



# ------------------------------------------------------------------ grant, revoke, restore

def rules_on(fake, key):
    """{(grantee id, capability): mode} for one rule set in the fake."""
    out = {}
    for entry in fake.permissions.get(key, []):
        gid = (entry.get("user") or entry.get("group"))["id"]
        for cap in entry["capabilities"]["capability"]:
            out[(gid, cap["name"])] = cap["mode"]
    return out


def changes_file(folder: Path, stem: str):
    [path] = folder.glob(f"{stem}_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return path, list(csv.DictReader(handle))


def test_grant_view_from_a_users_csv(perm_script, fake, ruled, env_file, tmp_path):
    team = tmp_path / "team.csv"
    team.write_text("USERNAME\njo@example.com\nSAM@example.com\nghost@example.com\n")
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--grant", "view", "--users-csv", str(team),
            "--workbook", "Finance/Reporting/Budget", "-y"]
    assert perm_script.main(args) == 0
    rules = rules_on(fake, f"workbooks/{ruled['budget']['id']}")
    assert rules[("user-2", "Read")] == "Allow" and rules[("user-2", "ExportData")] == "Allow"
    assert ("user-2", "ShareView") not in rules  # Tableau's View template leaves Share Customized to Explore
    assert rules[("user-3", "Read")] == "Allow"  # Sam's existing Read stays, untouched
    _, backup = changes_file(tmp_path, "permissions_backup")
    assert [(r["Grantee"], r["Capability"]) for r in backup] == [("Sam Gone", "Read")]  # the before-state
    _, changes = changes_file(tmp_path, "permissions_changes")
    assert {r["Status"] for r in changes} == {"Done"} and len(changes) == 6 + 5  # Jo: 6; Sam: 5 new


def test_grant_replaces_the_opposite_mode(perm_script, fake, ruled, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--grant", "custom",
            "--capability", "exportdata:allow", "--group", "Finance Team", "--project-defaults", "Finance", "-y"]
    assert perm_script.main(args) == 0
    assert rules_on(fake, "projects/p-fin/default")[(ruled["finance"]["id"], "ExportData")] == "Allow"
    deletes = [p for m, p in fake.requests if m == "DELETE"]
    assert deletes == [f"/api/3.29/sites/site-1/projects/p-fin/default-permissions/workbooks/groups/{ruled['finance']['id']}/ExportData/Deny"]


def test_revoke_only_named_capabilities_then_restore(perm_script, fake, ruled, env_file, tmp_path):
    first = tmp_path / "1"
    args = ["--env-file", str(env_file), "--output-dir", str(first), "--revoke", "--group", "Finance Team",
            "--capability", "Write", "--project", "Finance", "-y"]
    assert perm_script.main(args) == 0
    assert rules_on(fake, "projects/p-fin") == {(ruled["finance"]["id"], "Read"): "Allow"}
    backup, _ = changes_file(first, "permissions_backup")
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "2"), "--restore", str(backup), "-y"]) == 0
    assert rules_on(fake, "projects/p-fin") == {(ruled["finance"]["id"], "Read"): "Allow", (ruled["finance"]["id"], "Write"): "Allow"}


def test_restore_removes_rules_granted_since(perm_script, fake, ruled, env_file, tmp_path):
    first = tmp_path / "1"
    perm_script.main(["--env-file", str(env_file), "--output-dir", str(first), "--grant", "explore",
                      "--group", "All Users", "--workbook", "Budget", "-y"])
    assert len(rules_on(fake, f"workbooks/{ruled['budget']['id']}")) == 1 + 9
    backup, _ = changes_file(first, "permissions_backup")
    perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "2"), "--restore", str(backup), "-y"])
    assert rules_on(fake, f"workbooks/{ruled['budget']['id']}") == {("user-3", "Read"): "Allow"}


def test_dry_run_shows_the_difference_and_changes_nothing(perm_script, fake, ruled, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--grant", "view", "--user", "jo@example.com",
            "--workbook", "Finance/Reporting/Budget", "--dry-run"]
    assert perm_script.main(args) == 0
    out = capsys.readouterr().out
    assert "+ Jo Müller (user): AddComment, ExportData, ExportImage, Filter, Read, ViewComments" in out
    assert not any(m in ("PUT", "DELETE") for m, _ in fake.requests)
    assert not list(tmp_path.glob("permissions_*"))


def test_nothing_to_change_and_unknown_grantees(perm_script, fake, ruled, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--grant", "custom", "--capability", "Read",
            "--user", "sam@example.com", "--user", "nobody@example.com", "--workbook", "Budget", "-y"]
    assert perm_script.main(args) == 0
    out = capsys.readouterr().out
    assert "Nothing to change" in out and "Not on this site: nobody@example.com" in out


def test_locked_project_is_explained(perm_script, fake, ruled, env_file, tmp_path, capsys):
    fake.add_workbook("Locked", "p-fin")  # Finance is LockedToProject
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--grant", "view", "--group", "Finance Team",
            "--workbook", "Finance/Locked", "-y"]
    assert perm_script.main(args) == 1
    captured = capsys.readouterr()
    assert "Tableau will refuse workbook-level changes" in captured.out
    assert "change the project's default rules instead" in captured.err
    _, changes = changes_file(tmp_path, "permissions_changes")
    assert {r["Status"] for r in changes} == {"Failed"}


def test_ambiguous_workbook_name_is_refused(perm_script, fake, ruled, env_file, tmp_path, capsys):
    fake.add_workbook("Budget", "p-mkt")
    args = ["--env-file", str(env_file), "--grant", "view", "--group", "Finance Team", "--workbook", "Budget", "-y"]
    assert perm_script.main(args) == 1
    assert 'Give it as "Project/Budget"' in capsys.readouterr().err


def test_interactive_grant(perm_script, fake, ruled, env_file, tmp_path, answers):
    # Grant; items: workbooks in a project; project: 2 Finance > 2 Reporting (leaf? no, has Monthly) -> 0 choose;
    # workbooks: 1 Budget; who: groups -> pick Finance Team (sorted: All Users, Finance Team); template 4 view; confirm y
    answers(["2", "1", "2", "2", "0", "1", "1", "2", "4", "y"])
    assert perm_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert rules_on(fake, f"workbooks/{ruled['budget']['id']}")[(ruled["finance"]["id"], "Read")] == "Allow"


def test_change_flags_need_an_action(perm_script, env_file):
    with pytest.raises(SystemExit):
        perm_script.main(["--env-file", str(env_file), "--group", "X"])
    with pytest.raises(SystemExit):
        perm_script.main(["--env-file", str(env_file), "--grant", "view", "-y"])
    with pytest.raises(SystemExit):
        perm_script.main(["--env-file", str(env_file), "--grant", "custom", "--group", "X", "--project", "Y"])
