---
name: tableau-rest-api
description: How to write Tableau REST API calls that work the first time, and how scripts in the Tableau REST API Starter Pack are built. Use when writing or changing a script that calls the Tableau Cloud or Server REST API (projects, workbooks, views, data sources, users, groups, permissions, jobs, refreshes, subscriptions, publishing, downloads), adding a new numbered entry to the pack, using the tcs toolkit, or debugging a REST call that returns 400, 403, 409 or 429.
---

# Writing Tableau REST API scripts

The hard-won rules behind every script in this pack. Each was checked against a live Tableau Cloud site (REST API 3.30) or Tableau's documentation. Read the section for the area you're touching before writing the call.

## Start with the toolkit

Don't hand-roll sign-in, paging or error handling. `tcs` already does it:

```python
from tcs.config import load_settings
from tcs.client import TableauClient, TableauError

with TableauClient(load_settings(args.env_file)) as client:     # detects the API version, signs in, always signs out
    for wb in client.paginate("/workbooks", "workbooks", "workbook", action="List workbooks",
                              params={"fields": "_default_,owner.name,owner.email"}):
        ...
    data = client.get_json(f"/workbooks/{wb_id}/permissions", action="Read permissions")
    client.request("PUT", f"/workbooks/{wb_id}", action="Move workbook", json={"workbook": {"project": {"id": pid}}})
```

- Paths are relative to `/api/{version}/sites/{site-id}`; pass a full URL to go elsewhere.
- Every call takes an `action` label, which becomes the error message's start: "Move workbook failed (HTTP 409, Tableau error ...): ...".
- `TableauError` has `.status`, `.code`, `.summary` and `.detail`. Branch on `.status`.
- Responses are JSON (the client sends `Accept: application/json`).

| Module | Use it for |
| --- | --- |
| `tcs.content` | Listing workbooks (with owners), views (with view counts), data sources, users, groups, members, connections, subscriptions; `placement()` for an item's project path; `view_url()` |
| `tcs.projects` | The project tree: paths, depths, branches, the picker, `--project` lookup |
| `tcs.scope` | "Every project / all except / only these" by prompt or `--include` / `--exclude` |
| `tcs.permissions` | Read rules, templates, and change only the difference |
| `tcs.pacing` | An hourly budget for rate-limited calls, with back-off on 429 |
| `tcs.batch` | One change per item, failures recorded, a summary and exit code |
| `tcs.reports` | CSV and HTML output; reading CSVs saved from Excel |
| `tcs.publishing` | Publishing workbooks, chunked uploads, the name-clash check |
| `tcs.ui` | Prompts, menus, multi-select (`3 / 1,4,7 / 2-5 / a`), colours |

## Identity: IDs, never names

- **Names aren't unique.** Two workbooks or data sources can share a name in different projects, and a workbook and a data source can share one. Find things by ID, or by project path plus name, and refuse an ambiguous bare name.
- **Site ID vs content URL.** Sign-in takes the site's *content URL* (the part after `/site/` in the browser). Every later URL needs the site *ID* that sign-in returns. `TableauClient` handles both.
- **IDs survive renames and moves.** Anything a later script will read back (logs, backups, inventories) should carry IDs.

## Listing and paging

- List calls return at most 1,000 items per page. Keep asking until `pagination.totalAvailable` is reached (`client.paginate` does).
- **Filters:** `?filter=field:op:value`, several joined with commas: `status:eq:Failed,createdAt:gt:2026-10-01T00:00:00Z`. `in` takes a list: `jobType:in:[refresh_extracts,increment_extracts]`.
- A comma *separates* filter expressions, so a name containing a comma can't be filtered with `name:eq:`. List and match locally instead.
- `name:eq:` is exact and case-sensitive. A case-insensitive check has to be done locally.
- Not every field is filterable. On Query Jobs, `finishCode` gives HTTP 400 ("filter query contains a key which is not recognized"). Filter on `status` instead.
- **Visibility is per user.** List calls return what the signed-in account can see: the whole site for an admin, a slice for anyone else.

## Asking for more in one call: `fields`

`?fields=_default_,...` adds related details to a list call. `_default_` keeps the normal response.

| Call | Works | Doesn't |
| --- | --- | --- |
| Query Workbooks | `owner.name`, `owner.fullName`, `owner.email`, `owner.siteRole`, `owner.lastLogin` | `usage...` (400: "Invalid field names") |
| Query Projects | `owner.name`, `owner.fullName`, `owner.email`. A system-owned project has no email | |
| Query Data Sources | `owner.name`, `owner.fullName`, `owner.email` | |
| Query Groups | `userCount` | |
| Query Users | Not needed: the default response already has `email`, `fullName`, `siteRole`, `lastLogin`, `authSetting` | `authSetting` as a field name (400) |

Ask for owner details this way rather than looking each owner up. If a server ignores `fields` (owners come back without `fullName`), fall back to one paged `GET /users`.

## Where things live

- A project is a flat list item with `parentProjectId`; build the tree yourself (`tcs.projects.ProjectTree`).
- `External Assets Default Project` is a system project that can't hold published content; hide it.
- A project whose parent you can't see should be treated as top-level, so it stays reachable.
- Workbooks and views carry `location.type`: `Project` or `PersonalSpace`. An item whose project isn't in your tree is either in someone's Personal Space or in a project you can't see. **Keep and label such items**; never drop them, or counts stop adding up.
- `contentPermissions` on a project: `ManagedByOwner`, `LockedToProject` or `LockedToProjectWithoutNested`. Locked projects decide their workbooks' permissions.

## Views, usage and URLs

- **View counts:** `GET /views?includeUsageStatistics=true` returns every view with `usage.totalViewCount`, 1,000 per call. Sum per `workbook.id` for workbook totals. Views carry only their workbook's ID, so join names from Query Workbooks.
- **A view's link:** `contentUrl` is `Workbook/sheets/View`. The link is `{server}/t/{site}/views/Workbook/View`, without `sheets`. On Tableau Server's Default site there's no `/t/{site}` part.
- **A workbook's page:** `webpageUrl` on the workbook. Links don't include the project, so they survive moves.
- **Iframe:** the view link plus `?:embed=y&:showVizHome=n&:tabs=n&:toolbar=n`.
- **Embedding API v3:** load `https://{server}/javascripts/api/tableau.embedding.3.latest.min.js` (`type="module"`) from the user's own server, then `<tableau-viz src="{view link}" toolbar="bottom" hide-tabs>`.
- Embedded views still need the viewer signed in to Tableau (or SSO / Connected Apps). Escape every name you put in HTML: publishers choose them.

## Changing content

- **Update calls are partial.** `PUT /workbooks/{id}` with `{"workbook": {"project": {"id": "..."}}}` moves it and changes nothing else.
- **Names are unique per project per content type.** Moving or publishing into a clash gives HTTP 409. Check before you start, and still handle 409 for races.
- **Publishing:** a `multipart/mixed` body (not `form-data`). An XML `request_payload` part goes first, then the file. Files over 64 MB go through `POST /fileUploads` and `PUT /fileUploads/{id}` in chunks. `overwrite=true|false` is a query parameter. Send `showTabs` explicitly: Tableau Server Client leaves it out when false.
- **Downloads:** `GET /{workbooks|datasources}/{id}/content`, with `?includeExtract=false` to leave extracts out. The file name is in `Content-Disposition`. Stream to a `.part` file with `stream=True` and `iter_content`, then rename. A data source sometimes comes back named `.twb` / `.twbx`; save it as `.tds` / `.tdsx`.
- **Groups:** `POST /groups` with `{"group": {"name": ...}}`; add a member with `POST /groups/{id}/users` and `{"user": {"id": ...}}`. A 409 there means they're already a member: treat it as success. A group's `import.siteRole` / `grantLicenseMode` means members are licensed on sign-in, so adding people can change their licence.
- **Subscriptions:** `DELETE /subscriptions/{id}` can't be undone through the API. Back up subject, user, content, schedule and format first.

## Permissions

- **Three rule sets:** a project's own (`/projects/{id}/permissions`), its defaults for new workbooks (`/projects/{id}/default-permissions/workbooks`), and each workbook's (`/workbooks/{id}/permissions`).
- **Shape:** `granteeCapabilities: [{group|user: {id}, capabilities: {capability: [{name, mode}]}}]`. Grantees are IDs only, so look names up once from `/groups` and `/users`.
- **Add:** `PUT .../permissions` with an XML body: `<tsRequest><permissions><granteeCapabilities><user id="..."/><capabilities><capability name="Read" mode="Allow"/></capabilities></granteeCapabilities></permissions></tsRequest>`.
- **Delete:** one rule per call: `DELETE .../permissions/{users|groups}/{id}/{capability}/{mode}`.
- **Opposite modes clash.** To change Deny to Allow, delete the Deny first.
- **Locked projects:** workbook-level changes are refused. Change the project's default rules instead.
- **Workbook capability names:** Read, Filter, ViewComments, AddComment, ExportImage, ExportData, ShareView, ViewUnderlyingData, WebAuthoring, ExportXml, Write, ChangeHierarchy, Delete, ChangePermissions.
- **Templates:**
  - **View** = Read, Filter, ViewComments, AddComment, ExportImage, ExportData. ShareView is *not* in View.
  - **Explore** adds ShareView, ViewUnderlyingData, WebAuthoring.
  - **Publish** adds ExportXml, Write.
  - **Administer** adds ChangeHierarchy, Delete, ChangePermissions.
- **Explicit isn't effective.** User rules beat group rules, and Deny beats Allow at the same level. Don't try to compute effective permissions; report explicit ones.
- Change by difference: read current, compute desired, delete then add only what differs (`tcs.permissions`).

## Jobs, refreshes and rate limits

- **Failed refreshes:** `GET /jobs?filter=status:eq:Failed,createdAt:gt:{cutoff}` (site admins only; 403 otherwise). Keep jobs whose `jobType` contains "extract"; flows (`run_flow`) aren't extract refreshes.
- **Which item failed, and why:** `GET /jobs/{id}` → `job.extractRefreshJob.datasource|workbook` `{id, name}`, plus `statusNotes` for the error. Never match job titles to item names.
- `finishCode`: 0 succeeded, 1 failed, 2 cancelled. No `completedAt` means it's still running.
- **Tasks:** `GET /tasks/extractRefreshes` → `task.extractRefresh` with `consecutiveFailedCount` and the target data source or workbook.
- **Starting a refresh:**
  - `POST /datasources/{id}/refresh` or `/workbooks/{id}/refresh` with `{}`, or `POST /tasks/extractRefreshes/{id}/runNow`.
  - HTTP 202 returns a job ID. That's **queued, not succeeded**: check the job later.
  - 409 means it's already queued or running.
- **The hourly cap.** Starting refreshes through the API is capped per site per hour, by licence:
  - Standard: 120
  - Enterprise: 240, plus 1 per Creator
  - Tableau+: 480, plus 2 per Creator
  - Capacity-based: 1,000 per tenant

  Over the cap, every call gets 429 with "Please retry after N second(s)". Space calls evenly, remember spent calls across runs, back off on 429 for the time given, and recover slowly (`tcs.pacing.RateBudget`). Reads don't count toward the cap.
- **Long runs:** if the session expires, a 401 arrives mid-run. Sign in again once, then retry the call.

## Connections

- `GET /{datasources|workbooks}/{id}/connections`: one call per item; there's no site-wide call.
- Fields: `type`, `serverAddress`, `serverPort`, `userName`, `embedPassword`, `authenticationType`.
- **Passwords are never returned.**
- A workbook connection of type `sqlproxy` points at a published data source, named in `datasource`.
- `hyper`, `excel-direct` and `textscan` are extracts and files. Their usernames are internal, not people.

## How an entry is built

Every numbered entry from 02 onward follows the same interface and safety model:

- **Interactive by default, a flag for every prompt.** `--yes` means no prompts at all: it fails rather than asking.
- **A review screen** before anything changes, then **`--dry-run`**, and a confirmation that defaults to No for anything that writes to the site.
- **`--env-file`** for the settings, through `tcs.config.load_settings()` only. Never print a secret.
- **Read the input file before signing in,** so a bad file fails fast.
- **Sign out as soon as the reads are done** if the rest of the run is local.
- **Write the record first.** Anything that changes the site writes its record (a log, a backup, a results file) before the first change, and updates it at the end. Work one item at a time with `tcs.batch.run_batch`: failures are recorded, not fatal, and the exit code is 1 if anything failed.
- **Reports** go to `./output` with timestamped names that never overwrite (`tcs.reports.report_path`). CSVs are UTF-8 with a BOM, and cells starting with `= + - @` get a leading `'`, because names are user input. So match CSV input on IDs, never names.
- **Python 3.9 or newer;** dependencies are `requests` and `python-dotenv` only.
- **Tests** run end to end against `tests/fake_tableau.py`, an in-memory fake of the REST API.
  - Extend the fake rather than mocking `requests`.
  - `fake.fail[(method, path)] = status` injects a failure.
  - The `clock` fixture makes `tcs.pacing` instant.
  - The fake only returns `fields` and usage data when asked for them, so a test proves the request is right.

## When a call fails

| Response | Usually means |
| --- | --- |
| 400, "Invalid field names" / "not recognized" | A `fields` or `filter` name that call doesn't support (see the tables above) |
| 401 on sign-in | Wrong token name or secret, the site's display name instead of its content URL, or an expired token |
| 401 mid-run | The session expired: sign in again |
| 403 | The account can't do this: not an admin (jobs, users), not the owner or project leader, or a locked project |
| 404 | Wrong ID, or an ID from another site |
| 409 | A name clash, already queued/running, already a member, or an opposite-mode permission |
| 429 | The hourly refresh cap. Wait as long as the message says, and slow down |
