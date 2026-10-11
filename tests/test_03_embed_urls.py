"""End-to-end tests for 03-embed-urls/03_embed_urls.py."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from tcs.config import Settings
from tcs.content import view_url


def outputs(folder: Path):
    """(rows keyed by "Workbook / View", the HTML page) from the one run in ``folder``."""
    [csv_file] = folder.glob("views_acme_*.csv")
    [html_file] = folder.glob("views_acme_*.html")
    with csv_file.open(encoding="utf-8-sig", newline="") as handle:
        rows = {f'{r["Workbook"]} / {r["View"]}': r for r in csv.DictReader(handle)}
    return rows, html_file.read_text(encoding="utf-8")


def run(script, env_file, tmp_path, *extra):
    return script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path), "-y", *extra])


def test_every_view_gets_a_link_an_iframe_and_an_embed_code(embed_script, site_content, env_file, tmp_path):
    assert run(embed_script, env_file, tmp_path) == 0
    rows, _ = outputs(tmp_path)
    assert list(rows) == [
        "Plan / Plan",
        "Budget / Detail",
        "Budget / Overview",
        "Campaigns / Campaign ROI",
        "Scratch / Draft",  # Personal space comes last
    ]
    overview = rows["Budget / Overview"]
    base = f"{site_content.url}/t/acme/views/Budget/Overview"
    assert overview["View URL"] == base
    assert overview["Iframe URL"] == base + "?:embed=y&:showVizHome=n&:tabs=n&:toolbar=n"
    assert overview["Iframe Code"] == (
        f'<iframe src="{base}?:embed=y&amp;:showVizHome=n&amp;:tabs=n&amp;:toolbar=n" '
        'width="100%" height="800" frameborder="0" allowfullscreen></iframe>'
    )
    assert overview["Embed Code"] == (
        f'<script type="module" src="{site_content.url}/javascripts/api/tableau.embedding.3.latest.min.js"></script>\n'
        f'<tableau-viz src="{base}" toolbar="bottom" hide-tabs></tableau-viz>'
    )
    assert overview["Total Views"] == "12"
    assert (overview["Project Path"], overview["Owner Name"]) == ("Finance / Reporting / Monthly", "Jo Müller")
    assert rows["Scratch / Draft"]["Location"] == "Personal space"
    assert site_content.signed_out


def test_default_site_on_server_has_no_t_segment():
    settings = Settings(server_url="https://tableau.example.com", site_name="", pat_name="n", pat_secret="s")
    assert view_url(settings, {"contentUrl": "Sales/sheets/Map"}) == "https://tableau.example.com/views/Sales/Map"


def test_page_groups_by_project_most_viewed_first(embed_script, site_content, env_file, tmp_path):
    run(embed_script, env_file, tmp_path)
    _, page = outputs(tmp_path)
    finance = page.index("Finance / Reporting / Monthly")
    assert page.index(">Overview<", finance) < page.index(">Detail<", finance)  # 12 views before 3
    assert page.index("Finance / Planning") < finance < page.index(">Marketing ")
    assert "5 views · 56 total views · Every project" in page
    assert 'href="http' in page and "<script src=" not in page  # self-contained: no external scripts


def test_names_are_escaped_on_the_page(embed_script, fake, env_file, tmp_path):
    workbook = fake.add_workbook('<img src=x onerror="alert(1)">', "p-mkt")
    fake.add_view(workbook, "<b>Bold</b>", 2)
    assert run(embed_script, env_file, tmp_path) == 0
    _, page = outputs(tmp_path)
    assert "<img src=x" not in page and "<b>Bold</b>" not in page
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page


def test_include_scope_and_review(embed_script, site_content, env_file, tmp_path, capsys):
    assert run(embed_script, env_file, tmp_path, "--include", "Finance") == 0
    rows, _ = outputs(tmp_path)
    assert sorted(rows) == ["Budget / Detail", "Budget / Overview", "Plan / Plan"]
    out = " ".join(capsys.readouterr().out.split(" "))
    assert re.search(r"Views +3 in 2 workbooks", out)
    assert re.search(r"Total views +15 \(2 of the views have been opened\)", out)
    assert "Budget / Overview  Finance / Reporting / Monthly  12 views" in out


def test_usage_is_asked_for(embed_script, site_content, env_file, tmp_path):
    run(embed_script, env_file, tmp_path)
    view_calls = [path for _, path in site_content.requests if path.split("?")[0].endswith("/views")]
    assert view_calls and all("includeUsageStatistics=true" in path for path in view_calls)


def test_interactive_scope_and_confirm(embed_script, site_content, env_file, tmp_path, answers, capsys):
    # 3 = only some top-level projects; roots 1 Default, 2 Finance, 3 Marketing; then the default yes.
    answers(["3", "3", ""])
    assert embed_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path)]) == 0
    rows, _ = outputs(tmp_path)
    assert list(rows) == ["Campaigns / Campaign ROI"]
    assert "Marketing  1 view" in capsys.readouterr().out


def test_dry_run_and_decline_write_nothing(embed_script, site_content, env_file, tmp_path, answers):
    assert run(embed_script, env_file, tmp_path / "dry", "--dry-run") == 0
    assert not (tmp_path / "dry").exists()
    answers(["1", "n"])
    assert embed_script.main(["--env-file", str(env_file), "--output-dir", str(tmp_path / "no")]) == 130
    assert not (tmp_path / "no").exists()


def test_no_views_still_writes_both_files(embed_script, fake, env_file, tmp_path, capsys):
    assert run(embed_script, env_file, tmp_path) == 0
    rows, page = outputs(tmp_path)
    assert rows == {} and "No views in this scope." in page
    assert "The files will have no rows." in capsys.readouterr().out
