---
name: tableau-env-config
description: How Tableau credentials and settings are handled with .env files in the Tableau REST API Starter Pack (and any Python Tableau REST project that follows it). Use when creating or editing .env or .env.example, adding or renaming a setting, loading config in a script, switching sites with --env-file, writing config tests, or debugging sign-in and config errors.
---

# Tableau settings with .env files

Every script in this repo gets its Tableau connection settings the same way: from a git-ignored `.env` file, loaded and checked by `tcs/config.py`, and described for everyone else by the committed `.env.example`. This skill is the reference for that method. If the code changes, update this file in the same commit.

## Rules

1. **Secrets only in `.env` files.** Never hard-code a token, never print or log one, never commit one.
2. **`.env.example` is the contract.** Every key a script reads is listed there, with a comment and a placeholder or blank value. It is the only env file in git.
3. **One loader.** Scripts call `tcs.config.load_settings()`. They never call `os.getenv` for Tableau settings or run `load_dotenv` themselves.
4. **Shared key names.** Use the names below in every Tableau project, so one set of credentials works everywhere.
5. **Fail early and clearly.** Bad config stops the script before any network call, with a message that names the key and how to fix it. Messages never contain the value.

## The files

| File | In git? | Purpose |
| --- | --- | --- |
| `.env.example` | **Yes** | Shareable template: every key, with comments and placeholders |
| `.env` | No | Your real settings, loaded by default |
| `.env.<name>` (e.g. `.env.work`, `.env.personal`) | No | Settings for another site, chosen with `--env-file .env.work` |

`.gitignore` must contain these lines, in this order:

```gitignore
.env
.env.*
!.env.example
```

To set up: `cp .env.example .env`, then fill in `.env`. The default file is `.env` **in the repo root**, found from `tcs/config.py`'s location, so scripts work from any working directory.

**Keeping credentials outside the repo** is a good habit if you also commit to it: put the real `.env` in a private folder elsewhere and pass `--env-file /path/to/.env`. A script run without a settings file stops at "No .env file found" before any network call. That's the intended fail-safe.

## The keys

| Key | Required | What it is | Example |
| --- | --- | --- | --- |
| `TABLEAU_SERVER_URL` | Yes | Pod or server base URL | `https://10ax.online.tableau.com` |
| `TABLEAU_SITE_NAME` | Cloud: yes. Server: blank means the Default site | The site's **content URL** (the part after `/site/` in the browser), not its display name | `acme-analytics` |
| `TABLEAU_PAT_NAME` | Yes | Personal access token name | `publish-scripts` |
| `TABLEAU_PAT_SECRET` | Yes | Personal access token secret | *(from Tableau, shown once)* |
| `TABLEAU_API_VERSION` | No | Pin a REST API version. Blank means auto-detect through `serverinfo` | `3.29` |
| `TABLEAU_VERIFY_SSL` | No | `true` (default), `false`, or the path to a CA bundle for Tableau Server with a self-signed certificate | `true` |
| `DEFAULT_WORKBOOK_DIR` | No | Where the workbook picker opens. `~` and `$VARS` are expanded | `~/Documents/Tableau` |

Tokens are created under **My Account Settings > Personal Access Tokens** on Tableau Cloud or Server.

## How loading works

`load_settings(env_file=None) -> Settings` in `tcs/config.py`:

1. Picks the file: `env_file` if one is passed (an error if it doesn't exist), otherwise `<repo>/.env`. If there's no file and `TABLEAU_SERVER_URL` isn't set in the shell, it raises `ConfigError` telling the user to `cp .env.example .env`.
2. Loads the file with `load_dotenv(path, override=True)`. **Values in the file beat variables already set in the shell**, so `--env-file` reliably switches sites. Keys the file leaves out fall back to the shell, which lets CI run with no file.
3. Checks and cleans each value (next section) and returns a frozen `Settings` dataclass: `server_url`, `site_name`, `pat_name`, `pat_secret` (kept out of `repr`), `api_version`, `verify_ssl`, `default_workbook_dir` and `env_file`. It also has the helpers `is_tableau_cloud` and `site_label` (`"Default"` when the site is blank).

### Checks and clean-up

| Check | Behavior |
| --- | --- |
| A required key is missing or blank | `ConfigError: TABLEAU_PAT_NAME is missing. Add it to .env (see .env.example).` |
| A placeholder value was left in (`your-pod`, `your-site-name`, `your-pat-name`, `your-pat-secret`) | `ConfigError` saying the key still has the placeholder from `.env.example` |
| `TABLEAU_SERVER_URL` | Reduced to `scheme://host`. `https://` is added if missing. A pasted browser URL such as `.../#/site/acme/home` works, and the site is taken from it when `TABLEAU_SITE_NAME` is blank |
| `TABLEAU_SITE_NAME` | Accepts the bare content URL or a pasted URL containing `/site/<name>` |
| Tableau Cloud (host ends in `online.tableau.com`) with a blank site | `ConfigError`. Cloud has no Default site |
| `TABLEAU_API_VERSION` | Must look like `3.29` |
| `TABLEAU_VERIFY_SSL` | `true/1/yes/blank` mean True, `false/0/no` mean False, anything else must be an existing file path |

## Using it in a script

Every script takes `--env-file` and passes it straight through:

```python
from tcs.config import ConfigError, load_settings
from tcs.client import TableauClient, TableauError

parser.add_argument("--env-file", metavar="PATH", help="settings file to use instead of .env")
...
try:
    settings = load_settings(args.env_file)        # before any prompt or network call
    with TableauClient(settings) as client:        # signs in with the PAT, always signs out
        ...
except (ConfigError, TableauError) as exc:
    ui.error(str(exc)); return 1
```

Show the site, never the token: `ui.detail(f"Site: {settings.site_label} on {settings.server_url}")`.

### With Tableau Server Client

`01-publishing-workbooks/1a_publish_workbook_handbuilt.py` uses TSC instead of `tcs.client`, but it still gets its settings from `load_settings()`. Map the fields like this, including `verify_ssl`, so self-signed Tableau Server certificates behave the same as in the other scripts:

```python
settings = load_settings(env_file)
auth = TSC.PersonalAccessTokenAuth(settings.pat_name, settings.pat_secret, site_id=settings.site_name)
server = TSC.Server(settings.server_url, use_server_version=True, http_options={"verify": settings.verify_ssl})
server.auth.sign_in(auth)
```

## Writing values in .env

Checked against python-dotenv 1.2.3:

| You write | Value read |
| --- | --- |
| `KEY=My Tableau Repository` | `My Tableau Repository` (spaces are fine, trailing spaces are trimmed) |
| `KEY=abc#def` | `abc#def` |
| `KEY=abc #def` | `abc`: **space then `#` starts a comment** |
| `KEY=abc${HOME}` or `KEY="abc ${HOME}"` | `${HOME}` is **expanded** |
| `KEY='abc #def ${HOME}'` | Taken literally |

**Wrap a secret in single quotes if it contains a space, ` #` or `${`.** Typical Tableau PAT secrets (letters, digits, `+ / = :`) are safe unquoted.

## Keeping secrets safe

- `Settings.pat_secret` is `field(repr=False)`, so it never appears in tracebacks or debug prints. Keep it that way for any new secret field.
- Error messages name the key, never the value. `TableauError` messages come from Tableau's response, which never includes the token.
- Never echo the token, write it to output files, or send it anywhere except `POST /auth/signin`.
- Tests use fake values only (`test-pat`, `test-secret`). Never copy a real `.env` into a test or fixture.
- If a secret is ever committed: revoke the token in Tableau first, then clean the history.

## Adding or changing a setting

1. Add it to `.env.example` with a comment that says what it is, what an example value looks like, and what blank means. Use a blank value for optional keys and an obvious placeholder for required ones (add any new placeholder to `_PLACEHOLDERS`).
2. Add a field to `Settings`. Parse and check it in `load_settings`, using `get(key, required=...)`. Fail with `ConfigError` and a fix-it message.
3. Add a row to the README's credentials table.
4. Add a test in `tests/test_tcs.py`: write a temporary `.env` in `tmp_path`, call `load_settings(path)`, and assert the result or the `ConfigError`. Also add the key to `ENV_KEYS` in `tests/conftest.py` so the shell can't leak into tests.
5. **Update this skill** (the key table, plus the checks table if relevant).

When renaming a key, accept the old name for a while with a warning, and note the rename in the changelog or README.

## Testing config

- The autouse fixture `clean_env` in `tests/conftest.py` deletes every `ENV_KEYS` variable and sets `NO_COLOR=1`, because `load_dotenv` writes into `os.environ` and would otherwise leak between tests.
- The `env_file` fixture writes `.env.test` pointing at the in-memory fake server (`tests/fake_tableau.py`) with fake credentials. End-to-end tests pass it as `--env-file`.
- `test_placeholder_values_are_rejected` loads the real `.env.example`, so the template and the placeholder checks can't drift apart.

## Using the method in another project

For a Tableau project outside this repo: copy the four required keys and their comments from `.env.example`, use the same three `.gitignore` lines, and load the file once at start-up with `load_dotenv(path, override=True)`. Check the values before any network call. Better still, add the script to this repo as a numbered entry folder and reuse `tcs`.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `No .env file found` | `.env` was never created. Run `cp .env.example .env` |
| `... still has the placeholder value` | `.env` was copied but not filled in |
| `Sign in failed (HTTP 401, Tableau error 401001)` | Wrong token name or secret; the site's display name used instead of its content URL; or the token has expired or been revoked |
| A key looks set but the value is wrong | An unquoted ` #` cut it short, or a `${...}` was expanded. See "Writing values in .env" |
| The value from the shell is ignored | Expected: the file wins. Remove the key from the file to use the shell's value |
| `certificate problem` | Tableau Server with a self-signed certificate. Set `TABLEAU_VERIFY_SSL` to your CA bundle path |
| A key is ignored entirely | It's misspelled (for example `ETABLEAU_SERVER_URL`). Compare against `.env.example` |
