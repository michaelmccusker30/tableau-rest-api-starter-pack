"""Load and check connection settings from a .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# Values copied straight from .env.example that were never filled in.
_PLACEHOLDERS = {"your-pod", "your-site-name", "your-pat-name", "your-pat-secret"}


class ConfigError(Exception):
    """Settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    server_url: str
    site_name: str  # site content URL; "" means the Default site on Tableau Server
    pat_name: str
    pat_secret: str = field(repr=False)
    api_version: Optional[str] = None
    verify_ssl: Union[bool, str] = True
    default_workbook_dir: Optional[Path] = None
    env_file: Optional[Path] = None

    @property
    def is_tableau_cloud(self) -> bool:
        return (urlparse(self.server_url).hostname or "").endswith("online.tableau.com")

    @property
    def site_label(self) -> str:
        return self.site_name or "Default"


def load_settings(env_file: Optional[Union[str, Path]] = None) -> Settings:
    """Read settings from ``env_file`` (default: .env in the project root).

    Values in the file win over variables already set in your shell, so
    ``--env-file .env.work`` reliably switches sites. Anything the file leaves
    out falls back to the shell environment, which lets CI jobs skip the file.
    """
    path = Path(env_file).expanduser() if env_file else DEFAULT_ENV_FILE
    if path.is_file():
        load_dotenv(path, override=True)
    elif env_file:
        raise ConfigError(f"Env file not found: {path}")
    elif not os.getenv("TABLEAU_SERVER_URL"):
        raise ConfigError(
            f"No .env file found at {path}.\n"
            "  Copy the template and fill in your values:  cp .env.example .env"
        )

    def get(key: str, required: bool = True) -> str:
        value = (os.getenv(key) or "").strip()
        if required and not value:
            raise ConfigError(f"{key} is missing. Add it to {path.name} (see .env.example).")
        if value and any(p in value for p in _PLACEHOLDERS):
            raise ConfigError(f"{key} still has the placeholder value from .env.example. Add your real value to {path.name}.")
        return value

    server_url, site_from_url = normalize_server_url(get("TABLEAU_SERVER_URL"))
    site_name = normalize_site_name(get("TABLEAU_SITE_NAME", required=False)) or site_from_url or ""

    settings = Settings(
        server_url=server_url,
        site_name=site_name,
        pat_name=get("TABLEAU_PAT_NAME"),
        pat_secret=get("TABLEAU_PAT_SECRET"),
        api_version=get("TABLEAU_API_VERSION", required=False) or None,
        verify_ssl=_parse_verify(get("TABLEAU_VERIFY_SSL", required=False)),
        default_workbook_dir=_optional_path(get("DEFAULT_WORKBOOK_DIR", required=False)),
        env_file=path if path.is_file() else None,
    )
    if settings.is_tableau_cloud and not settings.site_name:
        raise ConfigError(
            "TABLEAU_SITE_NAME is required for Tableau Cloud. It's the part after /site/ "
            "in your browser's address bar."
        )
    if settings.api_version and not re.fullmatch(r"\d+\.\d+", settings.api_version):
        raise ConfigError(f"TABLEAU_API_VERSION should look like 3.29, not {settings.api_version!r}.")
    return settings


def normalize_server_url(raw: str) -> Tuple[str, Optional[str]]:
    """Reduce a pasted URL to scheme://host and pull out a site name if it has one.

    "https://10ax.online.tableau.com/#/site/acme/home" -> ("https://10ax.online.tableau.com", "acme")
    """
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise ConfigError(f"TABLEAU_SERVER_URL doesn't look like a URL: {raw!r}")
    match = re.search(r"/site/([^/?#]+)", f"{parsed.path}#{parsed.fragment}")
    return f"{parsed.scheme}://{parsed.netloc}", (match.group(1) if match else None)


def normalize_site_name(raw: str) -> str:
    """Accept either the bare content URL or a pasted URL containing /site/<name>."""
    match = re.search(r"/site/([^/?#]+)", raw)
    return match.group(1) if match else raw.strip("/ ")


def _parse_verify(raw: str) -> Union[bool, str]:
    value = raw.strip()
    if value.lower() in ("", "true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    path = Path(value).expanduser()
    if not path.is_file():
        raise ConfigError(f"TABLEAU_VERIFY_SSL should be true, false or a CA bundle file path. File not found: {path}")
    return str(path)


def _optional_path(raw: str) -> Optional[Path]:
    return Path(os.path.expandvars(raw)).expanduser() if raw else None
