# 05 · Users and groups: who's on the site, who's in what, who needs a look

A read-only report of every user and group on your site, as three CSV files, plus a short list of the users worth a look before a licence review.

```bash
python 05-users-and-groups/05_users_and_groups.py
```


## What it writes

**`users_<site>_<date>.csv`**, one row per user, sorted by name:

| Column | Notes |
| --- | --- |
| Name, Username, Email | |
| Site Role | `Creator`, `Explorer`, `Viewer`, `SiteAdministratorCreator`, `Unlicensed`... |
| Sign-in | How they authenticate, e.g. `ServerDefault`, `SAML`, `TableauIDWithMFA` |
| Last Login (UTC), Days Since Login | Both blank if they've never signed in |
| Groups, Group Count | Every group except the built-in All Users |
| Workbooks Owned, Data Sources Owned | What would need a new owner if they left |
| User ID | |

**`groups_<site>_<date>.csv`**: Group, Members, Domain, Licence On Sign-in (the site role a group grants when members sign in, if set), Group ID.

**`memberships_<site>_<date>.csv`**: one row per user per group, with the group, the user's name, username, email and site role, and both IDs. It's the shape for a pivot table.

## The review

```text
Review
  Site    acme on https://10ax.online.tableau.com
  Users   412: 280 Viewer, 96 Explorer, 21 Creator, 12 Unlicensed, 3 SiteAdministratorCreator
  Groups  37 (1,288 memberships)
  Files   output/users_acme_2026-10-10_1432.csv
          output/groups_acme_2026-10-10_1432.csv
          output/memberships_acme_2026-10-10_1432.csv

  Worth a look:
! 12 users unlicensed: ...
! 9 users never signed in: ...
! 41 users not signed in for 90+ days: ...
! 6 users in no group besides All Users: ...
? Write these 3 files? (Y/n):
```

| Flag | Effect |
| --- | --- |
| `--stale-days N` | The "not signed in for" threshold (default 90) |
| `--output-dir PATH` | Folder for the files (default: `./output`) |
| `-y`, `--yes` | No prompts |
| `--dry-run` | The review only; no files |
| `--env-file PATH` | Use a different settings file |

On Tableau Cloud, Admin Insights' *TS Users* data source has most of the user columns. This report adds the group memberships and content ownership side by side, works on Tableau Server, and is as of now.

## REST calls it makes

| Step | Call |
| --- | --- |
| Users | `GET /sites/{site-id}/users` (all pages). The default response already has email, full name, site role, sign-in method and last sign-in |
| Groups | `GET /sites/{site-id}/groups?fields=_default_,userCount` (all pages) |
| Members | `GET /sites/{site-id}/groups/{group-id}/users`, once per group (all pages) |
| Ownership | `GET /sites/{site-id}/workbooks` and `/datasources` (all pages), counted by owner id |

A site with G groups takes G member calls. There's no single call that returns every membership.
