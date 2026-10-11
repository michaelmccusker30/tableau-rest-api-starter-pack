#!/usr/bin/env python3
"""Build a link, an iframe and an embed code for every view on a Tableau site, with view counts.

Entry 03 of the starter pack. See README.md here.

For every view (sheet or dashboard) in scope it writes:
  * the view's link, as Share > Copy Link gives it
  * an iframe URL and a ready-to-paste <iframe>, for tools that embed by URL
    (portals, wikis, Palantir and the like)
  * an Embedding API v3 snippet (<tableau-viz>), Tableau's current way to embed
  * its total view count, and its workbook, project path and owner

Two files: views_<site>_<date>.csv for spreadsheets and other scripts, and
views_<site>_<date>.html, a page that lists the views by project, most viewed
first, with a search box and copy buttons. It opens straight from disk: a start
for a landing page, or a list of an organization's high-value workbooks.

Run it with no arguments for the interactive flow:
  1. sign in; projects, workbooks and views (with usage) are read (read-only)
  2. choose the scope: every project, every project except some branches, or
     only some branches
  3. review the counts and the most-viewed views, then write the files (to
     ./output unless --output-dir says otherwise)

Any step can be answered up front with a flag, and --yes skips all prompts.
It never changes anything on the site.

Admin Insights doesn't produce embed codes, and Tableau's Share dialog gives
them one view at a time. This script does every view at once.

Safeguards:
  * Names on the page are HTML-escaped. Anyone who can publish chooses them,
    and the page runs in your browser.
  * A link is only made from an http(s) URL.
  * Views in Personal Space or in a project you can't see are kept and labelled.
  * Files are never overwritten: a second run in the same minute gets _2.
"""

from __future__ import annotations

import argparse
import html
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs import ui  # noqa: E402
from tcs.client import TableauClient, TableauError  # noqa: E402
from tcs.config import ConfigError, Settings, load_settings  # noqa: E402
from tcs.content import list_views, list_workbooks, placement, total_views, view_url  # noqa: E402
from tcs.files import clean_path_input, display_path  # noqa: E402
from tcs.projects import ProjectTree, fetch_project_tree  # noqa: E402
from tcs.reports import DEFAULT_OUTPUT_DIR, ReportError, report_path, write_csv, write_text  # noqa: E402
from tcs.scope import Scope, choose_scope  # noqa: E402

COLUMNS = [
    "View",
    "Workbook",
    "Project Path",
    "Top-Level Project",
    "Location",
    "Owner Name",
    "Owner Email",
    "Total Views",
    "View URL",
    "Iframe URL",
    "Iframe Code",
    "Embed Code",
    "Workbook URL",
    "View ID",
    "Workbook ID",
    "Project ID",
]

# URL parameters for an iframe: embedded mode, no "view home" redirect, no tabs, no toolbar.
IFRAME_PARAMETERS = "?:embed=y&:showVizHome=n&:tabs=n&:toolbar=n"
IFRAME_SIZE = 'width="100%" height="800"'
PREVIEW_ROWS = 5

EXAMPLES = """\
examples:
  Interactive:
    python 03-embed-urls/03_embed_urls.py

  Every view, no prompts:
    python 03-embed-urls/03_embed_urls.py --yes

  One branch, into a folder of your choice:
    python 03-embed-urls/03_embed_urls.py --include "Sales/EMEA" --output-dir ~/Desktop/embeds --yes

  See the counts first:
    python 03-embed-urls/03_embed_urls.py --dry-run
"""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="03_embed_urls.py",
        description="A link, an iframe and an embed code for every view, with view counts, as CSV and an HTML page.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--include",
        metavar="PATH",
        action="append",
        help='only this project and everything under it, e.g. "Finance/Reporting"; repeat for more; '
        "skips the scope prompt",
    )
    scope.add_argument(
        "--exclude",
        metavar="PATH",
        action="append",
        help="every project except this one and everything under it; repeat for more; skips the scope prompt",
    )
    parser.add_argument(
        "--output-dir",
        metavar="PATH",
        help=f"folder for the files (default: ./{DEFAULT_OUTPUT_DIR}, created if needed)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="no prompts at all; the scope is every project unless --include or --exclude is given",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the review, but write no files")
    parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
    args = parser.parse_args(argv)

    for flag in ("include", "exclude"):
        if any(not path.strip() for path in getattr(args, flag) or []):
            parser.error(f"--{flag} can't be empty")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except (ui.Cancelled, KeyboardInterrupt):
        print()
        ui.warn("Cancelled. No files were written.")
        return 130
    except (ConfigError, TableauError, ReportError, LookupError, ValueError) as exc:
        ui.error(str(exc))
        return 1


def run(args: argparse.Namespace) -> int:
    settings = load_settings(args.env_file)
    ui.heading("Links, iframes and embed codes for every view")
    ui.detail(f"  Site: {settings.site_label} on {settings.server_url}")

    with TableauClient(settings) as client:
        user = client.current_user_name()
        ui.success(
            f"Signed in to {settings.site_label}"
            + (f" as {user}" if user else "")
            + f" (REST API {client.api_version})"
        )

        # 1. Read the site. Nothing here changes anything.
        tree = fetch_project_tree(client)
        workbooks = {wb["id"]: wb for wb in list_workbooks(client)}
        views = list_views(client)
    ui.success(f"Read {_n(len(tree), 'project')}, {_n(len(workbooks), 'workbook')} and {_n(len(views), 'view')}")

    # 2. Scope. Signed out by now: the rest is all local.
    scope = choose_scope(
        tree,
        include=args.include,
        exclude=args.exclude,
        ask=not args.yes,
        counts=Counter((v.get("project") or {}).get("id") for v in views),
        noun="view",
    )
    rows = view_rows(settings, tree, views, workbooks, scope)

    # 3. Where the files go. Shown in the review, so no prompt for it.
    folder = clean_path_input(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    now = datetime.now()
    csv_file = report_path(folder, "views", settings.site_label, now)
    html_file = report_path(folder, "views", settings.site_label, now, suffix=".html")

    # 4. Review, then write.
    _print_review(settings, user, scope, rows, csv_file, html_file)
    if args.dry_run:
        ui.warn("Dry run: no files were written.")
        return 0
    if not args.yes and not ui.confirm("Write these 2 files?", default=True):
        raise ui.Cancelled()

    write_csv(csv_file, rows, COLUMNS)
    write_text(html_file, render_html(settings, scope, rows, now))
    ui.success(f"{_n(len(rows), 'view')}: {display_path(csv_file)}")
    ui.success(f"Page: {display_path(html_file)}")
    return 0


def view_rows(
    settings: Settings,
    tree: ProjectTree,
    views: List[Dict[str, Any]],
    workbooks: Dict[str, Dict[str, Any]],
    scope: Scope,
) -> List[Dict[str, Any]]:
    """One row per view in scope, sorted by project path, workbook, then view."""
    rows = []
    for view in views:
        place = placement(tree, view)
        if not scope.covers(place.project_id, place.visible):
            continue
        workbook = workbooks.get((view.get("workbook") or {}).get("id", ""), {})
        owner = workbook.get("owner") or {}
        link = view_url(settings, view)
        iframe_url = link + IFRAME_PARAMETERS
        rows.append(
            {
                "View": view.get("name", ""),
                "Workbook": workbook.get("name", ""),
                "Project Path": place.path,
                "Top-Level Project": place.top_level,
                "Location": place.location,
                "Owner Name": owner.get("fullName") or owner.get("name", ""),
                "Owner Email": owner.get("email", ""),
                "Total Views": total_views(view),
                "View URL": link,
                "Iframe URL": iframe_url,
                "Iframe Code": iframe_code(iframe_url),
                "Embed Code": embed_code(settings, link),
                "Workbook URL": workbook.get("webpageUrl", ""),
                "View ID": view.get("id", ""),
                "Workbook ID": workbook.get("id", ""),
                "Project ID": place.project_id,
            }
        )
    rows.sort(
        key=lambda r: (
            r["Location"] != "Project",
            r["Project Path"].casefold(),
            r["Workbook"].casefold(),
            r["View"].casefold(),
        )
    )
    return rows


def iframe_code(iframe_url: str) -> str:
    return f'<iframe src="{html.escape(iframe_url)}" {IFRAME_SIZE} frameborder="0" allowfullscreen></iframe>'


def embed_code(settings: Settings, link: str) -> str:
    """Embedding API v3: the library from your own server, then the viz. Attributes as in Tableau's docs."""
    library = f"{settings.server_url}/javascripts/api/tableau.embedding.3.latest.min.js"
    return (
        f'<script type="module" src="{html.escape(library)}"></script>\n'
        f'<tableau-viz src="{html.escape(link)}" toolbar="bottom" hide-tabs></tableau-viz>'
    )


def _print_review(
    settings: Settings,
    user: Optional[str],
    scope: Scope,
    rows: List[Dict[str, Any]],
    csv_file: Path,
    html_file: Path,
) -> None:
    outside = Counter(r["Location"] for r in rows if r["Location"] != "Project")
    detail = ", ".join(f"{count} in {where.lower()}" for where, count in sorted(outside.items()))
    workbooks = len({r["Workbook ID"] for r in rows})
    viewed = sum(1 for r in rows if r["Total Views"])
    ui.heading("Review")
    ui.table(
        [
            ("Site", f"{settings.site_label} on {settings.server_url}"),
            ("Signed in as", user or "(name not available)"),
            ("Scope", scope.label),
            ("Views", f"{len(rows)} in {_n(workbooks, 'workbook')}" + (f" ({detail})" if detail else "")),
            ("Total views", f"{sum(r['Total Views'] for r in rows):,} ({viewed} of the views have been opened)"),
            ("Files", f"{display_path(csv_file)}\n{display_path(html_file)}"),
        ]
    )
    if not rows:
        ui.warn("No views in this scope. The files will have no rows.")
        return
    top = sorted(rows, key=lambda r: (-r["Total Views"], r["Workbook"].casefold(), r["View"].casefold()))
    print()
    ui.detail("  Most viewed:")
    for row in top[:PREVIEW_ROWS]:
        count = ui.style(_n(row["Total Views"], "view"), "dim")
        print(f"    {row['Workbook']} / {row['View']}  {ui.style(row['Project Path'], 'cyan')}  {count}")


# ------------------------------------------------------------------ the page

def render_html(settings: Settings, scope: Scope, rows: List[Dict[str, Any]], now: datetime) -> str:
    """A self-contained page: no external files, so it opens straight from disk.

    Views are grouped by project path; within a project, most viewed first.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:  # rows arrive sorted by path, so groups come out in order
        groups.setdefault(row["Project Path"], []).append(row)

    sections = []
    for path, members in groups.items():
        members = sorted(members, key=lambda r: (-r["Total Views"], r["Workbook"].casefold(), r["View"].casefold()))
        items = "\n".join(_html_row(r) for r in members)
        sections.append(
            f'<section class="group"><h2>{_e(path)} <span class="count">{_n(len(members), "view")}</span></h2>\n'
            "<table><colgroup><col class=\"c-view\"><col class=\"c-wb\"><col class=\"c-owner\">"
            "<col class=\"c-num\"><col class=\"c-embed\"></colgroup>"
            '<thead><tr><th>View</th><th>Workbook</th><th>Owner</th><th class="num">Views</th>'
            f"<th>Embed</th></tr></thead>\n<tbody>\n{items}\n</tbody></table></section>"
        )
    body = "\n".join(sections) or '<p class="empty">No views in this scope.</p>'
    title = f"Views on {settings.site_label}"
    total = sum(r["Total Views"] for r in rows)
    return _PAGE.format(
        title=_e(title),
        summary=_e(
            f"{_n(len(rows), 'view')} · {total:,} total views · {scope.label} · "
            f"{settings.server_url} · generated {now:%Y-%m-%d %H:%M}"
        ),
        body=body,
    )


def _html_row(row: Dict[str, Any]) -> str:
    return (
        '<tr class="item">'
        f'<td>{_link(row["View URL"], row["View"])}</td>'
        f'<td>{_link(row["Workbook URL"], row["Workbook"])}</td>'
        f'<td>{_e(row["Owner Name"])}</td>'
        f'<td class="num">{row["Total Views"]:,}</td>'
        "<td><details><summary>Show</summary>"
        f'{_snippet("Iframe URL", row["Iframe URL"])}'
        f'{_snippet("Iframe", row["Iframe Code"])}'
        f'{_snippet("Embed code (Embedding API v3)", row["Embed Code"])}'
        "</details></td></tr>"
    )


def _snippet(label: str, text: str) -> str:
    return (
        f'<div class="snippet"><span class="label">{_e(label)}</span>'
        f'<button type="button" class="copy">Copy</button>'
        f'<pre><code>{_e(text)}</code></pre></div>'
    )


def _link(url: str, text: str) -> str:
    if url.startswith(("https://", "http://")):
        return f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(text)}</a>'
    return _e(text)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg: #ffffff; --head: #1e293b; --head-text: #f1f5f9; --text: #0f172a; --sub: #64748b;
          --border: #e2e8f0; --stripe: #f1f5f9; --accent: #3b82f6; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  header {{ background: var(--head); color: var(--head-text); padding: 20px 24px; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; }}
  header p {{ margin: 0; color: #cbd5e1; font-size: 13px; }}
  main {{ padding: 16px 24px 40px; max-width: 1200px; }}
  #search {{ width: 100%; max-width: 420px; padding: 8px 10px; border: 1px solid var(--border);
            border-radius: 6px; font: inherit; margin: 8px 0 16px; }}
  h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
       color: var(--sub); margin: 24px 0 6px; }}
  .count {{ font-weight: 400; letter-spacing: 0; text-transform: none; }}
  table {{ width: 100%; table-layout: fixed; border-collapse: collapse; border: 1px solid var(--border); }}
  col.c-view {{ width: 28%; }} col.c-wb {{ width: 24%; }} col.c-owner {{ width: 16%; }}
  col.c-num {{ width: 8%; }} col.c-embed {{ width: 24%; }}
  td {{ overflow-wrap: anywhere; }}
  th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--sub); font-weight: 600; }}
  tbody tr:nth-child(even) {{ background: var(--stripe); }}
  .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  summary {{ cursor: pointer; color: var(--sub); font-size: 12px; }}
  .snippet {{ margin: 6px 0 0; }}
  .snippet .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--sub);
                    font-weight: 600; margin-right: 6px; }}
  pre {{ white-space: pre-wrap; word-break: break-all; background: var(--bg); border: 1px solid var(--border);
        border-radius: 6px; padding: 6px 8px; margin: 4px 0 0; font-size: 11px; }}
  button.copy {{ font: inherit; font-size: 12px; padding: 2px 8px; border: 1px solid var(--border);
                border-radius: 4px; background: var(--bg); cursor: pointer; }}
  .empty {{ color: var(--sub); }}
</style>
</head>
<body>
<header><h1>{title}</h1><p>{summary}</p></header>
<main>
<input id="search" type="search" placeholder="Filter by view, workbook, owner or project" aria-label="Filter">
{body}
</main>
<script>
  // Filter rows as you type; hide projects with nothing left.
  document.getElementById("search").addEventListener("input", function (event) {{
    var q = event.target.value.toLowerCase();
    document.querySelectorAll("section.group").forEach(function (group) {{
      var shown = 0;
      var inGroup = group.querySelector("h2").textContent.toLowerCase().indexOf(q) !== -1;
      group.querySelectorAll("tr.item").forEach(function (row) {{
        var hit = inGroup || row.textContent.toLowerCase().indexOf(q) !== -1;
        row.style.display = hit ? "" : "none";
        if (hit) shown++;
      }});
      group.style.display = shown ? "" : "none";
    }});
  }});
  // Copy a snippet. Falls back to selecting it where the clipboard isn't allowed.
  document.querySelectorAll("button.copy").forEach(function (button) {{
    button.addEventListener("click", function () {{
      var code = button.parentNode.querySelector("code");
      var select = function () {{
        var range = document.createRange();
        range.selectNodeContents(code);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = "Selected: press Ctrl/Cmd+C";
      }};
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(code.textContent).then(function () {{
          button.textContent = "Copied";
          setTimeout(function () {{ button.textContent = "Copy"; }}, 1500);
        }}, select);
      }} else {{
        select();
      }}
    }});
  }});
</script>
</body>
</html>
"""


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


if __name__ == "__main__":
    sys.exit(main())
