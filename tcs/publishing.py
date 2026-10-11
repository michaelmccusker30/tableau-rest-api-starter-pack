"""Publishing workbooks with the REST API.

Small files (64 MB or less) go up in one multipart/mixed request:

    POST /sites/{site-id}/workbooks?overwrite=true|false
        part 1  request_payload   <tsRequest><workbook name=...><project id=.../></workbook></tsRequest>
        part 2  tableau_workbook  the .twb/.twbx bytes

Bigger files use a chunked upload session:

    POST /sites/{site-id}/fileUploads                      -> uploadSessionId
    PUT  /sites/{site-id}/fileUploads/{uploadSessionId}    once per chunk (part name tableau_file)
    POST /sites/{site-id}/workbooks?uploadSessionId=...&workbookType=twbx&overwrite=...
        part 1  request_payload only
"""

from __future__ import annotations

import math
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .client import UPLOAD_TIMEOUT, TableauClient, TableauError
from .projects import Project

SINGLE_REQUEST_LIMIT = 64 * 1024 * 1024  # REST API limit for a one-shot publish
CHUNK_SIZE = 32 * 1024 * 1024  # each appended chunk must stay under 64 MB

Part = Tuple[str, Optional[str], str, bytes]  # (name, filename, content type, data)


@dataclass(frozen=True)
class PublishPlan:
    file: Path
    name: str
    project: Project
    project_path: str
    overwrite: bool = False
    show_tabs: bool = False

    @property
    def size(self) -> int:
        return self.file.stat().st_size

    @property
    def chunked(self) -> bool:
        return self.size > SINGLE_REQUEST_LIMIT

    @property
    def chunk_size(self) -> int:
        return CHUNK_SIZE

    @property
    def chunk_count(self) -> int:
        return max(1, math.ceil(self.size / CHUNK_SIZE))

    @property
    def workbook_type(self) -> str:
        return self.file.suffix.lower().lstrip(".")


def find_workbook(client: TableauClient, project: Project, name: str) -> Optional[Dict[str, Any]]:
    """Return the workbook called ``name`` in ``project``, or None.

    Tries the cheapest filter first. Tableau's filter syntax uses commas to
    separate expressions, so names containing commas fall back to broader queries.
    """
    target = name.casefold()
    filters: List[Optional[str]] = []
    if "," not in name:
        filters.append(f"name:eq:{name}")
    if "," not in project.name:
        filters.append(f"projectName:eq:{project.name}")
    filters.append(None)  # last resort: every workbook on the site

    for flt in filters:
        try:
            for wb in client.paginate(
                "/workbooks",
                "workbooks",
                "workbook",
                action="Check for an existing workbook",
                params={"filter": flt} if flt else None,
            ):
                if (wb.get("project") or {}).get("id") == project.id and (wb.get("name") or "").casefold() == target:
                    return wb
            return None
        except TableauError as exc:
            if exc.status == 400:  # filter not understood; try the next, broader query
                continue
            raise
    return None


def describe_existing(workbook: Optional[Dict[str, Any]]) -> str:
    """' (last updated 2026-09-12, owner Jane Doe)' for a workbook from find_workbook, or ''."""
    updated = ((workbook or {}).get("updatedAt") or "")[:10]
    owner = ((workbook or {}).get("owner") or {}).get("name")
    bits = [f"last updated {updated}" if updated else "", f"owner {owner}" if owner else ""]
    text = ", ".join(b for b in bits if b)
    return f" ({text})" if text else ""


def publish_workbook(
    client: TableauClient,
    plan: PublishPlan,
    progress: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Publish the workbook and return Tableau's description of it."""
    params: Dict[str, str] = {"overwrite": "true" if plan.overwrite else "false"}
    payload = _workbook_payload(plan)

    if plan.chunked:
        params["uploadSessionId"] = _upload_in_chunks(client, plan, progress)
        params["workbookType"] = plan.workbook_type
        parts: List[Part] = [("request_payload", None, "text/xml", payload)]
        progress("  Finishing publish...")
    else:
        parts = [
            ("request_payload", None, "text/xml", payload),
            ("tableau_workbook", _safe_filename(plan.file), "application/octet-stream", plan.file.read_bytes()),
        ]
        progress("  Uploading...")

    body, content_type = build_multipart(parts)
    response = client.request(
        "POST",
        "/workbooks",
        action="Publish workbook",
        params=params,
        data=body,
        headers={"Content-Type": content_type},
        timeout=UPLOAD_TIMEOUT,
    )
    return response.json().get("workbook", {})


def build_multipart(parts: List[Part]) -> Tuple[bytes, str]:
    """Build a multipart/mixed body, the format Tableau's publish endpoints expect."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, filename, content_type, data in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {content_type}\r\n\r\n".encode()
        body += data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/mixed; boundary={boundary}"


def _workbook_payload(plan: PublishPlan) -> bytes:
    request = ET.Element("tsRequest")
    workbook = ET.SubElement(
        request, "workbook", name=plan.name, showTabs="true" if plan.show_tabs else "false"
    )
    ET.SubElement(workbook, "project", id=plan.project.id)
    return ET.tostring(request, encoding="utf-8")


def _upload_in_chunks(client: TableauClient, plan: PublishPlan, progress: Callable[[str], None]) -> str:
    response = client.request("POST", "/fileUploads", action="Start chunked upload")
    upload_id = response.json()["fileUpload"]["uploadSessionId"]
    total, sent = plan.size, 0
    with plan.file.open("rb") as handle:
        for number in range(1, plan.chunk_count + 1):
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            body, content_type = build_multipart(
                [
                    ("request_payload", None, "text/xml", b""),
                    ("tableau_file", "file", "application/octet-stream", chunk),
                ]
            )
            client.request(
                "PUT",
                f"/fileUploads/{upload_id}",
                action=f"Upload part {number} of {plan.chunk_count}",
                data=body,
                headers={"Content-Type": content_type},
                timeout=UPLOAD_TIMEOUT,
            )
            sent += len(chunk)
            progress(f"  Uploaded part {number} of {plan.chunk_count} ({sent * 100 // total}%)")
    return upload_id


def _safe_filename(path: Path) -> str:
    """ASCII-only filename for the Content-Disposition header, extension preserved."""
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", path.stem).strip() or "workbook"
    return f"{stem}{path.suffix.lower()}"
