# 11 · Retry failed refreshes, drip-fed to the rate limit

Find every extract refresh that failed overnight, then start them again: not all at once, but paced to the number of refreshes your site may start per hour, slowing down whenever Tableau says "too many" and picking up speed again as calls succeed. A tracking CSV carries every item from scan to retry to result.

This entry **starts refreshes on your site**, which adds load. Scan with `--dry-run` or `--scan-only` first. It needs a **site administrator** account: only admins can list background jobs.

```bash
python 11-retry-failed-refreshes/11_retry_failed_refreshes.py
```


## Three steps, one CSV

| Step | What happens |
| --- | --- |
| **Scan** | Failed refresh jobs from the last `--hours` (14 by default: overnight), plus scheduled refresh tasks that have failed several times in a row. Each failed job is looked up to find exactly which data source or workbook it was for, and the error it gave. One row per item, written to `refresh_retries_<site>_<date>.csv` |
| **Retry** | A refresh is started for each `Pending` row, paced to `--calls-per-hour`. The CSV is saved after every row |
| **Check** | Later, `--check` looks up each refresh this tool started and records `Succeeded`, `Failed again`, `Cancelled` or `Still running` |

Run with no arguments to scan, see the review, and then decide whether to retry straight away. Between steps, the CSV is yours to edit. Set a row's Status to `Skip` and retry leaves it alone; set a `Failed to start` row back to `Pending` to try it again.

```text
Review
  Site          acme on https://10ax.online.tableau.com
  Window        last 14 hours
  To retry      23 data sources, 4 workbooks
  Tracking CSV  output/refresh_retries_acme_2026-10-10_0700.csv
  2 failed flow runs not included: flows aren't extract refreshes.

  Ledger  Finance · Job history  Login failed for user svc_etl
  CRM  Marketing · Scheduled task (3 failures in a row)
  ...
? Start refreshes for 27 items now, paced at up to 120/hour? (y/N):
```

| Flag | Effect |
| --- | --- |
| *(none)* | Scan, review, then ask whether to retry now |
| `--scan-only` | Scan and write the CSV; start nothing |
| `--retry CSV` | Retry the `Pending` and `Rate limited` rows of an earlier scan |
| `--check CSV` | Record how the refreshes this tool started turned out |
| `--hours N` | How far back to scan (default 14) |
| `--calls-per-hour N` | The most refreshes to start per hour (default 120; see below) |
| `--output-dir PATH` | Folder for the CSV (default: `./output`) |
| `-y`, `--yes` | No prompts: scan and retry in one go (or run `--retry` / `--check` without asking) |
| `--dry-run` | Scan and review only: no CSV, no refreshes |
| `--env-file PATH` | Use a different settings file |

```bash
# The overnight clean-up as a scheduled job at 7am, on an Enterprise site with 50 Creators
python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --hours 14 --calls-per-hour 290 --yes
# ...and at 9am, the results
python 11-retry-failed-refreshes/11_retry_failed_refreshes.py --check output/refresh_retries_acme_2026-10-10_0700.csv --yes
```

## The rate limit, and `--calls-per-hour`

Tableau Cloud caps how many refreshes a site may start through the API in an hour. That covers Update Data Source Now, Update Workbook Now, and Run Extract Refresh Task. Go over the cap and every call is refused with HTTP 429, "Please retry after N second(s)". The cap depends on your licence, per [Tableau's capacity documentation](https://help.tableau.com/current/online/en-gb/cloud_manager_capacity.htm):

| Licensing | Refreshes you can start per hour |
| --- | --- |
| Usage-based, Standard | 120 |
| Usage-based, Enterprise | 240, plus 1 per Creator licence |
| Usage-based, Tableau+ | 480, plus 2 per Creator licence |
| Capacity-based | 1,000 per tenant |

The default, **120**, is safe on every edition. Set `--calls-per-hour` to your own cap, or a little under it if other tools also start refreshes.

**How the pacing works:**
- **Evenly spaced.** At 120 an hour, the calls go out one every 30 seconds rather than in a burst.
- **Remembered.** The calls made are recorded in `.refresh_pace_<site>.json` next to the CSV, so a second run within the hour knows what the first one spent.
- **Backs off on a 429.** It waits as long as Tableau asks (plus 5 seconds) and drops the pace by a quarter.
- **Recovers slowly.** Every 25 successful calls in a row win back a little pace, up to your limit.
- **Gives up gracefully.** A row still refused after 4 tries is marked `Rate limited` for the next `--retry`.

## The tracking CSV

| Column | Notes |
| --- | --- |
| Type, Name, Project Path | `Data source` or `Workbook` |
| Failures | Failed jobs in the window, or failures in a row for a scheduled task |
| Last Failed (UTC), Source, Last Error | `Job history` or `Scheduled task (N failures in a row)`; the error text from the latest failure |
| Status | `Pending` → `Queued` / `Already queued or running` / `Rate limited` / `Failed to start` / `Skip`, then after `--check`: `Succeeded` / `Failed again` / `Cancelled` / `Still running` |
| Detail | Why it failed to start, or the new error |
| New Job ID, Started (UTC) | The refresh this tool started |
| Content ID, Task ID, Failed Job ID | |

**Queued is not succeeded.** Starting a refresh only puts it in Tableau's queue, and it can fail again for the same reason it failed overnight. `--check` is how you find out.

## REST calls it makes

| Step | Call |
| --- | --- |
| Failed jobs | `GET /sites/{site-id}/jobs?filter=status:eq:Failed,createdAt:gt:{cutoff}` (all pages). Filtered on the server, so a busy site's thousands of successful jobs never come back |
| Which item, and why | `GET /sites/{site-id}/jobs/{job-id}`, once per failed refresh job |
| Names, paths, tasks | `GET /projects`, `/datasources`, `/workbooks` (all pages), `GET /tasks/extractRefreshes` |
| Start a refresh | `POST /sites/{site-id}/datasources/{id}/refresh` or `/workbooks/{id}/refresh`; if that's refused and the item has a scheduled task, `POST /tasks/extractRefreshes/{task-id}/runNow` |
| Check | `GET /sites/{site-id}/jobs/{new-job-id}`: `finishCode` 0 succeeded, 1 failed, 2 cancelled |

Only the "start a refresh" calls count toward the hourly cap; the reads don't.
