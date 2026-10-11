"""A tiny in-memory stand-in for the Tableau REST API, for offline tests.

It implements just enough of the endpoints the toolkit calls (serverinfo,
sign in/out, users, projects, workbooks, fileUploads) to exercise real HTTP
round-trips, including multipart/mixed publishing, pagination, the
owner field expressions and view usage statistics.

It answers in JSON when the request asks for it (tcs always sends
Accept: application/json) and in XML otherwise, which is what Tableau Server
Client expects. That lets 01-publishing-workbooks/1a_publish_workbook_handbuilt.py run against it too.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

API = "3.29"
PAT_NAME, PAT_SECRET, SITE = "test-pat", "test-secret", "acme"
SITE_ID, USER_ID, TOKEN = "site-1", "user-1", "token-abc"


class FakeTableau:
    def __init__(self, page_cap: int = 2) -> None:
        self.page_cap = page_cap  # force pagination even with pageSize=1000
        self.projects: List[Dict[str, Any]] = [
            {"id": "p-default", "name": "Default", "contentPermissions": "ManagedByOwner"},
            {"id": "p-fin", "name": "Finance", "contentPermissions": "LockedToProject", "description": "Money"},
            {"id": "p-fin-rep", "name": "Reporting", "parentProjectId": "p-fin"},
            {"id": "p-fin-rep-mon", "name": "Monthly", "parentProjectId": "p-fin-rep"},
            {"id": "p-fin-plan", "name": "Planning", "parentProjectId": "p-fin"},
            {"id": "p-mkt", "name": "Marketing"},
            {"id": "p-ext", "name": "External Assets Default Project"},
        ]
        for project in self.projects:
            project["owner"] = {"id": "user-2" if project["id"] == "p-fin" else USER_ID}
        self.workbooks: List[Dict[str, Any]] = []
        self.views: List[Dict[str, Any]] = []
        self.datasources: List[Dict[str, Any]] = []
        self.connections: Dict[str, List[Dict[str, Any]]] = {}  # item id -> its connections
        self.groups: List[Dict[str, Any]] = [{"id": "g-all", "name": "All Users", "domain": {"name": "local"}}]
        self.members: Dict[str, List[str]] = {"g-all": []}  # group id -> user ids
        self.permissions: Dict[str, List[Dict[str, Any]]] = {}  # "projects/p-fin", "workbooks/wb-1", "projects/p-fin/default" -> rules
        self.subscriptions: List[Dict[str, Any]] = []
        self.files: Dict[str, Tuple[str, bytes]] = {}  # item id -> (download file name, bytes)
        self.fail: Dict[Tuple[str, str], int] = {}  # (method, path after the site) -> HTTP status to fail with
        self.token = TOKEN  # expire_session() swaps it, as if the session timed out
        self.jobs: List[Dict[str, Any]] = []  # Query Jobs rows
        self.job_details: Dict[str, Dict[str, Any]] = {}  # Get Job answers, by job id
        self.tasks: List[Dict[str, Any]] = []  # extract refresh tasks
        self.refresh_script: Dict[str, List[int]] = {}  # content or task id -> statuses to answer, in order
        self.refreshes: List[str] = []  # refresh / runNow paths that started a job
        self.users: List[Dict[str, Any]] = [
            {"id": USER_ID, "name": "test@example.com", "fullName": "Test User", "email": "test@example.com",
             "siteRole": "SiteAdministratorCreator", "lastLogin": "2026-10-09T08:00:00Z"},
            {"id": "user-2", "name": "jo@example.com", "fullName": "Jo Müller", "email": "jo@example.com",
             "siteRole": "Creator", "lastLogin": "2026-10-01T12:30:00Z"},
            {"id": "user-3", "name": "sam@example.com", "fullName": "Sam Gone", "email": "sam@example.com",
             "siteRole": "Unlicensed", "lastLogin": "2025-01-15T17:45:00Z"},
        ]
        self.members["g-all"] = [u["id"] for u in self.users]
        self.honor_fields = True  # False acts like a server that ignores ?fields=
        self.users_forbidden = False  # True acts like a non-admin listing users
        self.uploads: Dict[str, bytearray] = {}
        self.requests: List[Tuple[str, str]] = []  # (method, path?query)
        self.publishes: List[Dict[str, Any]] = []
        self.signed_out = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeTableau":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def add_workbook(
        self, name: str, project_id: str, owner_id: str = USER_ID, location_type: str = "Project", **extra: Any
    ) -> Dict[str, Any]:
        project_name = next((p["name"] for p in self.projects if p["id"] == project_id), "")
        owner_name = "Test User" if owner_id == USER_ID else next(u["name"] for u in self.users if u["id"] == owner_id)
        wb = {
            "id": f"wb-{uuid.uuid4().hex[:6]}",
            "name": name,
            "contentUrl": re.sub(r"\W+", "", name),
            "project": {"id": project_id, "name": project_name},
            "location": {"id": project_id, "type": location_type, "name": project_name},
            "owner": {"id": owner_id, "name": owner_name},
            "createdAt": "2026-09-01T09:00:00Z",
            "updatedAt": "2026-09-12T18:20:11Z",
            "size": "1",
            "showTabs": "true",
            "tags": {},
            **extra,
        }
        self.workbooks.append(wb)
        return wb

    def add_datasource(
        self, name: str, project_id: str, owner_id: str = USER_ID, kind: str = "sqlserver", **extra: Any
    ) -> Dict[str, Any]:
        project_name = next((p["name"] for p in self.projects if p["id"] == project_id), "")
        owner = next(u for u in self.users if u["id"] == owner_id)
        ds = {
            "id": f"ds-{uuid.uuid4().hex[:6]}",
            "name": name,
            "type": kind,
            "contentUrl": re.sub(r"\W+", "", name),
            "project": {"id": project_id, "name": project_name},
            "owner": {"id": owner_id, "name": owner["name"]},
            "hasExtracts": False,
            "isCertified": False,
            "size": "1",
            "updatedAt": "2026-09-20T10:00:00Z",
            "webpageUrl": f"{self.url}/#/site/{SITE}/datasources/{len(self.datasources) + 1}",
            **extra,
        }
        self.datasources.append(ds)
        return ds

    def add_connection(self, item: Dict[str, Any], kind: str, server: str = "", user: str = "", **extra: Any) -> None:
        self.connections.setdefault(item["id"], []).append(
            {"id": f"c-{uuid.uuid4().hex[:6]}", "type": kind, "serverAddress": server, "userName": user,
             "embedPassword": bool(user), **extra}
        )

    def add_group(self, name: str, member_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        group = {"id": f"g-{uuid.uuid4().hex[:6]}", "name": name, "domain": {"name": "local"}}
        self.groups.append(group)
        self.members[group["id"]] = list(member_ids or [])
        return group

    def add_user(self, name: str, full_name: str, site_role: str = "Viewer", last_login: Optional[str] = None) -> Dict[str, Any]:
        user = {"id": f"user-{uuid.uuid4().hex[:6]}", "name": name, "fullName": full_name, "email": name,
                "siteRole": site_role, "authSetting": "ServerDefault"}
        if last_login:
            user["lastLogin"] = last_login
        self.users.append(user)
        self.members["g-all"].append(user["id"])
        return user

    def add_rule(self, key: str, grantee: Tuple[str, str], capabilities: Dict[str, str]) -> None:
        """grantee is ("group", id) or ("user", id); capabilities maps name -> Allow/Deny."""
        self.permissions.setdefault(key, []).append(
            {grantee[0]: {"id": grantee[1]},
             "capabilities": {"capability": [{"name": n, "mode": m} for n, m in capabilities.items()]}}
        )

    def add_subscription(self, user_id: str, content: Dict[str, Any], kind: str = "Workbook", subject: str = "Daily") -> Dict[str, Any]:
        sub = {"id": f"sub-{uuid.uuid4().hex[:6]}", "subject": subject, "attachImage": True, "attachPdf": False,
               "suspended": False, "content": {"id": content["id"], "type": kind, "name": content["name"]},
               "schedule": {"id": "sch-1", "name": "Weekday mornings"}, "user": {"id": user_id}}
        self.subscriptions.append(sub)
        return sub

    def expire_session(self) -> None:
        self.token = f"token-{uuid.uuid4().hex[:6]}"

    def add_failed_job(
        self, item: Dict[str, Any], kind: str = "datasource", hours_ago: float = 2, error: str = "Connection refused",
        job_type: str = "refresh_extracts",
    ) -> Dict[str, Any]:
        """A failed background job for ``item`` (a data source or workbook), and its Get Job record."""
        from datetime import datetime, timedelta, timezone

        when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        job = {"id": f"job-{uuid.uuid4().hex[:6]}", "jobType": job_type, "status": "Failed", "createdAt": when,
               "title": f"Refresh extracts for {item['name']}"}
        self.jobs.append(job)
        detail: Dict[str, Any] = {"id": job["id"], "type": "RefreshExtract", "finishCode": "1", "createdAt": when,
                                  "completedAt": when, "statusNotes": {"statusNote": [{"type": "error", "text": error}]}}
        if job_type != "run_flow":
            detail["extractRefreshJob"] = {kind: {"id": item["id"], "name": item["name"]}}
        self.job_details[job["id"]] = detail
        return job

    def add_task(self, item: Dict[str, Any], kind: str = "datasource", failures_in_a_row: int = 0) -> Dict[str, Any]:
        task = {"extractRefresh": {"id": f"task-{uuid.uuid4().hex[:6]}", "priority": 50, "type": "FullRefresh",
                                   "consecutiveFailedCount": failures_in_a_row, kind: {"id": item["id"]}}}
        self.tasks.append(task)
        return task["extractRefresh"]

    def add_view(self, workbook: Dict[str, Any], name: str, views: int = 0) -> Dict[str, Any]:
        """A view (sheet or dashboard) in ``workbook``, viewed ``views`` times."""
        view = {
            "id": f"v-{uuid.uuid4().hex[:6]}",
            "name": name,
            "contentUrl": f"{workbook['contentUrl']}/sheets/{re.sub(r'[^A-Za-z0-9]+', '', name)}",
            "viewUrlName": re.sub(r"[^A-Za-z0-9]+", "", name),
            "workbook": {"id": workbook["id"]},
            "owner": {"id": workbook["owner"]["id"]},
            "project": {"id": workbook["project"]["id"]},
            "location": dict(workbook["location"]),
            "usage": {"totalViewCount": str(views)},
        }
        self.views.append(view)
        return view

    # ------------------------------------------------------------------ http

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: Optional[Dict[str, Any]] = None) -> None:
                xml_mode = "application/json" not in (self.headers.get("Accept") or "")
                if payload is None:
                    body = b""
                elif xml_mode:
                    body = to_xml(payload)
                else:
                    body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/xml" if xml_mode else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status: int, code: str, summary: str, detail: str = "") -> None:
                self._send(status, {"error": {"code": code, "summary": summary, "detail": detail}})

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _route(self, method: str) -> None:
                fake.requests.append((method, self.path))
                url = urlparse(self.path)
                path, query = url.path, parse_qs(url.query)
                body = self._body()

                if method == "GET" and re.fullmatch(r"/api/[\d.]+/serverinfo", path, re.IGNORECASE):
                    return self._send(200, {"serverInfo": {"productVersion": {"value": "2026.2.0", "build": "x"}, "restApiVersion": API}})
                # TSC signs in on its default version (2.4) before switching to the server's own.
                if re.fullmatch(r"/api/[\d.]+/auth/signin", path) and method == "POST":
                    creds = credentials_from_body(body)
                    ok = (creds.get("personalAccessTokenName"), creds.get("personalAccessTokenSecret"), creds["site"]["contentUrl"]) == (PAT_NAME, PAT_SECRET, SITE)
                    if not ok:
                        return self._error(401, "401001", "Signin Error", "Error signing in to Tableau Server")
                    return self._send(200, {"credentials": {"token": fake.token, "site": {"id": SITE_ID, "contentUrl": SITE}, "user": {"id": USER_ID}}})
                if path == f"/api/{API}/auth/signout" and method == "POST":
                    fake.signed_out = True
                    return self._send(204)

                prefix = f"/api/{API}/sites/{SITE_ID}"
                if not path.startswith(prefix):
                    return self._error(404, "404000", "Not found", path)
                if self.headers.get("X-Tableau-Auth") != fake.token:
                    return self._error(401, "401002", "Unauthorized Access")
                sub = path[len(prefix):]

                failure = fake.fail.get((method, sub))
                if failure:
                    return self._error(failure, f"{failure}000", "Injected failure", f"{method} {sub}")
                if method == "GET" and sub == f"/users/{USER_ID}":
                    return self._send(200, {"user": {"id": USER_ID, "name": "test@example.com", "fullName": "Test User"}})
                match = re.fullmatch(r"/users/([\w-]+)", sub)
                if method == "GET" and match:
                    user = next((u for u in fake.users if u["id"] == match.group(1)), None)
                    return self._send(200, {"user": user}) if user else self._error(404, "404002", "Not found", "user")
                if method == "GET" and sub == "/projects":
                    items = fake.projects
                    fields = (query.get("fields") or [""])[0]
                    if fake.honor_fields and "owner.fullName" in fields:
                        items = [fake._with_owner_details(p, fields) for p in items]
                    return self._send(200, fake._page(query, items, "projects", "project"))
                if method == "GET" and sub == "/workbooks":
                    items = fake.workbooks
                    flt = (query.get("filter") or [None])[0]
                    if flt:
                        field, _op, value = flt.split(":", 2)
                        if field == "name":
                            items = [w for w in items if w["name"] == value]
                        elif field == "projectName":
                            ids = {p["id"] for p in fake.projects if p["name"] == value}
                            items = [w for w in items if w["project"]["id"] in ids]
                    fields = (query.get("fields") or [""])[0]
                    if fake.honor_fields and "owner.fullName" in fields:
                        items = [fake._with_owner_details(w, fields) for w in items]
                    return self._send(200, fake._page(query, items, "workbooks", "workbook"))
                if method == "GET" and sub == "/views":
                    items = fake.views
                    if (query.get("includeUsageStatistics") or [""])[0] != "true":
                        items = [{k: v for k, v in view.items() if k != "usage"} for view in items]
                    return self._send(200, fake._page(query, items, "views", "view"))
                if method == "GET" and sub == "/users":
                    if fake.users_forbidden:
                        return self._error(403, "403014", "Forbidden", "Only administrators can list users.")
                    return self._send(200, fake._page(query, fake.users, "users", "user"))
                if method == "POST" and sub == "/fileUploads":
                    upload_id = f"up-{uuid.uuid4().hex[:6]}"
                    fake.uploads[upload_id] = bytearray()
                    return self._send(201, {"fileUpload": {"uploadSessionId": upload_id, "fileSize": "0"}})
                match = re.fullmatch(r"/fileUploads/([\w-]+)", sub)
                if method == "PUT" and match:
                    parts = parse_multipart(self.headers["Content-Type"], body)
                    fake.uploads[match.group(1)] += parts["tableau_file"]["data"]
                    return self._send(200, {"fileUpload": {"uploadSessionId": match.group(1), "fileSize": str(len(fake.uploads[match.group(1)]))}})
                if method == "POST" and sub == "/workbooks":
                    return fake._publish(self, query, body)
                return fake._more_routes(self, method, sub, query, body)

            def do_GET(self) -> None:
                self._route("GET")

            def do_POST(self) -> None:
                self._route("POST")

            def do_PUT(self) -> None:
                self._route("PUT")

            def do_DELETE(self) -> None:
                self._route("DELETE")

        return Handler

    def _page(self, query: Dict[str, List[str]], items: List[Dict[str, Any]], collection: str, item: str) -> Dict[str, Any]:
        size = min(int(query.get("pageSize", ["100"])[0]), self.page_cap)
        number = int(query.get("pageNumber", ["1"])[0])
        chunk = items[(number - 1) * size: number * size]
        return {
            "pagination": {"pageNumber": str(number), "pageSize": str(size), "totalAvailable": str(len(items))},
            collection: {item: chunk} if chunk else {},
        }

    def _more_routes(self, handler: Any, method: str, sub: str, query: Dict[str, List[str]], body: bytes) -> None:
        """Endpoints for entries 04 onward."""
        payload = json.loads(body) if body.strip().startswith(b"{") else {}
        fields = (query.get("fields") or [""])[0]

        if method == "GET" and sub == "/datasources":
            items = self.datasources
            if self.honor_fields and "owner.fullName" in fields:
                items = [self._with_owner_details(d, fields) for d in items]
            return handler._send(200, self._page(query, items, "datasources", "datasource"))
        match = re.fullmatch(r"/(workbooks|datasources)/([\w-]+)/connections", sub)
        if method == "GET" and match:
            return handler._send(200, {"connections": {"connection": self.connections.get(match.group(2), [])}})
        match = re.fullmatch(r"/(workbooks|datasources)/([\w-]+)/content", sub)
        if method == "GET" and match:
            name, data = self.files.get(match.group(2), (None, b""))
            if name is None:
                return handler._error(404, "404000", "Not found", sub)
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            part = "tableau_workbook" if match.group(1) == "workbooks" else "tableau_datasource"
            handler.send_header("Content-Disposition", f'name="{part}"; filename="{name}"')
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return None
        match = re.fullmatch(r"/workbooks/([\w-]+)", sub)
        if method == "PUT" and match:
            wb = next((w for w in self.workbooks if w["id"] == match.group(1)), None)
            if wb is None:
                return handler._error(404, "404006", "Not found", "workbook")
            target = payload.get("workbook", {}).get("project", {}).get("id")
            if target:
                if not any(p["id"] == target for p in self.projects):
                    return handler._error(404, "404005", "Not found", "project")
                if any(w["name"] == wb["name"] and w["project"]["id"] == target and w is not wb for w in self.workbooks):
                    return handler._error(409, "409010", "Resource Conflict", "A workbook with that name exists in the project.")
                name = next(p["name"] for p in self.projects if p["id"] == target)
                wb["project"] = {"id": target, "name": name}
                wb["location"] = {"id": target, "type": "Project", "name": name}
            return handler._send(200, {"workbook": wb})

        if method == "GET" and sub == "/groups":
            items = [dict(g, userCount=str(len(self.members.get(g["id"], [])))) if "userCount" in fields else g for g in self.groups]
            return handler._send(200, self._page(query, items, "groups", "group"))
        if method == "POST" and sub == "/groups":
            name = payload.get("group", {}).get("name", "")
            if any(g["name"].lower() == name.lower() for g in self.groups):
                return handler._error(409, "409009", "Resource Conflict", "Group name conflict.")
            return handler._send(201, {"group": self.add_group(name)})
        match = re.fullmatch(r"/groups/([\w-]+)/users", sub)
        if match and match.group(1) not in self.members:
            return handler._error(404, "404011", "Not found", "group")
        if method == "GET" and match:
            ids = self.members[match.group(1)]
            users = [u for u in self.users if u["id"] in ids]
            return handler._send(200, self._page(query, users, "users", "user"))
        if method == "POST" and match:
            user_id = payload.get("user", {}).get("id")
            if user_id in self.members[match.group(1)]:
                return handler._error(409, "409011", "Resource Conflict", "User already in group.")
            self.members[match.group(1)].append(user_id)
            return handler._send(200, {"user": next(u for u in self.users if u["id"] == user_id)})

        match = re.fullmatch(
            r"/(projects|workbooks)/([\w-]+)/(permissions|default-permissions/workbooks)"
            r"(?:/(users|groups)/([\w-]+)/(\w+)/(Allow|Deny))?", sub)
        if match:
            kind, item_id, which, gtype, gid, cap, mode = match.groups()
            key = f"{kind}/{item_id}" + ("/default" if which.startswith("default") else "")
            rules = self.permissions.setdefault(key, [])
            if method in ("PUT", "DELETE") and kind == "workbooks":
                wb = next((w for w in self.workbooks if w["id"] == item_id), None)
                project = next((p for p in self.projects if wb and p["id"] == wb["project"]["id"]), {})
                if project.get("contentPermissions") == "LockedToProject":
                    return handler._error(403, "403004", "Forbidden", "Permissions are locked to the project.")
            if method == "GET" and not gtype:
                owner = {"projects": "project", "workbooks": "workbook"}[kind]
                return handler._send(200, {"permissions": {owner: {"id": item_id}, "granteeCapabilities": rules}})
            if method == "DELETE" and gtype:
                entry = next((e for e in rules if (e.get(gtype[:-1]) or {}).get("id") == gid), None)
                caps = (entry or {}).get("capabilities", {}).get("capability", [])
                hit = next((c for c in caps if c["name"] == cap and c["mode"] == mode), None)
                if not hit:
                    return handler._error(404, "404003", "Not found", "permission")
                caps.remove(hit)
                if not caps:
                    rules.remove(entry)
                return handler._send(204)
            if method == "PUT" and not gtype:
                import xml.etree.ElementTree as ET

                for grant in ET.fromstring(body).iter("granteeCapabilities"):
                    grantee = grant.find("user") if grant.find("user") is not None else grant.find("group")
                    gkind, gid = grantee.tag, grantee.get("id")
                    entry = next((e for e in rules if (e.get(gkind) or {}).get("id") == gid), None)
                    if entry is None:
                        entry = {gkind: {"id": gid}, "capabilities": {"capability": []}}
                        rules.append(entry)
                    caps = entry["capabilities"]["capability"]
                    for c in grant.iter("capability"):
                        name, mode = c.get("name"), c.get("mode")
                        if any(x["name"] == name and x["mode"] != mode for x in caps):
                            return handler._error(409, "409003", "Conflict", f"{name} is already set the other way.")
                        if not any(x["name"] == name for x in caps):
                            caps.append({"name": name, "mode": mode})
                self.permission_puts = getattr(self, "permission_puts", 0) + 1
                return handler._send(200, {"permissions": {"granteeCapabilities": rules}})

        if method == "GET" and sub == "/jobs":
            items = self.jobs
            for clause in (query.get("filter") or [""])[0].split(","):
                field, _, value = clause.partition(":eq:") if ":eq:" in clause else clause.partition(":gt:")
                if field == "status":
                    items = [j for j in items if j["status"] == value]
                elif field == "createdAt":
                    items = [j for j in items if j["createdAt"] > value]
            return handler._send(200, self._page(query, items, "backgroundJobs", "backgroundJob"))
        match = re.fullmatch(r"/jobs/([\w-]+)", sub)
        if method == "GET" and match:
            job = self.job_details.get(match.group(1))
            return handler._send(200, {"job": job}) if job else handler._error(404, "404012", "Not found", "job")
        if method == "GET" and sub == "/tasks/extractRefreshes":
            return handler._send(200, {"tasks": {"task": self.tasks}} if self.tasks else {"tasks": {}})
        match = re.fullmatch(r"/(?:datasources|workbooks)/([\w-]+)/refresh|/tasks/extractRefreshes/([\w-]+)/runNow", sub)
        if method == "POST" and match:
            key = match.group(1) or match.group(2)
            script = self.refresh_script.get(key) or []
            status = script.pop(0) if script else 202
            if status == 429:
                return handler._error(429, "429000", "Too Many Requests",
                                      "Too many requests for 'api.rest.refresh_extracts'. Please retry after 3 second(s).")
            if status == 409:
                return handler._error(409, "409093", "Resource Conflict", "A refresh job is already queued.")
            if status == 401:  # the session timed out mid-run
                self.expire_session()
                return handler._error(401, "401002", "Unauthorized Access", "Invalid authentication credentials.")
            if status >= 400:
                return handler._error(status, f"{status}000", "Refused", sub)
            job_id = f"job-{uuid.uuid4().hex[:6]}"
            self.refreshes.append(sub)
            self.job_details[job_id] = {"id": job_id, "type": "RefreshExtract", "progress": "0"}  # running
            return handler._send(202, {"job": {"id": job_id, "mode": "Asynchronous", "type": "RefreshExtract"}})

        if method == "GET" and sub == "/subscriptions":
            return handler._send(200, self._page(query, self.subscriptions, "subscriptions", "subscription"))
        match = re.fullmatch(r"/subscriptions/([\w-]+)", sub)
        if method == "DELETE" and match:
            before = len(self.subscriptions)
            self.subscriptions = [x for x in self.subscriptions if x["id"] != match.group(1)]
            return handler._send(204) if len(self.subscriptions) < before else handler._error(404, "404025", "Not found", "subscription")
        return handler._error(404, "404000", "Not found", sub)

    def _with_owner_details(self, item: Dict[str, Any], fields: str) -> Dict[str, Any]:
        """What ?fields=_default_,owner.name,owner.fullName,... adds to a workbook or project."""
        user = next((u for u in self.users if u["id"] == item["owner"]["id"]), {})
        owner = dict(item["owner"], fullName=user.get("fullName", ""))
        for key in ("name", "email", "siteRole", "lastLogin"):
            if f"owner.{key}," in fields + ",":
                owner[key] = user.get(key, "")
        return dict(item, owner=owner)

    def _publish(self, handler: Any, query: Dict[str, List[str]], body: bytes) -> None:
        import xml.etree.ElementTree as ET

        parts = parse_multipart(handler.headers["Content-Type"], body)
        request = ET.fromstring(parts["request_payload"]["data"])
        wb_el = request.find("workbook")
        name, project_id = wb_el.get("name"), wb_el.find("project").get("id")
        overwrite = query.get("overwrite", ["false"])[0] == "true"
        upload_id = query.get("uploadSessionId", [None])[0]
        if upload_id:
            data, filename = bytes(self.uploads[upload_id]), f"chunked.{query['workbookType'][0]}"
        else:
            data, filename = parts["tableau_workbook"]["data"], parts["tableau_workbook"]["filename"]

        existing = next((w for w in self.workbooks if w["name"] == name and w["project"]["id"] == project_id), None)
        if existing and not overwrite:
            return handler._error(409, "409004", "Resource Conflict", "A workbook with that name already exists in the project.")
        if existing:
            self.workbooks.remove(existing)
        wb = self.add_workbook(name, project_id)
        wb["webpageUrl"] = f"{self.url}/#/site/{SITE}/workbooks/{wb['id']}"
        self.publishes.append(
            {"name": name, "project_id": project_id, "overwrite": overwrite, "show_tabs": wb_el.get("showTabs"),
             "data": data, "filename": filename, "chunked": bool(upload_id)}
        )
        handler._send(201, {"workbook": wb})


def credentials_from_body(body: bytes) -> Dict[str, Any]:
    """The sign-in request's credentials, from tcs's JSON or TSC's XML body."""
    if body.lstrip().startswith(b"{"):
        return json.loads(body)["credentials"]
    import xml.etree.ElementTree as ET

    creds = ET.fromstring(body).find("credentials")
    return {**creds.attrib, "site": creds.find("site").attrib}


def to_xml(payload: Dict[str, Any]) -> bytes:
    """Render a JSON-shaped response the way Tableau does in XML.

    Scalars become attributes, dicts become child elements and lists become
    repeated elements: {"projects": {"project": [...]}} -> <projects><project/>...</projects>.
    serverInfo and error carry text content, so they're written out by hand.
    """
    from xml.sax.saxutils import escape, quoteattr

    def element(tag: str, value: Any) -> str:
        if isinstance(value, list):
            return "".join(element(tag, item) for item in value)
        attrs = "".join(f" {k}={quoteattr(str(v))}" for k, v in value.items() if not isinstance(v, (dict, list)))
        children = "".join(element(k, v) for k, v in value.items() if isinstance(v, (dict, list)))
        return f"<{tag}{attrs}>{children}</{tag}>"

    if "serverInfo" in payload:
        info = payload["serverInfo"]
        inner = (
            f'<serverInfo><productVersion build="{info["productVersion"]["build"]}">'
            f'{info["productVersion"]["value"]}</productVersion>'
            f'<restApiVersion>{info["restApiVersion"]}</restApiVersion></serverInfo>'
        )
    elif "error" in payload:
        err = payload["error"]
        inner = (
            f'<error code="{err["code"]}"><summary>{escape(err["summary"])}</summary>'
            f'<detail>{escape(err.get("detail", ""))}</detail></error>'
        )
    else:
        inner = "".join(element(k, v) for k, v in payload.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><tsResponse xmlns="http://tableau.com/api">{inner}</tsResponse>'.encode()


def parse_multipart(content_type: str, body: bytes) -> Dict[str, Dict[str, Any]]:
    assert content_type.startswith("multipart/mixed"), content_type
    boundary = content_type.split("boundary=")[1].encode()
    parts: Dict[str, Dict[str, Any]] = {}
    for raw in body.split(b"--" + boundary)[1:]:
        if raw.startswith(b"--"):
            break
        head, _, data = raw.lstrip(b"\r\n").partition(b"\r\n\r\n")
        headers = head.decode()
        name = re.search(r'name="([^"]+)"', headers).group(1)
        filename = re.search(r'filename="([^"]+)"', headers)
        parts[name] = {"data": data[:-2], "filename": unquote(filename.group(1)) if filename else None}
    return parts
