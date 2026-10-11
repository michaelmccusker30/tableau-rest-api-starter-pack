"""Listing content on the site (workbooks with their owners, views with their usage) and placing it in the project tree."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List

from .client import TableauClient, TableauError
from .config import Settings
from .projects import ProjectTree

PERSONAL_SPACE = "Personal space"
HIDDEN_PROJECT = "Project not visible to you"

# Query Workbooks returns only the owner's id and user name by default. A field
# expression asks for more of the owner in the same request, which saves one
# user lookup per owner (or one pass over every user on the site).
WORKBOOK_FIELDS = "_default_,owner.name,owner.fullName,owner.email,owner.siteRole,owner.lastLogin"


def list_workbooks(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/workbooks, every page, with each owner's name, email, site role and last sign-in.

    You get the workbooks your account can see: everything for a site admin,
    less for anyone else. If the server ignores the field expression, owners
    are filled in from the site's user list instead.
    """
    workbooks = list(
        client.paginate(
            "/workbooks", "workbooks", "workbook", action="List workbooks", params={"fields": WORKBOOK_FIELDS}
        )
    )
    if workbooks and not any("fullName" in (wb.get("owner") or {}) for wb in workbooks):
        _fill_owners_from_users(client, workbooks)
    return workbooks


def _fill_owners_from_users(client: TableauClient, workbooks: List[Dict[str, Any]]) -> None:
    try:
        users = {u["id"]: u for u in client.paginate("/users", "users", "user", action="List users")}
    except TableauError:
        return  # non-admins may not be allowed to list users; owners keep their user names
    for wb in workbooks:
        owner = wb.get("owner") or {}
        user = users.get(owner.get("id"))
        if user:
            for key in ("fullName", "email", "siteRole", "lastLogin"):
                owner.setdefault(key, user.get(key, ""))


def list_views(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/views?includeUsageStatistics=true, every page.

    Each view carries usage.totalViewCount. That's the cheap way to view counts:
    one call per 1,000 views, rather than one per workbook.
    """
    return list(
        client.paginate(
            "/views", "views", "view", action="List views", params={"includeUsageStatistics": "true"}
        )
    )


def total_views(view: Dict[str, Any]) -> int:
    try:
        return int((view.get("usage") or {}).get("totalViewCount") or 0)
    except (TypeError, ValueError):
        return 0


def views_by_workbook(views: List[Dict[str, Any]]) -> Counter:
    """Total views per workbook id: the sum over the workbook's views."""
    counts: Counter = Counter()
    for view in views:
        counts[(view.get("workbook") or {}).get("id", "")] += total_views(view)
    return counts


@dataclass(frozen=True)
class Placement:
    """Where a workbook or view sits, for reports."""

    project_id: str
    visible: bool  # in the project tree your account can see
    path: str  # "Finance / Reporting", or "(Personal space)"
    top_level: str
    project: str
    location: str  # "Project", PERSONAL_SPACE or HIDDEN_PROJECT


def placement(tree: ProjectTree, item: Dict[str, Any]) -> Placement:
    """Place a workbook or view from a list call in the project tree.

    Items outside the tree are labelled, never dropped: Personal Space on
    Tableau Cloud, or a project your permissions hide.
    """
    project = item.get("project") or {}
    project_id = project.get("id", "")
    if project_id in tree.by_id:
        lineage = tree.lineage(project_id)
        return Placement(project_id, True, tree.path(project_id), lineage[0].name, lineage[-1].name, "Project")
    personal = (item.get("location") or {}).get("type") == "PersonalSpace"
    location = PERSONAL_SPACE if personal else HIDDEN_PROJECT
    return Placement(project_id, False, f"({location})", "", project.get("name", ""), location)


def view_url(settings: Settings, view: Dict[str, Any]) -> str:
    """The view's link, as Share > Copy Link gives it: {server}/t/{site}/views/{workbook}/{view}.

    The API's contentUrl is "Workbook/sheets/View"; the link drops "sheets".
    The Default site on Tableau Server has no /t/{site} part.
    """
    path = (view.get("contentUrl") or "").replace("/sheets/", "/", 1)
    site = f"/t/{settings.site_name}" if settings.site_name else ""
    return f"{settings.server_url}{site}/views/{path}"


DATASOURCE_FIELDS = "_default_,owner.name,owner.fullName,owner.email"


def list_datasources(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/datasources, every page, with owner names and emails."""
    return list(
        client.paginate(
            "/datasources", "datasources", "datasource", action="List data sources", params={"fields": DATASOURCE_FIELDS}
        )
    )


def list_users(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/users, every page. The default response already has
    each user's email, full name, site role, last sign-in and authentication."""
    return list(client.paginate("/users", "users", "user", action="List users"))


def list_groups(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/groups, every page, with each group's member count."""
    return list(
        client.paginate("/groups", "groups", "group", action="List groups", params={"fields": "_default_,userCount"})
    )


def group_members(client: TableauClient, group_id: str) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/groups/{group-id}/users, every page."""
    return list(client.paginate(f"/groups/{group_id}/users", "users", "user", action="List group members"))


def list_connections(client: TableauClient, kind: str, item_id: str) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/{kind}/{id}/connections, where kind is "workbooks" or "datasources".

    Not paged: an item has a handful of connections at most.
    """
    data = client.get_json(f"/{kind}/{item_id}/connections", action="List connections")
    return (data.get("connections") or {}).get("connection") or []


def list_subscriptions(client: TableauClient) -> List[Dict[str, Any]]:
    """GET /sites/{site-id}/subscriptions, every page."""
    return list(client.paginate("/subscriptions", "subscriptions", "subscription", action="List subscriptions"))


def owner_label(owner: Dict[str, Any]) -> str:
    return owner.get("fullName") or owner.get("name") or ""
