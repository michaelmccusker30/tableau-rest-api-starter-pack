# 02 · Content inventory: every workbook, its full project path and its owner

A read-only report of what's on your site, as two CSV files:

- **Workbooks:** one row per workbook, with its full project path (`Finance / Reporting / Monthly`), the top-level project, the owner's name, email, site role and last sign-in, total views, created and updated dates, size, tags and a link.
- **Projects:** the project tree, with each project's depth, owner, how many workbooks it holds (directly, and including its sub-projects) and its content permissions.

The owner's site role and last sign-in are what turn the inventory into something to act on: workbooks whose owner has lost their licence, or hasn't signed in for a year.

It never changes anything on the site.

```bash
python 02-content-inventory/02_content_inventory.py
```


## This or Admin Insights?

If you're a **site admin on Tableau Cloud** (or have been given access to the Admin Insights project), look at Admin Insights first. Its *Site Content* data source already lists the projects, data sources, flows, workbooks and views on your site, and it adds usage. Most of what this script reports is in there.

This script is for the cases Admin Insights doesn't cover:

| | Admin Insights (Site Content) | This script |
| --- | --- | --- |
| Tableau Server | Not available | Works |
| Who can use it | Site admins, and anyone given access to the Admin Insights project | Anyone. You see the content your permissions allow |
| How fresh | As of its last scheduled refresh | As of now |
| Full project path | Parent project, top-level project and level | Every level: `Finance / Reporting / Monthly` |
| Output | A data source to build views on | CSV files, ready for Excel or as the input to another script |
| Usage and views | Yes, in detail | Total views per workbook (all time) |

## The flow

1. **Sign in and read.** The project tree and the workbook list, every page of each.
2. **Choose the scope.**
   - Every project.
   - Every project except some top-level projects.
   - Only some top-level projects, with everything under them.

   Each top-level project is listed with its workbook count. Pick with `3`, `1,4,7`, `2-5` or `a`.
3. **Review.** You see the scope, the counts, where the files will go (`./output` unless you pass `--output-dir`) and the first five workbooks. If any workbooks belong to unlicensed users, the review says how many. Then confirm to write the files.

```text
Review
  Site          acme on https://10ax.online.tableau.com
  Signed in as  Jo Admin
  Scope         Every project except: Archive
  Projects      12
  Workbooks     47 (2 in personal space)
  Files         output/workbooks_acme_2026-10-10_1432.csv
                output/projects_acme_2026-10-10_1432.csv

  First 5 of 47:
    Budget vs Actual  Finance / Reporting / Monthly  owner Jo Admin
    ...
! 3 workbooks owned by unlicensed users. Filter Owner Site Role for "Unlicensed" to see which.
? Write these 2 files? (Y/n):
```

| Flag | Effect |
| --- | --- |
| `--include "A/B"` | Only this project and everything under it. Any level, case-insensitive. Repeat for more |
| `--exclude "A/B"` | Every project except this one and everything under it. Repeat for more |
| `--output-dir PATH` | Folder for the files (default: `./output`, created if needed) |
| `-y`, `--yes` | No prompts. The scope is every project unless `--include` or `--exclude` is given |
| `--dry-run` | Reads the site and shows the review and preview, but writes no files |
| `--env-file PATH` | Use a different settings file |

```bash
# Everything, unattended (a scheduled job)
python 02-content-inventory/02_content_inventory.py --yes

# Two branches, into a folder of your choice
python 02-content-inventory/02_content_inventory.py --include Finance --include "Sales/EMEA" \
    --output-dir ~/Desktop/inventory --yes
```

`output/` is git-ignored in this repo: the reports hold names and email addresses.

## The files

**`workbooks_<site>_<date>.csv`**, sorted by project path, then name:

| Column | Notes |
| --- | --- |
| Workbook | |
| Project Path | Every level, joined with ` / ` |
| Top-Level Project, Project | The first and last parts of the path |
| Location | `Project`, `Personal space`, or `Project not visible to you` (see below) |
| Owner Name, Owner Email, Owner Username | |
| Owner Site Role | `Creator`, `Explorer`, `Viewer`, `SiteAdministratorCreator`, `Unlicensed` and so on |
| Owner Last Login (UTC) | Blank if they've never signed in |
| Total Views | All-time views, summed over the workbook's views. For each view, see [entry 03](../03-embed-urls/README.md) |
| Created (UTC), Updated (UTC) | `2026-09-12 18:20:11`, a format Excel reads as a date |
| Size (MB) | Whole megabytes, rounded by Tableau |
| Tags | Comma-separated |
| Webpage URL | |
| Workbook ID, Project ID | The reliable key for joining to other reports, or as input to another script (see the formula safeguard below) |

**`projects_<site>_<date>.csv`**, in tree order:

| Column | Notes |
| --- | --- |
| Project, Full Path, Top-Level Project | |
| Depth | 1 for a top-level project |
| Workbooks | In this project itself, within the scope |
| Workbooks Incl. Sub-projects | In this project and everything below it |
| Owner Name, Owner Email | The project's owner. System-owned projects (such as Admin Insights) have no email |
| Content Permissions | `ManagedByOwner`, `LockedToProject` or `LockedToProjectWithoutNested` |
| Description | |
| Parent Project ID, Project ID | |

**Safeguards**

| Situation | What happens |
| --- | --- |
| A workbook sits in someone's Personal Space, or in a project your account can't see | Kept, with its Location saying so, and counted in the review. Counts always add up. An `--include` scope names projects, so these fall outside it |
| A name starts with `=`, `+`, `-` or `@` | Written with a leading `'`, so Excel shows it as text instead of running it as a formula. Anyone who can publish chooses workbook names. That also means a name in the file can differ from the name on the site, so scripts that read this CSV should match on **Workbook ID**, never on the name |
| You run it twice in the same minute | The second run's files get `_2`. Nothing is overwritten |
| The file is open in Excel (Windows) | A clear message to close it, instead of a traceback |
| An `--include` path doesn't exist | Stops before writing, and lists the projects that do exist at that level |
| Non-ASCII names (`Zoë`, `München`) | Files are UTF-8 with a byte-order mark, so Excel shows them correctly |

## REST calls it makes

| Step | Call |
| --- | --- |
| Find the API version | `GET /api/{v}/serverinfo` |
| Sign in / out | `POST /api/{v}/auth/signin`, `POST /api/{v}/auth/signout` |
| Load projects, with owners | `GET /sites/{site-id}/projects?fields=_default_,owner.name,owner.fullName,owner.email` (all pages) |
| Load workbooks, with owners | `GET /sites/{site-id}/workbooks?fields=_default_,owner.name,owner.fullName,owner.email,owner.siteRole,owner.lastLogin` (all pages) |
| View counts | `GET /sites/{site-id}/views?includeUsageStatistics=true` (all pages), summed per workbook |
| Owners, only if the server ignores `fields` | `GET /sites/{site-id}/users` (all pages) |

**The `fields` parameter.** By default, Query Workbooks and Query Projects give only the owner's id (and, for workbooks, user name). A field expression asks for more of the owner in the same request. So a site with 150 workbooks takes one call per 1,000 workbooks, not one extra call per owner or a second pass over every user. `_default_` keeps everything the call normally returns.

**Usage.** Query Workbooks can't return usage through `fields` (Tableau answers "Invalid field names"). Views carry it instead: Query Views for Site with `includeUsageStatistics=true` returns every view with `usage.totalViewCount`, 1,000 per call, and the script adds them up per workbook.

Everything is read and the session signed out before the scope prompt. The rest of the run is local.
