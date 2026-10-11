"""End-to-end tests for 01-publishing-workbooks/1b_publish_workbook.py (Story 1, part 1b)."""

from __future__ import annotations

import pytest

from tcs import publishing


def test_flags_only_publishes_new_workbook(publish_script, fake, env_file, workbook):
    code = publish_script.main(
        ["--env-file", str(env_file), "--file", str(workbook), "--project", "finance/reporting", "--yes"]
    )
    assert code == 0
    [pub] = fake.publishes
    assert pub["name"] == "Sales Overview"  # defaults to the file name
    assert pub["project_id"] == "p-fin-rep"
    assert pub["overwrite"] is False
    assert pub["show_tabs"] == "false"
    assert pub["data"] == workbook.read_bytes()
    assert pub["filename"] == "Sales Overview.twbx"
    assert fake.signed_out


def test_interactive_drills_into_subprojects(publish_script, fake, env_file, workbook, answers, capsys):
    # Workbook picker: 1 = Archive/, 2 = Sales Overview.twbx
    # Top level (sorted, hidden system project removed): 1 Default, 2 Finance, 3 Marketing
    # Finance: 0 publish here, 1 Planning, 2 Reporting > ; Reporting: 0 publish here, 1 Monthly
    answers(["2", "2", "2", "0", "Sales (Reporting)", "y"])
    assert publish_script.main(["--env-file", str(env_file)]) == 0
    [pub] = fake.publishes
    assert (pub["name"], pub["project_id"]) == ("Sales (Reporting)", "p-fin-rep")
    out = capsys.readouterr().out
    assert "External Assets Default Project" not in out
    assert "Finance / Reporting" in out


def test_picking_a_leaf_project_selects_it(publish_script, fake, env_file, workbook, answers):
    # Finance > Planning has no sub-projects, so choosing it ends the picker.
    answers(["2", "2", "1", "", "y"])
    assert publish_script.main(["--env-file", str(env_file)]) == 0
    assert fake.publishes[0]["project_id"] == "p-fin-plan"


def test_back_and_paste_path(publish_script, fake, env_file, workbook, answers):
    # Paste the file path (drag-and-drop style escaping), then go into Finance, back out, pick Default.
    escaped = str(workbook).replace(" ", "\\ ")
    answers(["p", escaped, "2", "b", "1", "", "y"])
    assert publish_script.main(["--env-file", str(env_file)]) == 0
    assert fake.publishes[0]["project_id"] == "p-default"


def test_existing_name_with_yes_and_no_overwrite_stops(publish_script, fake, env_file, workbook, capsys):
    fake.add_workbook("Sales Overview", "p-mkt")
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Marketing", "-y"])
    assert code == 1
    assert fake.publishes == []
    assert "--overwrite" in capsys.readouterr().err


def test_existing_name_overwrite_flag(publish_script, fake, env_file, workbook):
    fake.add_workbook("Sales Overview", "p-mkt")
    code = publish_script.main(
        ["--env-file", str(env_file), "--file", str(workbook), "--project", "Marketing", "--overwrite", "-y"]
    )
    assert code == 0
    assert fake.publishes[0]["overwrite"] is True
    assert [w["name"] for w in fake.workbooks] == ["Sales Overview"]


def test_existing_name_interactive_rename(publish_script, fake, env_file, workbook, answers):
    fake.add_workbook("Sales Overview", "p-mkt")
    # name prompt (Enter = default) -> conflict menu: 2 = different name -> new name -> confirm
    answers(["", "2", "Sales Overview v2", "y"])
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Marketing"])
    assert code == 0
    assert fake.publishes[0]["name"] == "Sales Overview v2"
    assert fake.publishes[0]["overwrite"] is False


def test_same_name_in_other_project_is_not_a_conflict(publish_script, fake, env_file, workbook):
    fake.add_workbook("Sales Overview", "p-default")
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Marketing", "-y"])
    assert code == 0
    assert fake.publishes[0]["overwrite"] is False


def test_dry_run_publishes_nothing(publish_script, fake, env_file, workbook, capsys):
    code = publish_script.main(
        ["--env-file", str(env_file), "--file", str(workbook), "--project", "Default", "--dry-run"]
        + ["--name", "X"]
    )
    assert code == 0
    assert fake.publishes == []
    assert "Dry run" in capsys.readouterr().out


def test_declining_confirmation_cancels(publish_script, fake, env_file, workbook, answers):
    answers(["", "n"])
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Default"])
    assert code == 130
    assert fake.publishes == []
    assert fake.signed_out


def test_chunked_upload_for_big_files(publish_script, fake, env_file, workbook, monkeypatch):
    monkeypatch.setattr(publishing, "SINGLE_REQUEST_LIMIT", 1000)
    monkeypatch.setattr(publishing, "CHUNK_SIZE", 700)
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Default", "-y"])
    assert code == 0
    [pub] = fake.publishes
    assert pub["chunked"] and pub["data"] == workbook.read_bytes()
    puts = [p for m, p in fake.requests if m == "PUT"]
    assert len(puts) == 3  # 2,100 bytes / 700 bytes per chunk


def test_bad_project_path_lists_options(publish_script, fake, env_file, workbook, capsys):
    code = publish_script.main(
        ["--env-file", str(env_file), "--file", str(workbook), "--project", "Finance/Monthy", "-y"]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert 'No project named "Monthy" under "Finance"' in err
    assert "Planning, Reporting" in err


def test_bad_credentials_explain_what_to_check(publish_script, fake, env_file, workbook, capsys):
    env_file.write_text(env_file.read_text().replace("test-secret", "wrong"))
    code = publish_script.main(["--env-file", str(env_file), "--file", str(workbook), "--project", "Default", "-y"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Sign in failed (HTTP 401, Tableau error 401001)" in err
    assert "TABLEAU_PAT_SECRET" in err


def test_yes_requires_file_and_project(publish_script):
    with pytest.raises(SystemExit):
        publish_script.main(["--yes", "--file", "x.twbx"])
