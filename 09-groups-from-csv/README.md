# 09 · Groups from a CSV: add users to groups, never remove

Add users to groups from a CSV, creating any groups that don't exist yet. It **only adds**: nobody is ever removed from a group, so a wrong file can't empty one.

This entry **changes your site**. Run it with `--dry-run` first.

```bash
python 09-groups-from-csv/09_groups_from_csv.py
python 09-groups-from-csv/09_groups_from_csv.py --csv pilot.csv --group "Pilot Users" --dry-run
```


## The CSV

Any CSV with a column of users, and optionally a column of groups:

```text
Email,Group
jo@example.com,Finance Team
sam@example.com,Finance Team
sam@example.com,Pilot Users
```

- **Users** can be usernames or email addresses, in any case. The column is found by name (`Username`, `User`, `User Name`, `Email`, `Name`...) or set with `--user-column`.
- **Groups** come from a `Group` / `Group Name` column (or `--group-column`). Without one, `--group NAME` puts everyone into one group, or you're asked.
- **Excel's encodings** (Windows-1252, non-breaking spaces) are handled. Blank rows and repeats are skipped.

## The flow

1. **The CSV,** read before signing in, so a bad file fails fast.
2. **Match** each user against the site, and each group against the site's groups (ignoring case), and check who's already a member.
3. **Review:** per group, how many will be added, how many are already in, how many aren't on the site; which groups will be created; and a warning for any group that grants a licence on sign-in. Then confirm.
4. **Create** any new groups, then **add** members one at a time.
5. **Results:** a CSV recording what happened to every row. Your input file isn't touched.

```text
Review
  Site             acme on https://10ax.online.tableau.com
  To add           38
  Already members  12
  Not on site      2
  New groups       Pilot Users
  Results          output/groups_from_csv_acme_2026-10-10_1432.csv

  Finance Team: 30 to add, 12 already in, 1 not on site
! Finance Team grants the Explorer role when members sign in (onLogin). Adding people can change their licence.
  Pilot Users (new): 8 to add, 0 already in, 1 not on site
! Not on this site: old.name@example.com, temp@example.com
? Add 38 memberships and create 1 group? (y/N):
```

| Flag | Effect |
| --- | --- |
| `--csv PATH` | The CSV |
| `--group NAME` | Put every user into this group (ignores any group column) |
| `--user-column NAME` / `--group-column NAME` | Name the columns, if detection doesn't find them |
| `--create-groups` | With `--yes`: allow creating groups. Without it, `--yes` stops rather than create one |
| `--output-dir PATH` | Folder for the results CSV (default: `./output`) |
| `-y`, `--yes` | No prompts; needs `--csv` |
| `--dry-run` | The review only: nothing changes and no file is written |
| `--env-file PATH` | Use a different settings file |

## The results CSV

`groups_from_csv_<site>_<date>.csv`: Group, User (as written in your CSV), Matched User (their username on the site), Status, Detail, Group ID, User ID. Status is one of: `Added`, `Already a member`, `User not on site`, `Group not created`, `Failed`, or `Not reached` if the run stopped first.

## Safeguards

| Situation | What happens |
| --- | --- |
| The CSV leaves someone out | Nothing. This entry never removes members |
| A group name has a typo | It would be a new group: shown as `(new)` in the review, and `--yes` refuses to create it without `--create-groups` |
| A user isn't on the site | Reported; users are never created |
| The group grants a licence on sign-in | Called out: adding people can change what they're licensed for |
| Someone else adds a user mid-run | Tableau answers 409; it's recorded as "already a member", not a failure |
| One add fails | Recorded; the rest carry on. Exit code 1 |

## REST calls it makes

| Step | Call |
| --- | --- |
| Users, groups | `GET /sites/{site-id}/users`, `/groups` (all pages) |
| Current members | `GET /sites/{site-id}/groups/{id}/users`, for each group named in the CSV |
| Create a group | `POST /sites/{site-id}/groups` with `{"group": {"name": "..."}}` |
| Add a member | `POST /sites/{site-id}/groups/{id}/users` with `{"user": {"id": "..."}}` |
