"""End-to-end tests for 02-content-inventory/02_content_inventory.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def outputs(folder: Path):
    """(workbook rows by name, project rows by full path) from the one run in ``folder``."""
    [workbooks_file] = folder.glob("workbooks_acme_*.csv")
    [projects_file] = folder.glob("projects_acme_*.csv")
    return (
        {r["Workbook"]: r for r in read_csv(workbooks_file)},
        {r["Full Path"]: r for r in read_csv(projects_file)},
    )


def test_yes_writes_every_workbook_and_project(inventory_script, site_content, env_file, tmp_path):
    out = tmp_path / "out"
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(out), "--yes"]) == 0
    workbooks, projects = outputs(out)

    assert sorted(workbooks) == ["Budget", "Campaigns", "Home", "Leftover", "Plan", "Scratch", "Secret"]
    budget = workbooks["Budget"]
    assert budget["Project Path"] == "Finance / Reporting / Monthly"
    assert (budget["Top-Level Project"], budget["Project"], budget["Location"]) == ("Finance", "Monthly", "Project")
    assert (budget["Owner Name"], budget["Owner Email"], budget["Owner Username"]) == (
        "Jo Müller",
        "jo@example.com",
        "jo@example.com",
    )
    assert (budget["Owner Site Role"], budget["Owner Last Login (UTC)"]) == ("Creator", "2026-10-01 12:30:00")
    assert (budget["Created (UTC)"], budget["Updated (UTC)"]) == ("2026-09-01 09:00:00", "2026-09-12 18:20:11")
    assert (budget["Size (MB)"], budget["Tags"]) == ("1", "kpi, q3")
    assert workbooks["Leftover"]["Owner Site Role"] == "Unlicensed"
    assert "Show Tabs" not in budget

    # Outside the visible tree: kept and labelled, never dropped.
    assert (workbooks["Scratch"]["Location"], workbooks["Scratch"]["Project Path"]) == (
        "Personal space",
        "(Personal space)",
    )
    assert workbooks["Secret"]["Location"] == "Project not visible to you"

    # The project tree, without the hidden system project.
    assert list(projects) == [
        "Default",
        "Finance",
        "Finance / Planning",
        "Finance / Reporting",
        "Finance / Reporting / Monthly",
        "Marketing",
    ]
    finance = projects["Finance"]
    assert (finance["Depth"], finance["Workbooks"], finance["Workbooks Incl. Sub-projects"]) == ("1", "0", "2")
    assert (finance["Content Permissions"], finance["Description"]) == ("LockedToProject", "Money")
    assert (finance["Owner Name"], finance["Owner Email"]) == ("Jo Müller", "jo@example.com")
    assert projects["Marketing"]["Owner Name"] == "Test User"
    assert projects["Finance / Reporting / Monthly"]["Depth"] == "3"
    assert projects["Finance / Reporting / Monthly"]["Parent Project ID"] == "p-fin-rep"


def test_owner_details_come_from_one_field_expression(inventory_script, site_content, env_file, tmp_path):
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--yes"])
    workbook_calls = [path for method, path in site_content.requests if "/workbooks?" in path]
    assert len(workbook_calls) == 4  # 7 workbooks, 2 per page in the fake
    wanted = "fields=_default_%2Cowner.name%2Cowner.fullName%2Cowner.email%2Cowner.siteRole%2Cowner.lastLogin"
    assert all(wanted in path for path in workbook_calls)
    project_calls = [path for method, path in site_content.requests if "/projects?" in path]
    assert all("fields=_default_%2Cowner.name%2Cowner.fullName%2Cowner.email" in path for path in project_calls)
    assert not any("/users?" in path for _, path in site_content.requests)
    assert site_content.signed_out


def test_owners_fall_back_to_the_user_list(inventory_script, site_content, env_file, tmp_path):
    site_content.honor_fields = False
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--yes"]) == 0
    workbooks, _ = outputs(tmp_path)
    assert (workbooks["Budget"]["Owner Name"], workbooks["Budget"]["Owner Email"]) == ("Jo Müller", "jo@example.com")
    assert workbooks["Leftover"]["Owner Site Role"] == "Unlicensed"  # the user list carries roles too


def test_owners_keep_user_names_when_users_cant_be_listed(inventory_script, site_content, env_file, tmp_path):
    site_content.honor_fields = False
    site_content.users_forbidden = True
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--yes"]) == 0
    workbooks, _ = outputs(tmp_path)
    assert (workbooks["Budget"]["Owner Name"], workbooks["Budget"]["Owner Email"]) == ("jo@example.com", "")


def test_include_a_sub_project_branch(inventory_script, site_content, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--include", "finance/reporting", "-y"]
    assert inventory_script.main(args) == 0
    workbooks, projects = outputs(tmp_path)
    assert list(workbooks) == ["Budget"]  # Personal space and hidden projects aren't in a named branch
    assert list(projects) == ["Finance / Reporting", "Finance / Reporting / Monthly"]


def test_include_several_branches(inventory_script, site_content, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--include", "Marketing", "--include", "Default", "-y"]
    assert inventory_script.main(args) == 0
    workbooks, projects = outputs(tmp_path)
    assert sorted(workbooks) == ["Campaigns", "Home", "Leftover"]
    assert list(projects) == ["Default", "Marketing"]


def test_exclude_a_branch_keeps_everything_else(inventory_script, site_content, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--exclude", "Finance", "-y"]
    assert inventory_script.main(args) == 0
    workbooks, projects = outputs(tmp_path)
    assert sorted(workbooks) == ["Campaigns", "Home", "Leftover", "Scratch", "Secret"]
    assert list(projects) == ["Default", "Marketing"]
    out = capsys.readouterr().out
    assert "Every project except: Finance" in out
    assert "5 (1 in personal space, 1 in project not visible to you)" in out


def test_unknown_project_path_is_a_clean_error(inventory_script, site_content, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path / "out"), "--include", "Finance/Nope", "-y"]
    assert inventory_script.main(args) == 1
    assert 'No project named "Nope" under "Finance"' in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
    assert site_content.signed_out


def test_empty_include_is_refused(inventory_script, env_file):
    with pytest.raises(SystemExit):
        inventory_script.main(["--env-file", str(env_file), "--include", " "])


def test_interactive_pick_top_level_projects(inventory_script, site_content, env_file, tmp_path, answers, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    # Scope: 3 = only some top-level projects. Roots: 1 Default, 2 Finance, 3 Marketing.
    # Then the default yes. The files go to ./output, shown in the review: no folder prompt.
    answers(["3", "2", ""])
    assert inventory_script.main(["--env-file", str(env_file)]) == 0
    workbooks, projects = outputs(tmp_path / "output")
    assert sorted(workbooks) == ["Budget", "Plan"]
    assert list(projects)[0] == "Finance"
    out = capsys.readouterr().out
    assert "Finance  2 workbooks" in out
    assert "Only: Finance" in out


def test_interactive_scope_with_output_dir_flag(inventory_script, site_content, env_file, tmp_path, answers):
    answers(["1", "y"])  # every project, then confirm; the folder comes from the flag
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "flag")]) == 0
    workbooks, _ = outputs(tmp_path / "flag")
    assert len(workbooks) == 7


def test_declining_the_review_writes_nothing(inventory_script, site_content, env_file, tmp_path, answers, capsys):
    answers(["1", "n"])
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "out")]) == 130
    assert not (tmp_path / "out").exists()
    assert "No files were written" in capsys.readouterr().out


def test_dry_run_shows_review_and_preview_only(inventory_script, site_content, env_file, tmp_path, capsys):
    out_dir = tmp_path / "out"
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(out_dir), "--dry-run", "-y"]) == 0
    assert not out_dir.exists()
    out = capsys.readouterr().out
    assert "Workbooks     7 (1 in personal space, 1 in project not visible to you)" in out
    assert "First 5 of 7:" in out
    assert "Budget  Finance / Reporting / Monthly  owner Jo Müller" in out
    assert "Dry run: no files were written." in out


def test_formula_like_names_are_written_as_text(inventory_script, fake, env_file, tmp_path):
    fake.add_workbook("=HYPERLINK(\"http://x\")", "p-mkt")
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    [row] = read_csv(next(tmp_path.glob("workbooks_*.csv")))
    assert row["Workbook"] == "'=HYPERLINK(\"http://x\")"


def test_file_has_a_byte_order_mark_for_excel(inventory_script, site_content, env_file, tmp_path):
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"])
    raw = next(tmp_path.glob("workbooks_*.csv")).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Jo Müller".encode() in raw


def test_unwritable_output_folder_is_a_clean_error(inventory_script, site_content, env_file, tmp_path, capsys):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(blocker), "-y"]) == 1
    assert "Can't write" in capsys.readouterr().err


def test_no_workbooks_still_writes_the_project_tree(inventory_script, fake, env_file, tmp_path, capsys):
    assert inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    workbooks, projects = outputs(tmp_path)
    assert workbooks == {} and len(projects) == 6
    assert "header row only" in capsys.readouterr().out


def test_review_flags_workbooks_owned_by_unlicensed_users(inventory_script, site_content, env_file, tmp_path, capsys):
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--dry-run", "-y"])
    assert '1 workbook owned by unlicensed users. Filter Owner Site Role for "Unlicensed"' in capsys.readouterr().out


def test_no_unlicensed_warning_when_every_owner_is_licensed(inventory_script, fake, env_file, tmp_path, capsys):
    fake.add_workbook("Home", "p-default")
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--dry-run", "-y"])
    assert "owned by unlicensed users" not in capsys.readouterr().out


def test_project_tree_without_owner_fields_is_unchanged(fake, env_file):
    """1b and 1c don't ask for project owners, so their requests stay as they were."""
    from tcs.client import TableauClient
    from tcs.config import load_settings
    from tcs.projects import fetch_project_tree

    with TableauClient(load_settings(env_file)) as client:
        tree = fetch_project_tree(client)
    assert tree.find_by_path("Finance").owner_name == ""
    assert not any("fields=" in path for _, path in fake.requests if "/projects?" in path)


def test_total_views_per_workbook_from_one_views_call(inventory_script, site_content, env_file, tmp_path):
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"])
    workbooks, _ = outputs(tmp_path)
    assert workbooks["Budget"]["Total Views"] == "15"  # Overview 12 + Detail 3
    assert workbooks["Campaigns"]["Total Views"] == "40"
    assert workbooks["Home"]["Total Views"] == "0"  # no views at all
    view_calls = [path for _, path in site_content.requests if path.split("?")[0].endswith("/views")]
    assert len(view_calls) == 3  # 5 views, 2 per page in the fake
    assert all("includeUsageStatistics=true" in path for path in view_calls)
