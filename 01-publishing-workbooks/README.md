# 01 · Publishing workbooks: one job, three versions

This is the only entry in the pack shown evolving. The job is the same in all three: get a workbook from your computer onto Tableau Cloud or Server. Each version shows what changes as the script grows up.

| | Script | Built by | In short |
| --- | --- | --- | --- |
| **1a** | [`1a_publish_workbook_handbuilt.py`](1a_publish_workbook_handbuilt.py) | Michael, by hand, while learning the REST API | **Simple and effective.** About 130 lines on Tableau Server Client (TSC). Give it a file and a project name, and it publishes |
| **1b** | [`1b_publish_workbook.py`](1b_publish_workbook.py) | Claude, tested by Michael | **Robust.** Direct REST calls. A file browser and project tree, a name-clash check, a review screen, a dry run, and a flag for every prompt |
| **1c** | [`1c_bulk_publish_workbooks.py`](1c_bulk_publish_workbooks.py) | Claude, tested by Michael | **Supercharged.** Scans a whole folder tree, then publishes one, some or all of what it finds in a single run, with tabs on, every clash settled up front and a summary at the end |

All three read the same `.env` (see [Setup](../README.md#setup)). Run them from the repo root.

### What changes from 1a to 1b

The hand-built version is close to what most people write first, and it works. Every gap below stays invisible until the site grows, two projects share a name, or someone overwrites the wrong workbook:

| In 1a | What can go wrong | In 1b |
| --- | --- | --- |
| Matches the project by name only | With "Reporting" under both Finance and Sales, the wrong one is picked (since promotion, 1a stops and lists them) | Pick by path (`Finance/Reporting`) or browse the tree |
| No check for a taken name | `CreateNew` uploads the whole file before Tableau refuses it; `Overwrite` replaces without warning | Checks first, shows the existing workbook's owner and last update, and asks |
| No review or dry run | `--mode Overwrite` acts immediately | A review screen, a confirmation, `--dry-run` |
| Not interactive | You need the exact path and project name | A file browser and a project picker, or flags |
| No warning for `.twb` | A `.twb` can publish with no data | Warns before publishing |
| General error handling | Whatever exception occurred | Tableau's own error code and message, with hints |
| Tableau Server Client | The REST calls are hidden inside a library | Direct REST calls you can read |

---

## 1a · Hand-built (TSC)

Michael wrote this before the rest of the pack existed. It was promoted with as few changes as possible, and each change is marked `Promoted:` in the code. It has no review screen, dry run or name check, and that's the point of the comparison.

```bash
pip install -r requirements-dev.txt          # includes tableauserverclient
python 01-publishing-workbooks/1a_publish_workbook_handbuilt.py "Sales Overview.twbx" "Finance"
python 01-publishing-workbooks/1a_publish_workbook_handbuilt.py "Sales Overview.twbx" "Finance" --mode Overwrite --hide-tabs
```

| Argument | Effect |
| --- | --- |
| `workbook_path` | The `.twb` / `.twbx` to publish |
| `project_name` | Target project, by its exact name. If two projects share the name, it stops and lists them |
| `--name NAME` | Name on Tableau (default: the file name) |
| `--mode CreateNew\|Overwrite` | `CreateNew` (default) refuses a taken name; `Overwrite` replaces it without asking |
| `--hide-tabs` | Tabs are **on** by default; this turns them off |
| `--env-file PATH` | Use a different settings file |

---

## 1b · Robust single publish

```bash
python 01-publishing-workbooks/1b_publish_workbook.py
```

The interactive flow:

1. **Choose a workbook.** A numbered list of folders and `.twb`/`.twbx` files. Open folders by number, go `u`p a level, or `p`aste a path (dragging a file into the terminal works).
2. **Choose a project.** Top-level projects are listed first. Choosing one shows `0. Publish here` plus its sub-projects, and this repeats until you publish or reach a project with no sub-projects. `b` goes back.
3. **Name it.** Defaults to the file name. If that name already exists in the project, choose to overwrite it or use a different name.
4. **Review and confirm.** Nothing is sent to Tableau until you say yes.

The workbook is published as-is, with any extracts or data it packages embedded.

| Flag | Effect |
| --- | --- |
| `--file PATH` | Workbook to publish |
| `--project "A/B/C"` | Target project by path (case-insensitive) |
| `--project-id ID` | Target project by id |
| `--name NAME` | Name on Tableau (default: file name) |
| `--overwrite` | Replace a same-named workbook in the project without asking |
| `--show-tabs` | Show sheets as tabs |
| `-y`, `--yes` | No prompts at all. Needs `--file` and a project, and stops if the name is taken and `--overwrite` wasn't given |
| `--dry-run` | Everything except the publish, including the review |
| `--env-file PATH` | Use a different settings file |

```bash
# Fully non-interactive, e.g. from CI
python 01-publishing-workbooks/1b_publish_workbook.py --file "Sales Overview.twbx" \
    --project "Finance/Reporting" --overwrite --yes
```

**REST calls it makes**

| Step | Call |
| --- | --- |
| Find the API version | `GET /api/{v}/serverinfo` |
| Sign in / out | `POST /api/{v}/auth/signin`, `POST /api/{v}/auth/signout` |
| Load projects | `GET /sites/{site-id}/projects` (all pages) |
| Check the name | `GET /sites/{site-id}/workbooks?filter=name:eq:{name}` |
| Publish (64 MB or less) | `POST /sites/{site-id}/workbooks` as `multipart/mixed` |
| Publish (over 64 MB) | `POST /fileUploads`, `PUT /fileUploads/{id}` per 32 MB chunk, then `POST /workbooks?uploadSessionId=...` |

---

## 1c · Supercharged bulk publish

```bash
python 01-publishing-workbooks/1c_bulk_publish_workbooks.py
```

Publishes a batch of packaged workbooks into one project in a single run. Each workbook keeps its file name as its name on Tableau and is published with **tabbed views on**.

1. **Choose a folder.** A numbered list of subfolders. Open one by number, go `u`p a level, `p`aste a path, or press `0` to scan the folder you're in.
2. **Choose workbooks.** Every `.twbx` in that folder and all its subfolders, numbered, with its size and date. Type `3`, `1,4,7`, `2-5`, a mix like `1-3, 8 10`, or `a` for all.
3. **Choose a project.** The same tiered picker as 1b.
4. **Review.** Each workbook is marked `new` or `exists`, with the existing one's last update and owner. If any already exist, you decide once for the whole batch: overwrite them all, or skip them all.
5. **Publish.** One at a time, with progress. A failure doesn't stop the rest, and a summary at the end lists what was published, skipped and failed, with links.

```text
Found 6 .twbx files in ~/Desktop/Exports
  1. Executive Summary.twbx  4.9 MB, modified 2025-10-03
  2. Finance/Budget vs Actual.twbx  3.1 MB, modified 2025-10-03
  3. Finance/Cash Flow.twbx  1.7 MB, modified 2025-10-03
  4. Finance/Q3 Close/Variance Drilldown.twbx  7.2 MB, modified 2025-10-03
  5. Marketing/Campaign ROI.twbx  2.8 MB, modified 2025-10-03
  6. Regional Sales.twbx  2.3 MB, modified 2025-10-03
? Select (e.g. 3 / 1,4,7 / 2-5 / a for all, q to quit): 1-3,6
```

**What it scans**

- `.twbx` files only: packaged workbooks, which carry their own extracts and files. A `.twb` packages no data and only works if its data comes from a database or published data source the site can reach. Publish one of those with 1b, which warns about this.
- Every subfolder at any depth, except hidden ones (`.git`, `.backup` and so on).
- Hidden files and `~` lock or temp files are left out.

**Safeguards**

| Situation | What happens |
| --- | --- |
| Two selected files share a name, e.g. `Finance/Sales.twbx` and `Archive/Sales.twbx` | Refused before signing in: they'd land on the same workbook. Pick only one of each (names are compared ignoring case) |
| Some names already exist in the project | Shown in the review; one choice for all of them. With `--yes` you must pass `--overwrite` or `--skip-existing` |
| A same-named workbook appears in the project while the batch runs | Only the workbooks known to clash are sent with `overwrite=true`, so Tableau refuses this one (409) and it's reported as failed, not silently replaced |
| One workbook fails (permissions, a broken file) | Reported, and the batch carries on. The exit code is 1 if anything failed |
| A bad `--select` | Fails before signing in |
| Ctrl-C mid-batch | Stops. Workbooks already published stay published |

| Flag | Effect |
| --- | --- |
| `--folder PATH` | Folder to scan, subfolders included |
| `--select LIST` | Which of the found workbooks to publish: `3`, `1,4,7`, `2-5` or `all`, numbered as in the list |
| `--project "A/B/C"` | Target project by path (case-insensitive) |
| `--project-id ID` | Target project by id |
| `--overwrite` | Replace workbooks that already exist in the project |
| `--skip-existing` | Leave workbooks that already exist alone |
| `-y`, `--yes` | No prompts at all. Needs `--folder`, `--select` and a project |
| `--dry-run` | Everything except the publish, including the review and the clash check |
| `--env-file PATH` | Use a different settings file |

```bash
# See what would happen, then do it
python 01-publishing-workbooks/1c_bulk_publish_workbooks.py --folder ~/Desktop/Exports --select all \
    --project "Finance/Reporting" --skip-existing --dry-run
python 01-publishing-workbooks/1c_bulk_publish_workbooks.py --folder ~/Desktop/Exports --select all \
    --project "Finance/Reporting" --skip-existing --yes
```

The list is sorted by path within the folder, so `--select` numbers stay the same between runs as long as the folder's contents don't change.

**REST calls:** the same as 1b, repeated per workbook. There's one name check per selected workbook before anything is published, then one publish each, with `showTabs="true"`. A batch of N workbooks of 64 MB or less makes about 2N + 5 requests.
