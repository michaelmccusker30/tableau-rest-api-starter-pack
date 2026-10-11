# 03 · Embed URLs: a link, an iframe and an embed code for every view

For every view (sheet or dashboard) on your site, in one run:

- **its link**, the same one Share > Copy Link gives you
- **an iframe URL and a ready-to-paste `<iframe>`**, for tools that embed by URL: intranets, wikis, portals, and platforms such as Palantir
- **an Embedding API v3 snippet** (`<tableau-viz>`), Tableau's current way to embed in a web page
- **its total view count**, plus its workbook, project path and owner

It writes them as a CSV, and as an HTML page that lists the views by project, most viewed first, with a filter box and copy buttons. The page opens straight from disk. Use it as the start of a landing page, or a list of your organization's high-value workbooks.

It never changes anything on the site.

```bash
python 03-embed-urls/03_embed_urls.py
```


## Why a script for this

Tableau's Share dialog gives you a link or an embed code one view at a time. Admin Insights doesn't produce embed codes at all. When you're building a portal page, wiring up a tool that iframes dashboards, or deciding which dashboards deserve a place on a landing page, you want all of them at once, with view counts to rank them.

## The flow

1. **Sign in and read.** Projects, workbooks and views (with usage), every page of each.
2. **Choose the scope.**
   - Every project.
   - Every project except some top-level projects.
   - Only some top-level projects, with everything under them.

   Each top-level project shows its view count.
3. **Review.** You see the scope, the number of views and workbooks, the total view count, where the files will go (`./output` unless you pass `--output-dir`) and the five most-viewed views. Then confirm to write the files.

```text
Review
  Site          acme on https://10ax.online.tableau.com
  Signed in as  Jo Admin
  Scope         Every project
  Views         748 in 151 workbooks
  Total views   12,063 (402 of the views have been opened)
  Files         output/views_acme_2026-10-10_1432.csv
                output/views_acme_2026-10-10_1432.html

  Most viewed:
    Sales Overview / Summary  Sales  1,204 views
    ...
? Write these 2 files? (Y/n):
```

| Flag | Effect |
| --- | --- |
| `--include "A/B"` | Only this project and everything under it. Any level, case-insensitive. Repeat for more |
| `--exclude "A/B"` | Every project except this one and everything under it. Repeat for more |
| `--output-dir PATH` | Folder for the files (default: `./output`, created if needed) |
| `-y`, `--yes` | No prompts. The scope is every project unless `--include` or `--exclude` is given |
| `--dry-run` | Reads the site and shows the review, but writes no files |
| `--env-file PATH` | Use a different settings file |

`output/` is git-ignored in this repo.

## The three ways to embed

For a view at `https://10ax.online.tableau.com/t/acme/views/Sales/Summary`:

**Iframe URL**: the link plus URL parameters for embedding: embedded mode, no "view home" redirect, no tabs, no toolbar. Paste it into any tool that asks for a URL to frame.

```text
https://10ax.online.tableau.com/t/acme/views/Sales/Summary?:embed=y&:showVizHome=n&:tabs=n&:toolbar=n
```

**Iframe**: the same URL in an `<iframe>`, full width and 800 pixels tall. Change the size to suit the page.

```html
<iframe src="https://10ax.online.tableau.com/t/acme/views/Sales/Summary?:embed=y&amp;:showVizHome=n&amp;:tabs=n&amp;:toolbar=n" width="100%" height="800" frameborder="0" allowfullscreen></iframe>
```

**Embed code (Embedding API v3)**: the library, loaded from your own server, then the viz. This is what Tableau's own Share dialog gives you today, and the route to filtering, events and resizing from JavaScript. The attributes follow Tableau's documentation (`toolbar="bottom"`, `hide-tabs`).

```html
<script type="module" src="https://10ax.online.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js"></script>
<tableau-viz src="https://10ax.online.tableau.com/t/acme/views/Sales/Summary" toolbar="bottom" hide-tabs></tableau-viz>
```

Whichever you use, people still need permission to see the view. An embedded view asks them to sign in to Tableau unless your site uses single sign-on or Connected Apps. Some browsers block the sign-in inside a frame on another domain; that's a site and browser setting, not something the URL can change.

## The CSV

`views_<site>_<date>.csv`, sorted by project path, workbook, then view:

| Column | Notes |
| --- | --- |
| View, Workbook | |
| Project Path, Top-Level Project, Location | As in [entry 02](../02-content-inventory/README.md#the-files): `Personal space` and `Project not visible to you` are kept and labelled |
| Owner Name, Owner Email | The workbook's owner |
| Total Views | All-time view count, as Tableau records it |
| View URL | The link |
| Iframe URL, Iframe Code | See above |
| Embed Code | Embedding API v3, two lines in one cell |
| Workbook URL | The workbook's page on Tableau |
| View ID, Workbook ID, Project ID | For joining to other reports, such as entry 02's |

## The page

`views_<site>_<date>.html`: one table per project, in path order. Within a project, the views are sorted by view count, highest first. Each row links to the view and its workbook. **Show** opens the three snippets, each with a **Copy** button. The filter box matches view, workbook, owner and project.

The page is a single file with no external scripts, fonts or images, so it can be opened from disk, emailed, or used as the starting point for a portal page. Names are HTML-escaped, because anyone who can publish chooses them and the page runs in your browser. Where a browser doesn't allow copying from a file opened from disk, **Copy** selects the text for Ctrl/Cmd+C instead.

## REST calls it makes

| Step | Call |
| --- | --- |
| Find the API version | `GET /api/{v}/serverinfo` |
| Sign in / out | `POST /api/{v}/auth/signin`, `POST /api/{v}/auth/signout` |
| Load projects | `GET /sites/{site-id}/projects` (all pages) |
| Load workbooks, with owners | `GET /sites/{site-id}/workbooks?fields=_default_,owner.name,owner.fullName,owner.email,...` (all pages) |
| Load views, with view counts | `GET /sites/{site-id}/views?includeUsageStatistics=true` (all pages) |

**Usage statistics.** Query Views for Site with `includeUsageStatistics=true` adds `usage.totalViewCount` to every view, 1,000 views per call. That's the cheap route to view counts. Query Workbooks can't return usage at all.

**Building the link.** A view's `contentUrl` is `Sales/sheets/Summary`. The link is `{server}/t/{site}/views/Sales/Summary`, without `sheets`. On Tableau Server's Default site there's no `/t/{site}` part.
