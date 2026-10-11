"""End-to-end tests for 01-publishing-workbooks/1c_bulk_publish_workbooks.py (Story 1, part 1c) against the fake REST API.

The scanned tree comes from the workbook_folder fixture. Its numbered list is:
  1 Finance/Budget.twbx   2 Finance/Q3/Forecast.twbx   3 Sales.twbx
Projects at the top level: 1 Default, 2 Finance (has sub-projects), 3 Marketing.
"""

from __future__ import annotations

import pytest


def run_flags(bulk_script, env_file, folder, *extra):
    return bulk_script.main(["--env-file", str(env_file), "--folder", str(folder), *extra])


def test_interactive_some_into_a_parent_project(bulk_script, fake, env_file, workbook_folder, answers, capsys):
    # Folder picker: Finance/, Old/ (hidden folder left out); 0 scans the folder it opened in.
    # Pick 1 and 3; Finance has sub-projects, so 0 publishes into Finance itself.
    answers(["p", str(workbook_folder), "0", "1,3", "2", "0", "y"])
    assert bulk_script.main(["--env-file", str(env_file)]) == 0
    assert [(p["name"], p["project_id"], p["show_tabs"], p["overwrite"]) for p in fake.publishes] == [
        ("Budget", "p-fin", "true", False),
        ("Sales", "p-fin", "true", False),
    ]
    assert fake.signed_out
    out = capsys.readouterr().out
    assert "Found 3 .twbx files" in out
    assert "Published  2" in out


def test_drill_into_subfolder_and_reask_on_bad_selection(
    bulk_script, fake, env_file, workbook_folder, answers, capsys
):
    # Into Finance/, scan it (Budget, Q3/Forecast); "9" is out of range and is asked again; Marketing is a leaf.
    answers(["p", str(workbook_folder), "1", "0", "9", "a", "3", "y"])
    assert bulk_script.main(["--env-file", str(env_file)]) == 0
    assert sorted(p["name"] for p in fake.publishes) == ["Budget", "Forecast"]
    assert "9 is outside 1-2" in capsys.readouterr().out


def test_clash_skip_all(bulk_script, fake, env_file, workbook_folder, answers, capsys):
    fake.add_workbook("Sales", "p-mkt")
    # Clash menu: 1 overwrite all, 2 skip all.
    answers(["a", "3", "2", "y"])
    assert run_flags(bulk_script, env_file, workbook_folder) == 0
    assert sorted(p["name"] for p in fake.publishes) == ["Budget", "Forecast"]
    out = capsys.readouterr().out
    assert "exists (last updated 2026-09-12, owner Test User)" in out
    assert "1 of the selected workbooks already exists" in out
    assert "Skip it (publish only the 2 new ones)" in out
    assert "Skipped    1 (already existed)" in out


def test_clash_overwrite_all_interactive(bulk_script, fake, env_file, workbook_folder, answers):
    fake.add_workbook("Sales", "p-mkt")
    answers(["a", "3", "1", "y"])
    assert run_flags(bulk_script, env_file, workbook_folder) == 0
    assert {p["name"]: p["overwrite"] for p in fake.publishes} == {"Budget": False, "Forecast": False, "Sales": True}


def test_flags_only_overwrite_only_flips_known_clashes(bulk_script, fake, env_file, workbook_folder):
    fake.add_workbook("Sales", "p-mkt")
    code = run_flags(bulk_script, env_file, workbook_folder, "--select", "all", "--project", "marketing", "--overwrite", "--yes")
    assert code == 0
    assert {p["name"]: p["overwrite"] for p in fake.publishes} == {"Budget": False, "Forecast": False, "Sales": True}
    assert all(p["show_tabs"] == "true" for p in fake.publishes)


def test_yes_with_clash_and_no_choice_stops(bulk_script, fake, env_file, workbook_folder, capsys):
    fake.add_workbook("Sales", "p-mkt")
    code = run_flags(bulk_script, env_file, workbook_folder, "--select", "3", "--project", "Marketing", "--yes")
    assert code == 1
    assert fake.publishes == []
    assert "--skip-existing" in capsys.readouterr().err


def test_everything_skipped_publishes_nothing(bulk_script, fake, env_file, workbook_folder, capsys):
    fake.add_workbook("Sales", "p-mkt")
    code = run_flags(
        bulk_script, env_file, workbook_folder, "--select", "3", "--project", "Marketing", "--skip-existing", "--yes"
    )
    assert code == 0
    assert fake.publishes == []
    assert "Nothing to publish" in capsys.readouterr().out


def test_same_name_in_two_subfolders_is_refused(bulk_script, fake, env_file, workbook_folder, answers, capsys):
    (workbook_folder / "Old" / "SALES.twbx").write_bytes(b"PK dup")
    # List is now 1 Budget, 2 Forecast, 3 Old/SALES, 4 Sales: "a" is refused, then 1,4 is fine.
    answers(["a", "1,4", "1", "y"])
    assert run_flags(bulk_script, env_file, workbook_folder) == 0
    out = capsys.readouterr().out
    assert "share a name" in out and "Old/SALES.twbx, Sales.twbx" in out
    assert sorted(p["name"] for p in fake.publishes) == ["Budget", "Sales"]


def test_bad_select_flag_fails_before_signing_in(bulk_script, fake, env_file, workbook_folder, capsys):
    code = run_flags(bulk_script, env_file, workbook_folder, "--select", "2-7", "--project", "Marketing", "--yes")
    assert code == 1
    assert "--select: 2-7 is outside 1-3" in capsys.readouterr().err
    assert not any("signin" in path for _, path in fake.requests)


def test_dry_run_publishes_nothing(bulk_script, fake, env_file, workbook_folder, capsys):
    code = run_flags(
        bulk_script, env_file, workbook_folder, "--select", "1-2", "--project", "Finance/Reporting/Monthly", "--dry-run", "--yes"
    )
    assert code == 0
    assert fake.publishes == []
    assert "would publish 2 workbooks" in capsys.readouterr().out


def test_one_failure_doesnt_stop_the_batch(bulk_script, fake, env_file, workbook_folder, monkeypatch, capsys):
    real_publish = bulk_script.publish_workbook

    def flaky(client, plan, progress):
        if plan.name == "Budget":
            raise bulk_script.TableauError("Publish workbook failed (HTTP 403, Tableau error 403004)", status=403)
        return real_publish(client, plan, progress)

    monkeypatch.setattr(bulk_script, "publish_workbook", flaky)
    code = run_flags(bulk_script, env_file, workbook_folder, "--select", "all", "--project", "Marketing", "--yes")
    assert code == 1  # so a scheduled job notices
    assert sorted(p["name"] for p in fake.publishes) == ["Forecast", "Sales"]
    captured = capsys.readouterr()
    assert "Failed     1" in captured.out
    assert "Budget (Finance/Budget.twbx): Publish workbook failed (HTTP 403" in captured.err


def test_name_appearing_mid_run_is_reported_not_replaced(bulk_script, fake, env_file, workbook_folder, monkeypatch, capsys):
    real_publish = bulk_script.publish_workbook

    def racing(client, plan, progress):
        if plan.name == "Sales":
            fake.add_workbook("Sales", "p-mkt")  # someone else publishes it after our check
        return real_publish(client, plan, progress)

    monkeypatch.setattr(bulk_script, "publish_workbook", racing)
    code = run_flags(bulk_script, env_file, workbook_folder, "--select", "3", "--project", "Marketing", "--yes")
    assert code == 1
    assert fake.publishes == []
    assert "appeared in the project during the run" in capsys.readouterr().err


def test_empty_folder(bulk_script, fake, env_file, tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert run_flags(bulk_script, env_file, tmp_path / "empty") == 1
    assert "No .twbx files" in capsys.readouterr().err


def test_yes_needs_select(bulk_script, workbook_folder, capsys):
    with pytest.raises(SystemExit):
        bulk_script.parse_args(["--folder", str(workbook_folder), "--project", "Marketing", "--yes"])
    assert "--select" in capsys.readouterr().err
