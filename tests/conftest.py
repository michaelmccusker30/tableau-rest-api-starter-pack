from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_tableau import PAT_NAME, PAT_SECRET, SITE, FakeTableau  # noqa: E402

ENV_KEYS = [
    "TABLEAU_SERVER_URL",
    "TABLEAU_SITE_NAME",
    "TABLEAU_PAT_NAME",
    "TABLEAU_PAT_SECRET",
    "TABLEAU_API_VERSION",
    "TABLEAU_VERIFY_SSL",
    "DEFAULT_WORKBOOK_DIR",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """load_dotenv writes into os.environ; start every test from a clean slate."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture
def fake():
    server = FakeTableau().start()
    yield server
    server.stop()


@pytest.fixture
def env_file(tmp_path, fake):
    path = tmp_path / ".env.test"
    path.write_text(
        f"TABLEAU_SERVER_URL={fake.url}\n"
        f"TABLEAU_SITE_NAME={SITE}\n"
        f"TABLEAU_PAT_NAME={PAT_NAME}\n"
        f"TABLEAU_PAT_SECRET={PAT_SECRET}\n"
        f"DEFAULT_WORKBOOK_DIR={tmp_path / 'workbooks'}\n"
    )
    return path


@pytest.fixture
def workbook(tmp_path):
    folder = tmp_path / "workbooks"
    (folder / "Archive").mkdir(parents=True)
    path = folder / "Sales Overview.twbx"
    path.write_bytes(b"PK\x03\x04 fake twbx bytes " * 100)
    return path


@pytest.fixture
def workbook_folder(tmp_path):
    """A folder tree to scan. Only the three visible .twbx files should be found:
    Finance/Budget.twbx, Finance/Q3/Forecast.twbx and Sales.twbx."""
    folder = tmp_path / "exports"
    for rel in [
        "Sales.twbx",
        "Finance/Budget.twbx",
        "Finance/Q3/Forecast.twbx",
        "Finance/notes.txt",
        ".hidden/Secret.twbx",
        "Old/~Temp.twbx",
        "Old/Legacy.twb",
    ]:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PK\x03\x04 fake twbx bytes " * 20)
    return folder


def load_script(relative_path):
    """Import a script by path. Entry file names start with a digit, so plain import won't do."""
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def handbuilt_script():
    return load_script("01-publishing-workbooks/1a_publish_workbook_handbuilt.py")


@pytest.fixture
def publish_script():
    return load_script("01-publishing-workbooks/1b_publish_workbook.py")


@pytest.fixture
def bulk_script():
    return load_script("01-publishing-workbooks/1c_bulk_publish_workbooks.py")


@pytest.fixture
def inventory_script():
    return load_script("02-content-inventory/02_content_inventory.py")


@pytest.fixture
def embed_script():
    return load_script("03-embed-urls/03_embed_urls.py")


@pytest.fixture
def move_script():
    return load_script("04-move-workbooks/04_move_workbooks.py")


@pytest.fixture
def people_script():
    return load_script("05-users-and-groups/05_users_and_groups.py")


@pytest.fixture
def download_script():
    return load_script("06-download-content/06_download_content.py")


@pytest.fixture
def conn_script():
    return load_script("07-connections/07_connections.py")


@pytest.fixture
def perm_script():
    return load_script("08-permissions/08_permissions.py")


@pytest.fixture
def groups_script():
    return load_script("09-groups-from-csv/09_groups_from_csv.py")


@pytest.fixture
def subs_script():
    return load_script("10-unlicensed-subscriptions/10_unlicensed_subscriptions.py")


@pytest.fixture
def retry_script():
    return load_script("11-retry-failed-refreshes/11_retry_failed_refreshes.py")


@pytest.fixture
def clock(monkeypatch):
    """A fake clock for tcs.pacing: sleeping advances it instantly. .sleeps records every pause."""
    from tcs import pacing

    class Clock:
        def __init__(self):
            self.t = 1_000_000.0
            self.sleeps = []

        def now(self):
            return self.t

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.t += seconds

    fake_clock = Clock()
    monkeypatch.setattr(pacing, "_now", fake_clock.now)
    monkeypatch.setattr(pacing, "_sleep", fake_clock.sleep)
    return fake_clock


@pytest.fixture
def site_content(fake):
    """Workbooks spread over the fake's project tree, plus two outside it, and their views."""
    budget = fake.add_workbook(
        "Budget", "p-fin-rep-mon", owner_id="user-2", tags={"tag": [{"label": "kpi"}, {"label": "q3"}]}
    )
    plan = fake.add_workbook("Plan", "p-fin-plan")
    campaigns = fake.add_workbook("Campaigns", "p-mkt", showTabs="false")
    fake.add_workbook("Home", "p-default")  # no views
    scratch = fake.add_workbook("Scratch", "p-personal-1", location_type="PersonalSpace")
    fake.add_workbook("Secret", "p-hidden")
    fake.add_workbook("Leftover", "p-mkt", owner_id="user-3")  # its owner has lost their licence
    fake.add_view(budget, "Overview", 12)
    fake.add_view(budget, "Detail", 3)
    fake.add_view(plan, "Plan", 0)
    fake.add_view(campaigns, "Campaign ROI", 40)
    fake.add_view(scratch, "Draft", 1)
    return fake


@pytest.fixture
def answers(monkeypatch):
    """Feed scripted answers to input(). Call it with the list of answers."""

    def feed(values):
        queue = list(values)

        def fake_input(prompt=""):
            print(prompt, end="")
            if not queue:
                raise EOFError
            value = queue.pop(0)
            print(value)
            return value

        monkeypatch.setattr("builtins.input", fake_input)

    return feed
