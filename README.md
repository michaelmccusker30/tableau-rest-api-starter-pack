# Tableau REST API Starter Pack

Python scripts for Tableau Cloud and Tableau Server admins and developers, built on the Tableau REST API. Each one is a complete tool you can run today, and a worked example of the API you can learn from and build on.

- **Eleven entries**, from publishing and moving workbooks to permissions, user audits, backups and retrying failed refreshes.
- **One shared toolkit** (`tcs`) for sign-in, paging, project paths, prompts and reports, so every script reads the same way.
- **Skill files** that capture what this pack learned about the REST API, so you (or your AI assistant) write the next script faster and get it right more often.
- **Offline tests** for everything, against an in-memory fake of the REST API.

The scripts were written with AI (Claude) and tested by hand against a real Tableau Cloud site.

## What's in the pack

### 01 · The story: publishing workbooks, three ways

[`01-publishing-workbooks/`](01-publishing-workbooks/) is the one entry shown evolving. It does the same job three times:

| | Version | What it shows |
| --- | --- | --- |
| **1a** | Hand-built by Michael McCusker while learning the API | Simple and effective: about 130 lines on Tableau Server Client |
| **1b** | Built with AI | Robust: direct REST calls, pickers, a name-clash check, a review screen and a dry run |
| **1c** | Built with AI | Supercharged: a bulk uploader that scans a folder tree and publishes one, some or all of what it finds |

### 02 onward · The kit

Each entry is a single, full-strength tool. **Read-only** entries never change your site. The others change it only after showing you every change.

| Entry | What it does | Changes your site? |
| --- | --- | --- |
| [**02 · Content inventory**](02-content-inventory/) | Every workbook with its full project path, owner (with site role and last sign-in) and view count, plus the project tree, as CSV | No |
| [**03 · Embed URLs**](03-embed-urls/) | For every view: its link, an iframe URL and `<iframe>`, an Embedding API v3 snippet, and its view count, as CSV and a self-contained HTML page | No |
| [**04 · Move workbooks**](04-move-workbooks/) | Move workbooks between projects, picked from a list or from a CSV (02's inventory works as-is), with a log that `--undo` replays in reverse | Yes |
| [**05 · Users and groups**](05-users-and-groups/) | Every user and group, who's in what, and who needs a look: unlicensed, never signed in, stale, in no group | No |
| [**06 · Download content**](06-download-content/) | Back up workbooks and data sources into folders that mirror your project tree, with a manifest of IDs | No |
| [**07 · Connections**](07-connections/) | Every data connection: type, server, username, embedded password or not. "What uses this service account?" | No |
| [**08 · Permissions**](08-permissions/) | Report every explicit rule with names; grant a template to users (from a CSV) or groups, revoke, or restore from the automatic backup | When granting, revoking or restoring |
| [**09 · Groups from a CSV**](09-groups-from-csv/) | Add users to groups from a CSV, creating groups as needed. Never removes anyone | Yes |
| [**10 · Unlicensed subscriptions**](10-unlicensed-subscriptions/) | Back up, then delete, subscriptions owned by unlicensed users | Yes (deletes) |
| [**11 · Retry failed refreshes**](11-retry-failed-refreshes/) | Find last night's failed extract refreshes and rerun them, drip-fed to your edition's hourly limit, then check how they turned out | Yes (starts refreshes) |

Each entry's README covers what it's for, how to run it, every flag, the files it writes, and the REST calls it makes.

### One way of working, in every script

- **Interactive by default, with a flag for every prompt.** Run it bare and answer the questions, or pass flags and `--yes` for a scheduled job. With `--yes` it never asks; it stops instead.
- **A review screen before anything happens,** and `--dry-run` to stop there.
- **Before changing anything,** a record (log, backup or results file) is written first, so you can see or undo what happened. Changes go one item at a time, a failure is recorded rather than stopping the run, and the exit code is 1 if anything failed.
- **Reports** go to `./output/` with timestamped names that never overwrite. They open cleanly in Excel, with accented names intact, and cells that look like formulas are written as text.
- **`--env-file`** on every script, to switch sites.

## Setup

Requires Python 3.9 or newer.

```bash
git clone https://github.com/michaelmccusker30/tableau-rest-api-starter-pack.git
cd tableau-rest-api-starter-pack
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # requests and python-dotenv; requirements-dev.txt adds pytest and Tableau Server Client (for 1a)
cp .env.example .env               # then fill in .env
```

Then try something read-only:

```bash
python 02-content-inventory/02_content_inventory.py --dry-run
```

### Your settings: the `.env` file

Every script reads its connection settings from a `.env` file. It's a plain text file of `KEY=value` lines that stays on your machine. `.env.example` is the template: copy it to `.env` and fill it in.

| Key | What to put there |
| --- | --- |
| `TABLEAU_SERVER_URL` | Your pod or server, e.g. `https://10ax.online.tableau.com`. Pasting a full browser URL is fine; the site name is picked out of it |
| `TABLEAU_SITE_NAME` | The site's content URL: the part after `/site/` in the browser's address bar, not its display name. Leave blank for the Default site on Tableau Server |
| `TABLEAU_PAT_NAME` / `TABLEAU_PAT_SECRET` | A personal access token, created under **My Account Settings > Personal Access Tokens** |
| `TABLEAU_API_VERSION` | Optional. Blank auto-detects the newest version your server supports |
| `TABLEAU_VERIFY_SSL` | Optional. `true` for Tableau Cloud. For Tableau Server with a self-signed certificate, a CA bundle path (or `false`) |
| `DEFAULT_WORKBOOK_DIR` | Optional. Where the workbook pickers in 01 open. Defaults to `~/Documents/My Tableau Repository/Workbooks` |

**Keeping it safe:**
- `.env` and any `.env.*` variant are git-ignored; only `.env.example` is committed.
- Scripts check your settings before connecting, and they never print the token.
- If a token is ever exposed, revoke it in Tableau and create a new one.

**Several sites?** Keep one file per site (`.env.work`, `.env.personal`) and pick one with `--env-file .env.work`. Values in the file win over variables already set in your shell, so switching is reliable. You can also keep the file outside the repo entirely: `--env-file ~/secrets/tableau.env`.

**What account to use:** reports work for any user and show what that account can see. Listing users, jobs or subscriptions, and changing groups, need a **site administrator** (entries 05, 09, 10, 11). Changing permissions needs an administrator or the project's leader or owner.

## Skill files: the pack's institutional knowledge

[`skills/`](skills/) holds two skill files. Each is a plain Markdown file that captures how things are done here and what's been learned the hard way:

| Skill | What it knows |
| --- | --- |
| [`tableau-rest-api`](skills/tableau-rest-api/SKILL.md) | How to write REST calls that work first time. It covers the `fields` and `filter` options that are supported (and the ones that return 400), paging, IDs versus names, Personal Space, view URLs and embedding, publishing and downloads, permissions (three rule sets, XML, templates, locked projects), jobs and refresh rate limits by edition, connections, what each error code usually means, and how an entry in this pack is built |
| [`tableau-env-config`](skills/tableau-env-config/SKILL.md) | How settings and credentials work: every key, how values are checked, `.env` quoting gotchas, and adding a new setting safely |

**Why it helps:**
- **Institutional knowledge, written down.** Hard-won lessons ("`finishCode` isn't filterable", "ShareView isn't in the View template", "a refresh that's queued hasn't succeeded") stop living in one person's head or an old chat.
- **Write new scripts faster.** An assistant that has read the skill starts from the toolkit and the right endpoint, with the right body and fields, instead of guessing.
- **Get it right more often.** The checks that each script needed (match by ID, back up before changing, back off on 429) become the default rather than a lesson relearned.

**Using them with Claude Code:** Claude Code loads skills from `.claude/skills/`. Link the folder once from the repo root:

```bash
mkdir -p .claude && ln -s ../skills .claude/skills     # Windows: copy skills\ into .claude\skills\
```

Then ask for what you need, e.g. "add an entry that lists stale workbooks with no views in 90 days", and the skill is read when it's relevant. **With other assistants,** attach or paste the skill file at the start of the conversation. **Without an assistant,** it's still a good page to read before writing a call.

Keep the skills current: when a script teaches you something new about the API, add it to `tableau-rest-api/SKILL.md` in the same change.

## The shared toolkit: `tcs`

```python
from tcs.config import load_settings
from tcs.client import TableauClient

with TableauClient(load_settings()) as client:
    for wb in client.paginate("/workbooks", "workbooks", "workbook", action="List workbooks"):
        print(wb["name"])
```

| Module | What it does |
| --- | --- |
| `config` | Loads and checks `.env`, with fix-it messages for common setup mistakes |
| `client` | REST session: version detection, sign-in and sign-out, paging, Tableau's error codes |
| `projects` | The project tree (paths, depths, branches), the tiered project picker, `--project` lookup |
| `content` | Listing workbooks, views, data sources, users, groups, connections and subscriptions; placing items in the project tree; view links |
| `scope` | The "every project / all except / only these" choice, by prompt or `--include` / `--exclude` |
| `permissions` | Reading rules, Tableau's templates, and changing only the difference |
| `pacing` | An hourly call budget that spreads calls out and backs off on HTTP 429 |
| `batch` | One change per item, failures recorded rather than fatal, a summary and exit code |
| `reports` | CSV and HTML reports that never overwrite; reading CSVs saved from Excel |
| `files` | Workbook and folder pickers, folder scans, safe file names |
| `publishing` | Name-clash check, single-request and chunked publishing |
| `ui` | Prompts, numbered menus, multi-select lists (`3 / 1,4,7 / 2-5 / a`), colours |

`client.request()` raises `TableauError` with Tableau's own error code and message, and the `with` block always signs out.

## Layout

```
tableau-rest-api-starter-pack/
├── 01-publishing-workbooks/        the story: 1a, 1b, 1c
├── 02-content-inventory/ ... 11-retry-failed-refreshes/
│                                   one folder per entry: the script and its README
├── tcs/                            shared toolkit
├── skills/                         skill files for you and your AI assistant
├── tests/                          offline tests against a fake REST API
├── output/                         reports land here by default (git-ignored)
├── .env.example                    settings template
└── LICENSE                         MIT
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run each script end to end against a small in-memory fake of the REST API (`tests/fake_tableau.py`), so they need no Tableau site or credentials. The fake answers in JSON for `tcs` and in XML for Tableau Server Client. It only returns optional data (owner details, usage) when a script asks for it, so the tests check that the requests are right. The 1a tests are skipped if `tableauserverclient` isn't installed.

## Troubleshooting

| Message | Usually means |
| --- | --- |
| `No .env file found` | Run `cp .env.example .env` and fill it in, or pass `--env-file` |
| `Sign in failed (HTTP 401 ...)` | Wrong token name or secret, the site's display name instead of its content URL, or an expired or revoked token |
| `certificate problem` | Tableau Server with a self-signed certificate: set `TABLEAU_VERIFY_SSL` |
| `... failed (HTTP 403 ...)` | Your account isn't allowed to do this. Some entries need a site administrator; changing a workbook in a locked project is refused |
| `... failed (HTTP 429 ...)` | Too many refreshes started this hour. Entry 11 paces itself; see its README for the limits by edition |
| `.twb ... doesn't package any data` | Save as `.twbx` from Tableau Desktop so extracts and local files go with it |

## Licence

MIT. See [LICENSE](LICENSE).
