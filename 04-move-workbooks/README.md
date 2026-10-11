# 04 · Move workbooks, with an undo log

Move workbooks to another project: a few picked from a list, or hundreds listed in a CSV. Every run writes a log of where each workbook was. Feed the log back with `--undo` to put them all back.

This entry **changes your site**. Run it with `--dry-run` first.

```bash
python 04-move-workbooks/04_move_workbooks.py
```


## The flow

1. **Choose the workbooks.**
   - **From a project:** browse to it, then pick one, some or all of its workbooks (`3`, `1,4,7`, `2-5`, `a`). Sub-projects aren't included.
   - **From a CSV:** any CSV with a Workbook ID column. An [entry 02](../02-content-inventory/README.md) inventory, filtered in Excel, is exactly that. Excel's own CSV encodings are read correctly.
   - **Undo:** a log from an earlier run.
2. **Choose the destination** with the project picker, unless you're undoing.
3. **Review.** Every workbook is listed as `move` or `skip`, with the reason for each skip, plus any IDs that aren't on the site and where the log will go. If the destination's permissions are locked, the review says that moved workbooks will take its permissions.
4. **Confirm.** The log is written, then the workbooks move one at a time. A summary follows, with the exact `--undo` command.

```text
Review
  Site         acme on https://10ax.online.tableau.com
  Destination  Archive / 2026
  To move      3
  Skipped      1
  Log          output/moves_acme_2026-10-10_1432.csv

  move  Sales Overview  Sandbox / Drafts -> Archive / 2026
  move  Pipeline  Sandbox / Drafts -> Archive / 2026
  skip  Q3 Review  (name taken in the destination)
  move  Budget  Finance -> Archive / 2026
? Move 3 workbooks now? (y/N):
```

| Flag | Effect |
| --- | --- |
| `--from-project "A/B"` | Pick workbooks from this project |
| `--select LIST` | With `--from-project`: which ones, numbered as listed (`3`, `1,4,7`, `2-5`, `all`) |
| `--from-csv PATH` | Move the workbooks listed in this CSV (a `Workbook ID`, `id` or `luid` column) |
| `--undo LOG` | Move everything in this log back where it came from |
| `--to-project "A/B"` / `--to-project-id ID` | The destination |
| `--output-dir PATH` | Folder for the log (default: `./output`) |
| `-y`, `--yes` | No prompts. Needs a source (`--from-project` with `--select`, `--from-csv`, or `--undo`) and a destination (except for `--undo`) |
| `--dry-run` | The review only: nothing moves and no log is written |
| `--env-file PATH` | Use a different settings file |

```bash
# Archive everything an inventory flagged, after checking the review
python 04-move-workbooks/04_move_workbooks.py --from-csv stale.csv --to-project "Archive/2026" --dry-run
python 04-move-workbooks/04_move_workbooks.py --from-csv stale.csv --to-project "Archive/2026" --yes

# Changed your mind
python 04-move-workbooks/04_move_workbooks.py --undo output/moves_acme_2026-10-10_1432.csv
```

## The log

`moves_<site>_<date>.csv`, one row per workbook in the batch:

| Column | Notes |
| --- | --- |
| Workbook, Workbook ID | |
| From Project, From Project ID | Where it was |
| To Project, To Project ID | Where it went |
| Status | `Moved`, `Failed`, `Skipped`, or `Planned` if the run stopped before reaching it |
| Detail | Why it failed or was skipped |
| When (UTC) | |

It's written **before the first move** and again at the end, so even a run you interrupt can be undone.

## Safeguards

| Situation | What happens |
| --- | --- |
| A workbook is already in the destination | Skipped |
| The destination already has a workbook with the same name | Skipped before the run. Tableau would refuse it (409) anyway |
| Two selected workbooks share a name | The first moves; the second is skipped |
| An ID in the CSV isn't on this site | Listed in the review as not found |
| The run is interrupted | The log already lists every planned move, so `--undo` still works |
| `--undo`, but a workbook has been moved again since | Left alone and reported. Undo only puts back what this tool put there |
| One move fails (permissions, say) | Reported and logged; the rest carry on. The exit code is 1 |
| The destination's permissions are locked | Called out in the review: the workbooks will take the project's permissions |

## What a move does and doesn't change

The workbook keeps its name, owner, views, extracts, schedules and subscriptions. Its URL doesn't change either, because Tableau links don't include the project. What changes is who can see it, if the two projects' permissions differ. On a project with locked permissions, the workbook takes the project's permissions and its own no longer apply.

## REST calls it makes

| Step | Call |
| --- | --- |
| Sign in / out, API version | As in every entry |
| Load projects and workbooks | `GET /sites/{site-id}/projects`, `GET /sites/{site-id}/workbooks` (all pages) |
| Move one workbook | `PUT /sites/{site-id}/workbooks/{workbook-id}` with `{"workbook": {"project": {"id": "..."}}}` |

**Update Workbook** changes only what the request body names. Leave everything else out, and only the project changes.
