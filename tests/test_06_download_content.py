"""End-to-end tests for 06-download-content/06_download_content.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.fixture
def stock(fake):
    """Two workbooks and two data sources, with download files. One data source comes back named like a workbook."""
    budget = fake.add_workbook("Budget", "p-fin-rep-mon")
    plan = fake.add_workbook("Plan: 2026?", "p-fin-plan")  # characters a file name can't hold
    ledger = fake.add_datasource("Ledger", "p-fin", hasExtracts=True)
    live = fake.add_datasource("Live Sales", "p-mkt")
    fake.files[budget["id"]] = ("Budget.twbx", b"PK budget")
    fake.files[plan["id"]] = ("Plan.twbx", b"PK plan")
    fake.files[ledger["id"]] = ("Ledger.tdsx", b"PK ledger")
    fake.files[live["id"]] = ("Live+Sales.twb", b"<datasource/>")  # the API's workbook-named data source
    return {"budget": budget, "plan": plan, "ledger": ledger, "live": live}


def run_folder(tmp_path: Path) -> Path:
    [folder] = [p for p in tmp_path.iterdir() if p.name.startswith("download_acme_")]
    return folder


def manifest(folder: Path):
    with (folder / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
        return {r["Name"]: r for r in csv.DictReader(handle)}


def test_everything_mirrors_the_project_tree(download_script, fake, stock, env_file, tmp_path):
    assert download_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    folder = run_folder(tmp_path)
    assert (folder / "Finance" / "Reporting" / "Monthly" / "Budget.twbx").read_bytes() == b"PK budget"
    assert (folder / "Finance" / "Planning" / "Plan_ 2026_.twbx").exists()
    assert (folder / "Finance" / "Ledger.tdsx").read_bytes() == b"PK ledger"
    assert (folder / "Marketing" / "Live Sales.tds").exists()  # .twb corrected to .tds
    rows = manifest(folder)
    assert rows["Budget"]["Status"] == "Downloaded" and rows["Budget"]["ID"] == stock["budget"]["id"]
    assert rows["Live Sales"]["File"] == str(Path("Marketing") / "Live Sales.tds")
    assert not list(folder.rglob("*.part"))
    assert fake.signed_out


def test_type_scope_select_and_no_extract(download_script, fake, stock, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--type", "datasources",
            "--include", "Finance", "--no-extract", "-y"]
    assert download_script.main(args) == 0
    rows = manifest(run_folder(tmp_path))
    assert list(rows) == ["Ledger"]
    assert rows["Ledger"]["Extract Included"] == "No"
    calls = [path for _, path in fake.requests if "/content" in path]
    assert calls == [f"/api/3.29/sites/site-1/datasources/{stock['ledger']['id']}/content?includeExtract=false"]


def test_select_picks_by_number(download_script, fake, stock, env_file, tmp_path):
    # Sorted by path, then type, then name: Finance (Ledger), Finance/Planning (Plan), Finance/.../Monthly (Budget), Marketing (Live Sales)
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--select", "1,4", "-y"]
    assert download_script.main(args) == 0
    assert sorted(manifest(run_folder(tmp_path))) == ["Ledger", "Live Sales"]


def test_a_failure_is_recorded_and_the_rest_carry_on(download_script, fake, stock, env_file, tmp_path):
    fake.fail[("GET", f"/workbooks/{stock['budget']['id']}/content")] = 403
    assert download_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 1
    rows = manifest(run_folder(tmp_path))
    assert rows["Budget"]["Status"] == "Failed" and "403" in rows["Budget"]["Detail"]
    assert rows["Ledger"]["Status"] == "Downloaded"


def test_interactive_flow(download_script, fake, stock, env_file, tmp_path, answers):
    # What: 1 workbooks; scope: 1 every project; pick: a; confirm: default yes
    answers(["1", "1", "a", ""])
    assert download_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert sorted(manifest(run_folder(tmp_path))) == ["Budget", "Plan: 2026?"]


def test_dry_run_downloads_nothing(download_script, fake, stock, env_file, tmp_path, capsys):
    assert download_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "--dry-run", "-y"]) == 0
    assert not list(tmp_path.glob("download_*"))
    assert not any("/content" in path for _, path in fake.requests)
    out = capsys.readouterr().out
    assert "2 workbooks, 2 data sources" in out and "1 data source(s) have one" in out
