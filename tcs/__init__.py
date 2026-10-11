"""tcs: shared building blocks for the Tableau REST API Starter Pack.

Every robust entry in the pack (1b, 1c and everything from 02 on) reuses these modules:

    config      load and check settings from .env
    client      REST API session: sign in/out, version detection, paging, errors
    projects    the site's project tree, the tiered project picker, --project lookup
    files       workbook and folder pickers, folder scans
    publishing  name check, single-request and chunked publishing
    content     listing workbooks, views, data sources, users, groups, connections, subscriptions;
                placing items in the project tree; view links
    scope       every project / all except some branches / only some branches
    reports     CSV and HTML reports: safe file names, Excel-friendly encoding and cells; reading CSVs
    batch       one change per item, failures recorded, a summary and exit code
    pacing      an hourly call budget that backs off on HTTP 429
    permissions read rules, templates, and change only the difference
    ui          terminal prompts, menus, multi-select lists and colors
"""

__version__ = "0.1.0"
