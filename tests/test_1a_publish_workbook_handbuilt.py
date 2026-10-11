"""Tests for 01-publishing-workbooks/1a_publish_workbook_handbuilt.py (Story 1, part 1a).

Michael's hand-built version talks XML through Tableau Server Client, so these
exercise the fake's XML mode. Skipped when tableauserverclient isn't installed
(it's in requirements-dev.txt).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tableauserverclient")

ROOT = Path(__file__).resolve().parent.parent


def run(handbuilt_script, env_file, *args):
    return handbuilt_script.main([*map(str, args), "--env-file", str(env_file)])


def test_publishes_with_tabs_on_by_default(handbuilt_script, fake, env_file, workbook, capsys):
    assert run(handbuilt_script, env_file, workbook, "Finance") == 0
    [pub] = fake.publishes
    assert (pub["name"], pub["project_id"], pub["show_tabs"]) == ("Sales Overview", "p-fin", "true")
    assert pub["data"] == workbook.read_bytes()
    assert fake.signed_out
    assert "Project: Finance" in capsys.readouterr().out


def test_finds_projects_beyond_the_first_page(handbuilt_script, fake, env_file, workbook):
    # The fake pages two projects at a time. Marketing is on page 3, so the original
    # projects.get() (first page only) couldn't see it; TSC.Pager can.
    assert run(handbuilt_script, env_file, workbook, "Marketing") == 0
    assert fake.publishes[0]["project_id"] == "p-mkt"
    assert sum("/projects?" in path for _, path in fake.requests) > 1


def test_name_hide_tabs_and_overwrite(handbuilt_script, fake, env_file, workbook):
    fake.add_workbook("Q3 Sales", "p-mkt")
    code = run(handbuilt_script, env_file, workbook, "Marketing", "--name", "Q3 Sales", "--hide-tabs", "--mode", "Overwrite")
    assert code == 0
    [pub] = fake.publishes
    # TSC only writes showTabs when it's true, so "hide" means leaving it out and
    # relying on Tableau's default (no tabs). The repo's own script sends "false".
    assert (pub["name"], pub["show_tabs"], pub["overwrite"]) == ("Q3 Sales", None, True)


def test_create_new_refuses_a_taken_name(handbuilt_script, fake, env_file, workbook, capsys):
    fake.add_workbook("Sales Overview", "p-mkt")
    assert run(handbuilt_script, env_file, workbook, "Marketing") == 1
    assert fake.publishes == []
    assert "409" in capsys.readouterr().out
    assert fake.signed_out


def test_duplicate_project_names_stop_instead_of_guessing(handbuilt_script, fake, env_file, workbook, capsys):
    fake.projects.append({"id": "p-mkt-rep", "name": "Reporting", "parentProjectId": "p-mkt"})
    assert run(handbuilt_script, env_file, workbook, "Reporting") == 1
    assert fake.publishes == []
    out = capsys.readouterr().out
    assert "2 projects are named 'Reporting'" in out and "p-fin-rep" in out and "p-mkt-rep" in out


def test_unknown_project_lists_what_exists(handbuilt_script, fake, env_file, workbook, capsys):
    assert run(handbuilt_script, env_file, workbook, "Nope") == 1
    assert "Project 'Nope' not found. Available projects:" in capsys.readouterr().out


def test_bad_file_fails_before_signing_in(handbuilt_script, fake, env_file, tmp_path, capsys):
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    assert run(handbuilt_script, env_file, notes, "Finance") == 1
    assert "must be a .twb or .twbx" in capsys.readouterr().out
    assert fake.requests == []


def test_uses_the_repo_settings_checks(handbuilt_script, tmp_path, workbook, capsys):
    # Settings go through tcs.config, so an unfilled template is caught before any sign-in.
    env = tmp_path / ".env.template"
    env.write_text((ROOT / ".env.example").read_text())
    assert run(handbuilt_script, env, workbook, "Finance") == 1
    assert "placeholder" in capsys.readouterr().out
