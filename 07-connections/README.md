# 07 · Connections: what everything connects to, and as whom

Every data connection on your site, for published data sources, workbooks, or both: the connection type, server, port, username, whether a password is embedded, and how it authenticates.

It answers the question that comes before every password rotation and database move: **"What uses this account, or this server?"**

It never changes anything, and it never sees a password. The API only says whether one is embedded.

```bash
python 07-connections/07_connections.py
python 07-connections/07_connections.py --username svc_reporting --yes   # everything that signs in as one account
```


## The flow

1. **Whose connections:** published data sources, workbooks, or both.
2. **Scope:** every project, all except some branches, or only some.
3. **Read:** one call per item, with progress every 25.
4. **Review:** connection types, usernames in use with counts, how many have an embedded password, and anything that couldn't be read. Then confirm to write the CSV.

```text
Review
  Site         acme on https://10ax.online.tableau.com
  Scope        Every project
  Items read   412
  Connections  655 (210 with an embedded password)
  Types        sqlserver 301, snowflake 122, sqlproxy 98, excel-direct 61, hyper 40
  Usernames    svc_reporting (188), svc_finance (40), jdoe (3)
  File         output/connections_acme_2026-10-10_1432.csv
```

| Flag | Effect |
| --- | --- |
| `--type datasources\|workbooks\|all` | Whose connections |
| `--include "A/B"` / `--exclude "A/B"` | Scope, as in entry 02. Repeatable |
| `--username NAME` | Only connections that sign in as NAME (any case) |
| `--output-dir PATH` | Folder for the CSV (default: `./output`) |
| `-y`, `--yes` | No prompts; `--type` defaults to `all` |
| `--dry-run` | Read and review; no file |
| `--env-file PATH` | Use a different settings file |

## The CSV

`connections_<site>_<date>.csv`, one row per connection: Content Type, Name, Project Path, Owner, Connection Type, Server, Port, Username, Password Embedded, Authentication, Data Source Name, Content URL, Content ID, Connection ID.

**Reading it:**
- **`sqlproxy` in a workbook** means it uses a published data source (named in Data Source Name). The real database connection is on that data source.
- **`hyper`** is an extract, and `excel-direct` / `textscan` are files. Their usernames are internal, not people.
- **Password Embedded = No with a username** means viewers are prompted, or the site holds the credential some other way (OAuth, for instance).

## REST calls it makes

| Step | Call |
| --- | --- |
| Load projects, data sources, workbooks | `GET /projects`, `/datasources`, `/workbooks` (all pages) |
| Connections | `GET /sites/{site-id}/datasources/{id}/connections` and `/workbooks/{id}/connections`, once per item |

A site with N items takes N connection calls: on a test site with 165 items, about 18 seconds. An item that can't be read is reported and skipped; the file is still written, and the exit code is 1.

## What's deliberately not here

Changing the credentials. The original script could bulk-update a username and password across everything that used it. That's the riskiest operation in the old collection: the old values can't be read back, so one wrong password breaks every refresh at once. This entry is the safe half: find everything first. An update entry would need a test-one-first step and a record of what changed; it's noted for later.
