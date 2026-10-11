# 08 · Permissions: report every rule, then grant, revoke or restore safely

Two jobs in one entry:

- **Report** (read-only, the default): every explicit permission rule on your projects, their default rules for new workbooks, and your workbooks, as a CSV with names instead of IDs.
- **Change:** grant a permission template or named capabilities to users and groups, revoke them, or restore an earlier state. Every change saves a backup in the report's own format first, and `--restore` puts it back.

Changing **changes your site**. Run it with `--dry-run` first.

```bash
python 08-permissions/08_permissions.py                 # choose what to do
python 08-permissions/08_permissions.py --yes           # report everything
python 08-permissions/08_permissions.py --grant view --users-csv team.csv --workbook "Finance/Reporting/Budget" --dry-run
```


## Report

1. **Whose rules:** projects (with their defaults), workbooks, or both.
2. **Scope:** every project, all except some branches, or only some.
3. **Read** one rule set per item, then **review** and write the CSV.

```text
Review
  Site   acme on https://10ax.online.tableau.com
  Scope  Every project
  Read   37 projects, 37 project defaults, 412 workbooks
  Rules  9,880 capability rules for 41 grantees
  File   output/permissions_acme_2026-10-10_1432.csv
! 23 rule sets grant to individual users rather than groups. Groups are easier to keep right.
! 4 Deny rules. A group's Deny overrides Allow from the user's other groups (only a rule on the user beats it), so check these are intended.
```

| Flag | Effect |
| --- | --- |
| `--type projects\|workbooks\|all` | Whose rules to report |
| `--include "A/B"` / `--exclude "A/B"` | Scope, as in entry 02. Repeatable |

`permissions_<site>_<date>.csv` has one row per grantee per capability: Content Type (`Project`, `Project default for workbooks`, `Workbook`), Name, Project Path, Project Permissions (the lock setting), Grantee Type, Grantee, Capability, Mode, Content ID, Grantee ID. Pivot it, with grantees down the side and capabilities across the top, for a permissions matrix.

**Explicit, not effective.** These are the rules set on each item. What one person can actually do also depends on their site role, all their groups, and project locking. For one person on one item, Tableau's permissions dialog is the place to look.

## Change: grant, revoke, restore

| Action | What it does |
| --- | --- |
| `--grant view\|explore\|publish\|administer` | Gives the grantees one of Tableau's templates on each item. A capability already set the other way (Deny instead of Allow) is replaced. Other rules are untouched |
| `--grant custom --capability Name[:Allow\|Deny]` | Only the named capabilities. `--capability` also adds to a template |
| `--revoke` | Removes the grantees' rules from each item: all of them, or only those named with `--capability` |
| `--restore BACKUP` | Makes each item in the backup match it exactly |

**The items:** `--workbook "Project/Sub/Workbook"` (repeatable; a bare name is fine if it's unique), `--workbooks-csv FILE` (a Workbook ID column, so an [entry 02](../02-content-inventory/README.md) inventory filtered in Excel works), `--project PATH` (the project's own rules), and `--project-defaults PATH` (the rules new workbooks in it start with).

**The grantees:** `--group NAME`, `--user NAME` (username or email, any case), or `--users-csv FILE` (a Username, User or Email column, such as `USERNAME`). All repeatable and combinable.

Without those flags, you're asked. Pick the items with the project picker, then choose groups from a list, users from a CSV, or users by name, and finally the template.

**The templates**, matching Tableau's, each including the one before:

| Template | Capabilities (REST names) |
| --- | --- |
| View | Read, Filter, ViewComments, AddComment, ExportImage, ExportData |
| Explore | + ShareView, ViewUnderlyingData, WebAuthoring |
| Publish | + ExportXml, Write |
| Administer | + ChangeHierarchy, Delete, ChangePermissions |

Newer capabilities (Run Explain Data, API access and so on) can be added by name with `--capability`.

### How every change runs

1. **Read** each item's current rules.
2. **Work out** the rules it should have, and **review** the difference, item by item:

   ```text
   Workbook: Finance / Reporting / Budget
     + Jo Müller (user): AddComment, ExportData, ExportImage, Filter, Read, ViewComments
     - Finance Team (group): ExportData Deny
   ```
3. **Confirm** (the default is No).
4. **Back up** the current rules of every item about to change: `permissions_backup_<site>_<date>.csv`, in the report's format. Items with no rules get a placeholder row, so a restore knows to leave them empty again.
5. **Change** only the difference: deletes first, then one add per item.
6. **Log** every rule added or removed, with its outcome: `permissions_changes_<site>_<date>.csv`. The summary prints the `--restore` command.

| Flag | Effect |
| --- | --- |
| `--output-dir PATH` | Folder for the CSVs (default: `./output`) |
| `-y`, `--yes` | No prompts. With `--grant` or `--revoke`, needs the items and the grantees |
| `--dry-run` | Read and review only: nothing changes and no file is written |
| `--env-file PATH` | Use a different settings file |

### Safeguards

| Situation | What happens |
| --- | --- |
| A grant would set a capability already set the other way | The old rule is deleted, then the new one added, so Tableau doesn't refuse the clash |
| A user or group isn't on the site | Listed in the review; the rest go ahead |
| A workbook name is in several projects | Refused: give it as `Project/Workbook` |
| The workbook's project has locked permissions | Called out in the review; Tableau refuses the change, and the error says to change the project's default rules instead |
| Nothing would change | Nothing is written or sent |
| You restore after others have made changes too | The review warns that restore makes the items match the backup exactly, undoing any change made since, by anyone. The difference is shown first |
| One item fails | Recorded in the changes log; the rest carry on. Exit code 1 |

## REST calls it makes

| Step | Call |
| --- | --- |
| Projects, workbooks, groups, users | `GET /projects`, `/workbooks`, `/groups`, `/users` (all pages) |
| Read rules | `GET .../projects/{id}/permissions`, `.../projects/{id}/default-permissions/workbooks`, `.../workbooks/{id}/permissions` |
| Remove one rule | `DELETE .../permissions/{users\|groups}/{grantee-id}/{capability}/{mode}` (same three bases) |
| Add rules | `PUT .../permissions` with an XML body of grantees and capabilities, one per item |

Rules refer to groups and users by ID only, which is why names are looked up first.
