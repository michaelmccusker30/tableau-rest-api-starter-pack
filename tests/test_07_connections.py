"""End-to-end tests for 07-connections/07_connections.py."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest


@pytest.fixture
def wired(fake):
    ledger = fake.add_datasource("Ledger", "p-fin")
    fake.add_connection(ledger, "sqlserver", "sql01.example.com", "svc_reporting", serverPort="1433", authenticationType="auth-user-pass")
    crm = fake.add_datasource("CRM", "p-mkt")
    fake.add_connection(crm, "postgres", "pg.example.com", "SVC_Reporting")
    fake.add_connection(crm, "postgres", "pg2.example.com", "crm_reader")
    extract = fake.add_datasource("Extract Only", "p-default", kind="hyper")
    fake.add_connection(extract, "hyper", "", "")
    budget = fake.add_workbook("Budget", "p-fin")
    fake.connections[budget["id"]] = [
        {"id": "c-wb", "type": "sqlproxy", "serverAddress": "localhost", "userName": "", "embedPassword": False,
         "datasource": {"id": ledger["id"], "name": "Ledger"}}
    ]
    return {"ledger": ledger, "crm": crm, "budget": budget}


def rows(folder: Path):
    [path] = folder.glob("connections_acme_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_every_connection_of_both_kinds(conn_script, fake, wired, env_file, tmp_path):
    assert conn_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y"]) == 0
    found = rows(tmp_path)
    assert len(found) == 5
    ledger = next(r for r in found if r["Name"] == "Ledger")
    assert (ledger["Server"], ledger["Port"], ledger["Username"], ledger["Password Embedded"]) == (
        "sql01.example.com", "1433", "svc_reporting", "Yes")
    assert (ledger["Project Path"], ledger["Authentication"], ledger["Data Source Name"]) == ("Finance", "auth-user-pass", "Ledger")
    budget = next(r for r in found if r["Content Type"] == "Workbook")
    assert (budget["Connection Type"], budget["Data Source Name"]) == ("sqlproxy", "Ledger")
    assert fake.signed_out


def test_username_filter_ignores_case(conn_script, fake, wired, env_file, tmp_path):
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--username", "svc_reporting", "-y"]
    assert conn_script.main(args) == 0
    assert sorted((r["Name"], r["Server"]) for r in rows(tmp_path)) == [
        ("CRM", "pg.example.com"), ("Ledger", "sql01.example.com")]


def test_review_sums_up_types_and_usernames(conn_script, fake, wired, env_file, tmp_path, capsys):
    conn_script.main(["--env-file", str(env_file), "--type", "datasources", "--dry-run", "-y"])
    out = capsys.readouterr().out
    assert re.search(r"Connections +4 \(3 with an embedded password\)", out)
    assert "postgres 2" in out and "svc_reporting (1)" in out and "crm_reader (1)" in out
    assert not any("/workbooks/" in p and "/connections" in p for _, p in fake.requests)


def test_scope_and_a_failed_read(conn_script, fake, wired, env_file, tmp_path, capsys):
    fake.fail[("GET", f"/datasources/{wired['crm']['id']}/connections")] = 403
    args = ["--env-file", str(env_file), "--output-dir", str(tmp_path), "--type", "datasources",
            "--exclude", "Default", "-y"]
    assert conn_script.main(args) == 1  # written, but partial
    assert [r["Name"] for r in rows(tmp_path)] == ["Ledger"]
    assert "Couldn't read Data source CRM" in capsys.readouterr().out


def test_interactive(conn_script, fake, wired, env_file, tmp_path, answers):
    # Whose: 2 workbooks; scope: 1 every project; write: default yes
    answers(["2", "1", ""])
    assert conn_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    assert [r["Name"] for r in rows(tmp_path)] == ["Budget"]
