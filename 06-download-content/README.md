# 06 · Download content: back up workbooks and data sources

Download workbooks and published data sources into a folder that mirrors your project tree, with a manifest listing every item and its ID. Use it as a backup before a big change, as a snapshot before a migration, or to take a copy to republish with [entry 01](../01-publishing-workbooks/README.md).

It never changes anything on the site.

```bash
python 06-download-content/06_download_content.py
```


## The flow

1. **What:** workbooks, data sources, or both.
2. **Scope:** every project, all except some branches, or only some (as in entries 02 and 03).
3. **Pick** one, some or all of the items in scope (`3`, `1,4,7`, `2-5`, `a`), listed by project path.
4. **Review** the count, the size as Tableau reports it, whether extracts are included, and the folder. Then confirm.
5. **Download,** one item at a time, with a summary at the end.

```text
output/download_acme_2026-10-10_1432/
├── manifest.csv
├── Finance/
│   ├── Ledger.tdsx
│   └── Reporting/
│       └── Monthly/
│           └── Budget.twbx
└── Marketing/
    └── Live Sales.tds
```

| Flag | Effect |
| --- | --- |
| `--type workbooks\|datasources\|all` | What to download |
| `--include "A/B"` / `--exclude "A/B"` | Scope, as in entry 02. Repeatable |
| `--select LIST` | Which items, numbered as listed |
| `--no-extract` | Leave extract data out: definitions only, much smaller |
| `--output-dir PATH` | Where the run's folder goes (default: `./output`) |
| `-y`, `--yes` | No prompts. Downloads everything of `--type` (default `all`) in scope, unless `--select` |
| `--dry-run` | The review only |
| `--env-file PATH` | Use a different settings file |

```bash
# A nightly definitions-only backup of everything
python 06-download-content/06_download_content.py --type all --no-extract --yes
```

## The manifest

`manifest.csv` in the run's folder: Type, Name, Project Path, File (relative to the folder), Size (bytes), Extract Included, Status (`Downloaded`, `Failed`, `Not reached`), Detail, ID.

The **ID** is what makes a backup useful later. Names aren't unique across projects; IDs are. To compare a backup with the site, or republish into the right place, match on IDs.

## Safeguards

| Situation | What happens |
| --- | --- |
| A download is interrupted | It was being written as `.part`; that's removed, so no half-file looks finished |
| The API names a data source's file `.twb` or `.twbx` | Saved as `.tds` / `.tdsx`, so it reopens as a data source |
| A name has characters a file name can't hold (`: ? / \|`...) | They become `_` |
| One item fails (permissions, say) | Recorded in the manifest; the rest carry on. Exit code 1 |
| Two runs in the same minute | The second folder gets `_2` |

## REST calls it makes

| Step | Call |
| --- | --- |
| Load projects, workbooks, data sources | `GET /projects`, `/workbooks`, `/datasources` (all pages) |
| Download a workbook | `GET /sites/{site-id}/workbooks/{id}/content` (`?includeExtract=false` with `--no-extract`) |
| Download a data source | `GET /sites/{site-id}/datasources/{id}/content` (same option) |

The file comes back as the response body, with its name in the `Content-Disposition` header. It's streamed to disk 1 MB at a time, so a large extract doesn't have to fit in memory.
