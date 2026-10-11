"""A thin wrapper around the Tableau REST API.

Every call is a plain ``requests`` call, so you can read exactly what goes over
the wire. The client handles:

* signing in and out with a personal access token
* detecting the newest REST API version the server supports
* the X-Tableau-Auth header and site-scoped URLs
* paging through list endpoints
* turning Tableau's error responses into readable exceptions

Use it as a context manager so the session is always signed out::

    with TableauClient(load_settings()) as client:
        for project in client.paginate("/projects", "projects", "project", action="List projects"):
            print(project["name"])
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterator, Optional
from urllib.parse import quote, urlencode

import requests

from . import __version__
from .config import Settings

# Used only if the server doesn't answer the serverinfo probe.
# 3.19 = Tableau Server 2023.1 / Tableau Cloud Mar 2023; covers everything this toolkit needs.
FALLBACK_API_VERSION = "3.19"
_PROBE_VERSIONS = ("3.19", "3.4", "2.4")

DEFAULT_TIMEOUT = (15, 120)  # (connect, read) seconds
UPLOAD_TIMEOUT = (15, 1800)  # publishing a big workbook can take a while
MAX_PAGE_SIZE = 1000  # the most the REST API returns per page


class TableauError(Exception):
    """A REST API call failed. ``status`` and ``code`` come from Tableau when available."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        summary: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.summary = summary
        self.detail = detail

    @classmethod
    def from_response(cls, response: requests.Response, action: str) -> "TableauError":
        code = summary = detail = None
        try:
            err = response.json().get("error", {})
            code, summary, detail = err.get("code"), err.get("summary"), err.get("detail")
        except ValueError:
            try:  # some endpoints answer errors in XML even when JSON was requested
                root = ET.fromstring(response.content)
                err = next((el for el in root.iter() if el.tag.endswith("error")), None)
                if err is not None:
                    code = err.get("code")
                    for child in err:
                        if child.tag.endswith("summary"):
                            summary = child.text
                        elif child.tag.endswith("detail"):
                            detail = child.text
            except ET.ParseError:
                detail = response.text[:300] or None

        message = f"{action} failed (HTTP {response.status_code}"
        message += f", Tableau error {code})" if code else ")"
        if summary:
            message += f": {summary}"
        if detail and detail != summary:
            message += f" - {detail}"
        if action == "Sign in" and response.status_code == 401:
            message += f"\n  {_SIGN_IN_HINT}"
        return cls(message, status=response.status_code, code=code, summary=summary, detail=detail)


_SIGN_IN_HINT = (
    "Check TABLEAU_PAT_NAME, TABLEAU_PAT_SECRET and TABLEAU_SITE_NAME in your .env, and that the "
    "token hasn't expired or been revoked (My Account Settings > Personal Access Tokens)."
)


class TableauClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.server = settings.server_url
        self.api_version: Optional[str] = settings.api_version
        self.product_version: Optional[str] = None
        self.site_id: Optional[str] = None
        self.site_content_url: str = settings.site_name
        self.user_id: Optional[str] = None
        self._token: Optional[str] = None

        self.session = requests.Session()
        self.session.verify = settings.verify_ssl
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": f"tableau-rest-api-starter-pack/{__version__}"}
        )

    # ------------------------------------------------------------ lifecycle

    def __enter__(self) -> "TableauClient":
        self.sign_in()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.sign_out()

    @property
    def api_url(self) -> str:
        return f"{self.server}/api/{self.api_version}"

    def site_url(self, path: str = "") -> str:
        if not self.site_id:
            raise TableauError("Not signed in.")
        return f"{self.api_url}/sites/{self.site_id}{path}"

    def detect_api_version(self) -> str:
        """Ask the server for the newest REST API version it supports.

        GET /api/{any-version}/serverinfo needs no sign-in and reports the
        server's own restApiVersion, so we probe with an old version we know exists.
        """
        for probe in _PROBE_VERSIONS:
            response = self._send("GET", f"{self.server}/api/{probe}/serverinfo", action="Contact server")
            if not response.ok:
                continue
            try:
                info = response.json().get("serverInfo", {})
            except ValueError:
                continue
            product = info.get("productVersion")
            self.product_version = product.get("value") if isinstance(product, dict) else product
            if info.get("restApiVersion"):
                return str(info["restApiVersion"])
        return FALLBACK_API_VERSION

    def sign_in(self) -> None:
        """POST /auth/signin with a personal access token."""
        if not self.api_version:
            self.api_version = self.detect_api_version()
        body = {
            "credentials": {
                "personalAccessTokenName": self.settings.pat_name,
                "personalAccessTokenSecret": self.settings.pat_secret,
                "site": {"contentUrl": self.settings.site_name},
            }
        }
        response = self.request("POST", f"{self.api_url}/auth/signin", action="Sign in", json=body)
        credentials = response.json()["credentials"]
        self._token = credentials["token"]
        self.site_id = credentials["site"]["id"]
        self.site_content_url = credentials["site"].get("contentUrl", self.settings.site_name)
        self.user_id = credentials["user"]["id"]
        self.session.headers["X-Tableau-Auth"] = self._token

    def sign_out(self) -> None:
        """POST /auth/signout. Never raises: it runs during cleanup."""
        if not self._token:
            return
        try:
            self.session.post(f"{self.api_url}/auth/signout", timeout=DEFAULT_TIMEOUT)
        except requests.RequestException:
            pass
        finally:
            self._token = None
            self.session.headers.pop("X-Tableau-Auth", None)

    # ------------------------------------------------------------ requests

    def request(self, method: str, path_or_url: str, *, action: str, **kwargs: Any) -> requests.Response:
        """Send a request and raise TableauError if it fails.

        ``path_or_url`` is either a full URL or a path relative to the signed-in
        site, e.g. "/projects" -> {server}/api/{version}/sites/{site-id}/projects.
        """
        url = path_or_url if path_or_url.startswith("http") else self.site_url(path_or_url)
        response = self._send(method, url, action=action, **kwargs)
        if not response.ok:
            raise TableauError.from_response(response, action)
        return response

    def get_json(self, path: str, *, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if params:
            # Encode spaces as %20 (not "+") so filter values like "name:eq:My Workbook" survive.
            path = f"{path}?{urlencode(params, quote_via=quote)}"
        response = self.request("GET", path, action=action)
        return response.json() if response.content else {}

    def paginate(
        self,
        path: str,
        collection: str,
        item: str,
        *,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        page_size: int = MAX_PAGE_SIZE,
    ) -> Iterator[Dict[str, Any]]:
        """Yield every item from a paged list endpoint.

        List responses look like {"pagination": {...}, "projects": {"project": [...]}},
        so ``collection`` is "projects" and ``item`` is "project".
        """
        page = 1
        while True:
            query = dict(params or {}, pageSize=page_size, pageNumber=page)
            data = self.get_json(path, action=action, params=query)
            items = (data.get(collection) or {}).get(item) or []
            yield from items
            total = int((data.get("pagination") or {}).get("totalAvailable", 0))
            # The server may cap pageSize below what we asked for, so count what came back.
            returned = int((data.get("pagination") or {}).get("pageSize", page_size))
            if not items or page * returned >= total:
                return
            page += 1

    def current_user_name(self) -> Optional[str]:
        """Display name of the signed-in user, or None if the lookup isn't allowed."""
        try:
            user = self.get_json(f"/users/{self.user_id}", action="Look up signed-in user").get("user", {})
        except TableauError:
            return None
        return user.get("fullName") or user.get("name")

    def _send(self, method: str, url: str, *, action: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        try:
            return self.session.request(method, url, **kwargs)
        except requests.exceptions.SSLError as exc:
            raise TableauError(
                f"{action} failed: certificate problem talking to {self.server}.\n"
                "  For Tableau Server with a self-signed certificate, set TABLEAU_VERIFY_SSL in .env.\n"
                f"  ({exc})"
            ) from None
        except requests.exceptions.Timeout:
            raise TableauError(f"{action} failed: {self.server} didn't respond in time.") from None
        except requests.RequestException as exc:
            raise TableauError(f"{action} failed: couldn't reach {self.server} ({exc}).") from None
