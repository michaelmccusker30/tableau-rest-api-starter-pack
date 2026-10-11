"""End-to-end tests for 04-move-workbooks/04_move_workbooks.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.fixture
def books(fake):
    """Three workbooks in Marketing, one in Finance > Planning, and a name clash waiting in Default."""
    made = {
        "Alpha": fake.add_workbook("Alpha", "p-mkt"),
        "Beta": fake.add_workbook("Beta", "p-mkt"),
        "Gamma": fake.add_workbook("Gamma", "p-mkt"),
        "Plan": fake.add_workbook("Plan", "p-fin-plan"),
    }
    fake.add_workbook("Beta", "p-default")  # Default already has a Beta
    return made


def project_of(fake, workbook):
    return next(w for w in fake.workbooks if w["id"] == workbook["id"])["project"]["id"]


def read_log(folder: Path):
    [log] = folder.glob("moves_acme_*.csv")
    with log.open(encoding="utf-8-sig", newline="") as handle:
        return log, {r["Workbook"]: r for r in csv.DictReader(handle)}


def test_move_selected_from_a_project_and_log_it(move_script, fake, books, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--from-project", "Marketing", "--select", "1,3",
            "--to-project", "Finance/Reporting", "--output-dir", str(tmp_path), "-y"]
    assert move_script.main(args) == 0
    assert project_of(fake, books["Alpha"]) == "p-fin-rep"
    assert project_of(fake, books["Gamma"]) == "p-fin-rep"
    assert project_of(fake, books["Beta"]) == "p-mkt"
    _, log = read_log(tmp_path)
    assert log["Alpha"]["Status"] == "Moved"
    assert (log["Alpha"]["From Project"], log["Alpha"]["To Project"]) == ("Marketing", "Finance / Reporting")
    assert (log["Alpha"]["From Project ID"], log["Alpha"]["To Project ID"]) == ("p-mkt", "p-fin-rep")
    puts = [path for method, path in fake.requests if method == "PUT"]
    assert len(puts) == 2


def test_undo_puts_them_back(move_script, fake, books, env_file, tmp_path):
    move_script.main(["--env-file", str(env_file), "--from-project", "Marketing", "--select", "all",
                      "--to-project", "Finance", "--output-dir", str(tmp_path / "1"), "-y"])
    log, _ = read_log(tmp_path / "1")
    assert {project_of(fake, books[n]) for n in ("Alpha", "Beta", "Gamma")} == {"p-fin"}
    assert move_script.main(["--env-file", str(env_file), "--undo", str(log),
                             "--output-dir", str(tmp_path / "2"), "-y"]) == 0
    assert {project_of(fake, books[n]) for n in ("Alpha", "Beta", "Gamma")} == {"p-mkt"}
    _, undo_log = read_log(tmp_path / "2")  # an undo logs too, so it can be undone
    assert undo_log["Alpha"]["To Project ID"] == "p-mkt"


def test_undo_leaves_workbooks_that_moved_again(move_script, fake, books, env_file, tmp_path, capsys):
    move_script.main(["--env-file", str(env_file), "--from-project", "Marketing", "--select", "1",
                      "--to-project", "Finance", "--output-dir", str(tmp_path / "1"), "-y"])
    log, _ = read_log(tmp_path / "1")
    next(w for w in fake.workbooks if w["id"] == books["Alpha"]["id"])["project"] = {"id": "p-default", "name": "Default"}
    assert move_script.main(["--env-file", str(env_file), "--undo", str(log), "--output-dir", str(tmp_path / "2"), "-y"]) == 0
    assert project_of(fake, books["Alpha"]) == "p-default"
    assert "moved again since; left alone" in capsys.readouterr().out


def test_from_csv_reports_unknown_ids_and_skips_clashes(move_script, fake, books, env_file, tmp_path, capsys):
    source = tmp_path / "list.csv"
    source.write_text(
        "Workbook,Workbook ID\n"
        f"Alpha,{books['Alpha']['id']}\n"
        f"Beta,{books['Beta']['id']}\n"
        "Gone,wb-missing\n",
        encoding="cp1252",
    )
    args = ["--env-file", str(env_file), "--from-csv", str(source), "--to-project", "Default",
            "--output-dir", str(tmp_path), "-y"]
    assert move_script.main(args) == 0
    assert project_of(fake, books["Alpha"]) == "p-default"
    assert project_of(fake, books["Beta"]) == "p-mkt"  # Default already has a Beta
    out = capsys.readouterr().out
    assert "name taken in the destination" in out
    assert "wb-missing" in out and "Not found" in out
    _, log = read_log(tmp_path)
    assert log["Beta"]["Status"] == "Skipped"


def test_entry_02_inventory_works_as_input(move_script, inventory_script, fake, books, env_file, tmp_path):
    inventory_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "inv"), "--include", "Marketing", "-y"])
    [inventory] = (tmp_path / "inv").glob("workbooks_*.csv")
    args = ["--env-file", str(env_file), "--from-csv", str(inventory), "--to-project", "Finance/Planning",
            "--output-dir", str(tmp_path), "-y"]
    assert move_script.main(args) == 0
    assert {project_of(fake, books[n]) for n in ("Alpha", "Beta", "Gamma")} == {"p-fin-plan"}


def test_already_there_and_same_name_in_batch(move_script, fake, books, env_file, tmp_path, capsys):
    twin = fake.add_workbook("Alpha", "p-fin-plan")
    source = tmp_path / "list.csv"
    source.write_text(f"id\n{books['Alpha']['id']}\n{twin['id']}\n{books['Plan']['id']}\n")
    args = ["--env-file", str(env_file), "--from-csv", str(source), "--to-project", "Finance/Reporting",
            "--output-dir", str(tmp_path), "-y"]
    assert move_script.main(args) == 0
    out = capsys.readouterr().out
    assert "same name as another workbook in this batch" in out
    capsys.readouterr()
    source.write_text(f"id\n{books['Plan']['id']}\n")
    assert move_script.main(args) == 0
    assert "already there" in capsys.readouterr().out


def test_dry_run_moves_nothing_and_writes_no_log(move_script, fake, books, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--from-project", "Marketing", "--select", "all",
            "--to-project", "Finance", "--output-dir", str(tmp_path / "out"), "--dry-run"]
    assert move_script.main(args) == 0
    assert not any(method == "PUT" for method, _ in fake.requests)
    assert not (tmp_path / "out").exists()
    assert "Dry run" in capsys.readouterr().out


def test_failure_doesnt_stop_the_batch_and_is_logged(move_script, fake, books, env_file, tmp_path, capsys):
    fake.fail[("PUT", f"/workbooks/{books['Alpha']['id']}")] = 403
    args = ["--env-file", str(env_file), "--from-project", "Marketing", "--select", "all",
            "--to-project", "Finance", "--output-dir", str(tmp_path), "-y"]
    assert move_script.main(args) == 1
    assert project_of(fake, books["Gamma"]) == "p-fin"
    _, log = read_log(tmp_path)
    assert log["Alpha"]["Status"] == "Failed" and "isn't allowed" in log["Alpha"]["Detail"]


def test_locked_destination_is_called_out(move_script, fake, books, env_file, tmp_path, capsys):
    args = ["--env-file", str(env_file), "--from-project", "Marketing", "--select", "1",
            "--to-project", "Finance", "--dry-run"]
    move_script.main(args)
    assert "locked permissions" in capsys.readouterr().out


def test_interactive_project_picker_flow(move_script, fake, books, env_file, tmp_path, answers):
    # 1 = pick from a project; top level 1 Default 2 Finance 3 Marketing -> 3 (no sub-projects, selected);
    # select 2 (Beta); destination: 2 Finance -> 0 move here; confirm y
    answers(["1", "3", "2", "2", "0", "y"])
    assert move_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert project_of(fake, books["Beta"]) == "p-fin"


def test_yes_needs_a_source_and_destination(move_script, env_file):
    with pytest.raises(SystemExit):
        move_script.main(["--env-file", str(env_file), "--from-project", "Marketing", "-y"])
    with pytest.raises(SystemExit):
        move_script.main(["--env-file", str(env_file), "--from-csv", "x.csv", "-y"])


def test_not_a_move_log_is_refused_before_sign_in(move_script, fake, env_file, tmp_path, capsys):
    bad = tmp_path / "other.csv"
    bad.write_text("Workbook ID\nabc\n")
    assert move_script.main(["--env-file", str(env_file), "--undo", str(bad), "-y"]) == 1
    assert "isn't a move log" in capsys.readouterr().err
    assert not any("signin" in path for _, path in fake.requests)
