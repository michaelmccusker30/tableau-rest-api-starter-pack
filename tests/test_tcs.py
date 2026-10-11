"""Unit tests for the shared tcs modules."""

from __future__ import annotations

import pytest

from tcs.config import ConfigError, load_settings, normalize_server_url
from tcs.files import clean_path_input, find_workbook_files, human_size
from tcs.projects import Project, ProjectTree, resolve_project
from tcs.publishing import build_multipart, describe_existing
from tcs.reports import ReportError, report_path, write_csv
from tcs.ui import parse_selection


def test_server_url_from_pasted_browser_url():
    assert normalize_server_url("https://10ax.online.tableau.com/#/site/acme/home") == (
        "https://10ax.online.tableau.com",
        "acme",
    )
    assert normalize_server_url("tableau.example.com/") == ("https://tableau.example.com", None)


def test_placeholder_values_are_rejected(tmp_path):
    env = tmp_path / ".env"
    env.write_text((__import__("pathlib").Path(__file__).parent.parent / ".env.example").read_text())
    with pytest.raises(ConfigError, match="placeholder"):
        load_settings(env)


def test_cloud_requires_site_name(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "TABLEAU_SERVER_URL=https://10ax.online.tableau.com\nTABLEAU_PAT_NAME=a\nTABLEAU_PAT_SECRET=b\n"
    )
    with pytest.raises(ConfigError, match="TABLEAU_SITE_NAME is required"):
        load_settings(env)


def test_server_default_site_can_be_blank(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "TABLEAU_SERVER_URL=https://tableau.example.com\nTABLEAU_SITE_NAME=\nTABLEAU_PAT_NAME=a\nTABLEAU_PAT_SECRET=b\n"
    )
    settings = load_settings(env)
    assert settings.site_name == "" and settings.site_label == "Default"
    assert "pat_secret" not in repr(settings)  # secret kept out of logs and tracebacks


def test_project_tree_orphans_become_top_level():
    tree = ProjectTree(
        [
            Project("a", "Alpha"),
            Project("b", "beta", parent_id="a"),
            Project("c", "Child of hidden", parent_id="not-visible"),
        ]
    )
    assert [p.name for p in tree.roots()] == ["Alpha", "Child of hidden"]
    assert tree.path("b") == "Alpha / beta"
    assert tree.find_by_path(" alpha / BETA ").id == "b"


def test_find_by_path_on_leaf_explains():
    tree = ProjectTree([Project("a", "Alpha")])
    with pytest.raises(LookupError, match="It has no sub-projects"):
        tree.find_by_path("Alpha/Nope")


def test_clean_path_input_handles_quotes_and_escapes(tmp_path):
    assert clean_path_input('"/tmp/My File.twbx"').name == "My File.twbx"
    assert clean_path_input("/tmp/My\\ File.twbx").name == "My File.twbx"


def test_find_workbook_files_skips_hidden_temp_and_other_types(workbook_folder):
    found = [str(p.relative_to(workbook_folder)) for p in find_workbook_files(workbook_folder)]
    assert found == ["Finance/Budget.twbx", "Finance/Q3/Forecast.twbx", "Sales.twbx"]
    both = [p.name for p in find_workbook_files(workbook_folder, (".twbx", ".twb"))]
    assert "Legacy.twb" in both


@pytest.mark.parametrize(
    "text, count, expected",
    [
        ("3", 5, [2]),
        ("1,4", 5, [0, 3]),
        ("2-4", 5, [1, 2, 3]),
        ("1-2 5", 5, [0, 1, 4]),
        (" 1 , 3 ", 5, [0, 2]),
        ("4-2", 5, [1, 2, 3]),
        ("1, 1-2", 3, [0, 1]),
        ("a", 3, [0, 1, 2]),
        ("ALL", 2, [0, 1]),
    ],
)
def test_parse_selection(text, count, expected):
    assert parse_selection(text, count) == expected


@pytest.mark.parametrize("text", ["0", "6", "x", "1-9", ",", "2-", "-1"])
def test_parse_selection_rejects(text):
    with pytest.raises(ValueError):
        parse_selection(text, 5)


def test_resolve_project_prefers_id_then_path():
    tree = ProjectTree([Project("a", "Alpha"), Project("b", "Beta", parent_id="a")])
    assert resolve_project(tree, project_id="b").id == "b"
    assert resolve_project(tree, path="alpha/beta").id == "b"


def test_describe_existing():
    wb = {"updatedAt": "2026-09-12T18:20:11Z", "owner": {"name": "Jane"}}
    assert describe_existing(wb) == " (last updated 2026-09-12, owner Jane)"
    assert describe_existing({}) == ""
    assert describe_existing(None) == ""


def test_human_size():
    assert human_size(512) == "512 bytes"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def test_multipart_shape():
    body, content_type = build_multipart([("request_payload", None, "text/xml", b"<x/>")])
    boundary = content_type.split("boundary=")[1]
    assert content_type.startswith("multipart/mixed")
    assert body.startswith(f"--{boundary}\r\nContent-Disposition: form-data; name=\"request_payload\"".encode())
    assert body.endswith(f"--{boundary}--\r\n".encode())


def _tree() -> ProjectTree:
    return ProjectTree(
        [
            Project("a", "Alpha"),
            Project("a1", "One", "a"),
            Project("a1x", "Deep", "a1"),
            Project("a2", "Two", "a"),
            Project("b", "beta"),
        ]
    )


def test_project_tree_subtree_depth_and_walk():
    tree = _tree()
    assert tree.subtree_ids("a") == {"a", "a1", "a1x", "a2"}
    assert tree.subtree_ids("a2") == {"a2"}
    assert (tree.depth("a"), tree.depth("a1x")) == (1, 3)
    assert [p.id for p in tree.walk()] == ["a", "a1", "a1x", "a2", "b"]


def test_report_path_never_reuses_a_name(tmp_path):
    from datetime import datetime

    now = datetime(2026, 10, 10, 14, 32)
    first = report_path(tmp_path, "workbooks", "my site/1", now)
    assert first.name == "workbooks_my-site-1_2026-10-10_1432.csv"
    first.write_text("")
    second = report_path(tmp_path, "workbooks", "my site/1", now)
    assert second.name == "workbooks_my-site-1_2026-10-10_1432_2.csv"


def test_write_csv_bom_and_formula_guard(tmp_path):
    path = tmp_path / "sub" / "r.csv"
    write_csv(path, [{"Name": "=1+1", "Count": 3, "Note": "Zoë"}], ["Name", "Count", "Note"])
    text = path.read_text(encoding="utf-8")
    assert text.startswith("\ufeffName,Count,Note")
    assert "'=1+1,3,Zoë" in text


def test_write_csv_permission_error_mentions_excel(tmp_path, monkeypatch):
    from pathlib import Path

    def locked(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", locked)
    with pytest.raises(ReportError, match="open in Excel"):
        write_csv(tmp_path / "r.csv", [], ["A"])


def test_scope_covers_items_outside_the_tree_only_when_not_including():
    from tcs.scope import Scope, branches

    tree = _tree()
    everything = Scope("Every project")
    only_alpha = branches(tree, ["a"], include=True)
    not_alpha = branches(tree, ["a"], include=False)
    assert everything.covers("anything", visible=False)
    assert only_alpha.covers("a1x") and not only_alpha.covers("b") and not only_alpha.covers("x", visible=False)
    assert not_alpha.covers("b") and not not_alpha.covers("a2") and not_alpha.covers("x", visible=False)
    assert (only_alpha.label, not_alpha.label) == ("Only: Alpha", "Every project except: Alpha")


def test_report_path_suffix(tmp_path):
    from datetime import datetime

    path = report_path(tmp_path, "views", "acme", datetime(2026, 10, 10, 9, 5), suffix=".html")
    assert path.name == "views_acme_2026-10-10_0905.html"


def test_read_csv_handles_excel_encodings_and_spaces(tmp_path):
    from tcs.reports import find_column, read_csv

    path = tmp_path / "excel.csv"
    path.write_bytes(" Workbook ID ,Name\nabc\xa0,Caf\xe9\n,\n".encode("cp1252"))
    columns, rows = read_csv(path)
    assert columns == ["Workbook ID", "Name"]
    assert rows == [{"Workbook ID": "abc", "Name": "Café"}]  # blank row dropped, NBSP trimmed
    assert find_column(columns, ["workbook_id"]) == "Workbook ID"
    assert find_column(columns, ["luid"]) is None


def test_read_csv_errors(tmp_path):
    from tcs.reports import read_csv

    with pytest.raises(ReportError, match="not found"):
        read_csv(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("\n\n")
    with pytest.raises(ReportError, match="empty"):
        read_csv(empty)


def test_safe_name():
    from tcs.files import safe_name

    assert safe_name('Plan: 2026? "Final"/v2') == "Plan_ 2026_ _Final_v2"
    assert safe_name("  ..  ") == "unnamed"
    assert len(safe_name("x" * 300)) == 100


def test_run_batch_carries_on_and_explains(capsys):
    from tcs.batch import run_batch, summary
    from tcs.client import TableauError

    def act(n):
        if n == 2:
            raise TableauError("nope", status=409)
        return f"did {n}"

    outcomes = run_batch([1, 2, 3], act, str, explain=lambda n, exc: "clash")
    assert [o.ok for o in outcomes] == [True, False, True]
    assert outcomes[1].detail == "clash" and outcomes[0].detail == "did 1"
    assert summary(outcomes, "Done") == 1
    assert summary(outcomes[:1], "Done") == 0


def test_rate_budget_backs_off_and_recovers(clock, tmp_path):
    from tcs.pacing import RateBudget, retry_after_seconds

    budget = RateBudget(120, tmp_path / "pace.json")
    budget.wait_for_slot()
    budget.wait_for_slot()
    assert clock.sleeps == [30.0]  # 3600 / 120
    budget.penalise(60)
    assert budget.limit == 90
    for _ in range(25):
        budget.reward()
    assert budget.limit == 96  # +6 (120 // 20)
    for _ in range(20):
        budget.penalise(1)
    assert budget.limit == 12  # the floor
    assert retry_after_seconds("Please retry after 146 second(s).") == 151
    assert retry_after_seconds("retry after 2 minutes") == 125
    assert retry_after_seconds("nothing useful") == 120
    assert RateBudget(120, tmp_path / "pace.json").calls  # remembered


def test_permission_diffs_and_xml():
    from tcs.permissions import TEMPLATES, Rule, _xml, granted, plan, revoked

    current = {Rule("group", "g1", "Read", "Allow"), Rule("group", "g1", "ExportData", "Deny"), Rule("user", "u1", "Read", "Allow")}
    want = granted(current, [("group", "g1")], {"ExportData": "Allow", "Filter": "Allow"})
    deletes, adds = plan(current, want)
    assert deletes == [Rule("group", "g1", "ExportData", "Deny")]
    assert adds == [Rule("group", "g1", "ExportData", "Allow"), Rule("group", "g1", "Filter", "Allow")]
    assert revoked(current, {"g1"}) == {Rule("user", "u1", "Read", "Allow")}
    assert revoked(current, {"g1"}, ["Read"]) == {Rule("group", "g1", "ExportData", "Deny"), Rule("user", "u1", "Read", "Allow")}
    assert "ShareView" not in TEMPLATES["view"] and "ShareView" in TEMPLATES["explore"]
    assert set(TEMPLATES["view"]) < set(TEMPLATES["explore"]) < set(TEMPLATES["publish"]) < set(TEMPLATES["administer"])
    body = _xml([Rule("user", 'u"1', "Read", "Allow")]).decode()
    assert body == ('<tsRequest><permissions><granteeCapabilities><user id=\'u"1\'/><capabilities>'
                    '<capability name="Read" mode="Allow"/></capabilities></granteeCapabilities></permissions></tsRequest>')
