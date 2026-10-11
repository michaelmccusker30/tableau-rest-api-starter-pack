"""Permission rules: read them, compare them, and change only the difference.

A rule is one grantee (a user or group), one capability and one mode, e.g.
(group "Finance", ExportData, Deny). Tableau stores them per item: on a
project, on a project's default rules for new workbooks, and on a workbook.

Changing permissions here always works the same way: read the item's current
rules, work out the rules it should have, then delete what shouldn't be there
and add what's missing. Grant, revoke and restore are just three ways of saying
what "should have" is.

    read     GET    .../permissions
    add      PUT    .../permissions               (XML body, one per item)
    delete   DELETE .../permissions/{users|groups}/{id}/{capability}/{mode}
"""

from __future__ import annotations

from typing import Dict, Iterable, List, NamedTuple, Set, Tuple
from xml.sax.saxutils import quoteattr

from .client import TableauClient

PROJECT, PROJECT_DEFAULT, WORKBOOK = "Project", "Project default for workbooks", "Workbook"
ALLOW, DENY = "Allow", "Deny"

# Workbook capabilities, as the REST API names them.
CAPABILITIES = [
    "Read", "Filter", "ViewComments", "AddComment", "ExportImage", "ExportData",
    "ShareView", "ViewUnderlyingData", "WebAuthoring", "ExportXml", "Write",
    "ChangeHierarchy", "Delete", "ChangePermissions",
]

# Tableau's workbook templates, each including the one before. Newer capabilities
# (Run Explain Data, API access...) can be named with --capability.
_VIEW = ["Read", "Filter", "ViewComments", "AddComment", "ExportImage", "ExportData"]
_EXPLORE = _VIEW + ["ShareView", "ViewUnderlyingData", "WebAuthoring"]
_PUBLISH = _EXPLORE + ["ExportXml", "Write"]
_ADMINISTER = _PUBLISH + ["ChangeHierarchy", "Delete", "ChangePermissions"]
TEMPLATES: Dict[str, List[str]] = {"view": _VIEW, "explore": _EXPLORE, "publish": _PUBLISH, "administer": _ADMINISTER}


class Rule(NamedTuple):
    grantee_type: str  # "user" or "group"
    grantee_id: str
    capability: str
    mode: str  # Allow or Deny


def base_path(kind: str, item_id: str) -> str:
    return {
        PROJECT: f"/projects/{item_id}/permissions",
        PROJECT_DEFAULT: f"/projects/{item_id}/default-permissions/workbooks",
        WORKBOOK: f"/workbooks/{item_id}/permissions",
    }[kind]


def read_rules(client: TableauClient, kind: str, item_id: str) -> Set[Rule]:
    data = client.get_json(base_path(kind, item_id), action="Read permissions")
    rules: Set[Rule] = set()
    for entry in (data.get("permissions") or {}).get("granteeCapabilities") or []:
        grantee_type = "group" if "group" in entry else "user"
        grantee_id = (entry.get(grantee_type) or {}).get("id", "")
        for capability in (entry.get("capabilities") or {}).get("capability") or []:
            rules.add(Rule(grantee_type, grantee_id, capability.get("name", ""), capability.get("mode", "")))
    return rules


def plan(current: Set[Rule], desired: Set[Rule]) -> Tuple[List[Rule], List[Rule]]:
    """(to delete, to add), sorted for a stable review."""
    return sorted(current - desired), sorted(desired - current)


def granted(current: Set[Rule], grantees: Iterable[Tuple[str, str]], capabilities: Dict[str, str]) -> Set[Rule]:
    """Current rules plus ``capabilities`` for each grantee. A capability set the other way is replaced."""
    desired = set(current)
    for grantee_type, grantee_id in grantees:
        for name, mode in capabilities.items():
            desired = {r for r in desired if not (r.grantee_id == grantee_id and r.capability == name)}
            desired.add(Rule(grantee_type, grantee_id, name, mode))
    return desired


def revoked(current: Set[Rule], grantee_ids: Set[str], only: Iterable[str] = ()) -> Set[Rule]:
    """Current rules without the grantees' rules (all of them, or only the named capabilities)."""
    names = set(only)
    return {r for r in current if not (r.grantee_id in grantee_ids and (not names or r.capability in names))}


def apply(client: TableauClient, kind: str, item_id: str, deletes: List[Rule], adds: List[Rule]) -> None:
    """Deletes first (so an opposite mode is gone before its replacement arrives), then one add."""
    base = base_path(kind, item_id)
    for rule in deletes:
        client.request(
            "DELETE",
            f"{base}/{rule.grantee_type}s/{rule.grantee_id}/{rule.capability}/{rule.mode}",
            action="Delete permission",
        )
    if adds:
        client.request(
            "PUT", base, action="Add permissions", data=_xml(adds), headers={"Content-Type": "application/xml"}
        )


def _xml(rules: List[Rule]) -> bytes:
    by_grantee: Dict[Tuple[str, str], List[Rule]] = {}
    for rule in rules:
        by_grantee.setdefault((rule.grantee_type, rule.grantee_id), []).append(rule)
    entries = "".join(
        f"<granteeCapabilities><{gtype} id={quoteattr(gid)}/><capabilities>"
        + "".join(f"<capability name={quoteattr(r.capability)} mode={quoteattr(r.mode)}/>" for r in group)
        + "</capabilities></granteeCapabilities>"
        for (gtype, gid), group in by_grantee.items()
    )
    return f"<tsRequest><permissions>{entries}</permissions></tsRequest>".encode()
