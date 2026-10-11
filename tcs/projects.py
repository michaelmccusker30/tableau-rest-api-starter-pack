"""The site's project hierarchy and the tiered, numbered project picker."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set

from . import ui
from .client import TableauClient

PATH_SEPARATOR = "/"

# Asks Query Projects for the owner's details in the same request (see fetch_project_tree).
PROJECT_OWNER_FIELDS = "_default_,owner.name,owner.fullName,owner.email"

# System projects that show up in Query Projects but can't take published content.
HIDDEN_TOP_LEVEL = {"External Assets Default Project"}


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    parent_id: Optional[str] = None
    description: str = ""
    content_permissions: str = ""  # "ManagedByOwner", "LockedToProject" or "LockedToProjectWithoutNested"
    owner_name: str = ""  # filled in only by fetch_project_tree(..., with_owner=True)
    owner_email: str = ""


class ProjectTree:
    """Projects arranged by parent.

    A project whose parent you can't see (permissions hide it) is treated as a
    top-level project, so everything you can see stays reachable.
    """

    def __init__(self, projects: List[Project]) -> None:
        self.by_id: Dict[str, Project] = {p.id: p for p in projects}
        self._children: Dict[str, List[Project]] = defaultdict(list)
        self._roots: List[Project] = []
        for project in projects:
            if project.parent_id and project.parent_id in self.by_id:
                self._children[project.parent_id].append(project)
            else:
                self._roots.append(project)
        sort_key = lambda p: p.name.casefold()  # noqa: E731
        self._roots.sort(key=sort_key)
        for siblings in self._children.values():
            siblings.sort(key=sort_key)

    def __len__(self) -> int:
        return len(self.by_id)

    def roots(self) -> List[Project]:
        return list(self._roots)

    def children(self, project_id: str) -> List[Project]:
        return list(self._children.get(project_id, []))

    def lineage(self, project_id: str) -> List[Project]:
        """The project and its ancestors, top level first."""
        chain: List[Project] = []
        current: Optional[Project] = self.by_id[project_id]
        while current is not None:
            chain.append(current)
            current = self.by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(chain))

    def path(self, project_id: str) -> str:
        """e.g. "Finance / Reporting / Monthly"."""
        return f" {PATH_SEPARATOR} ".join(p.name for p in self.lineage(project_id))

    def depth(self, project_id: str) -> int:
        """1 for a top-level project, 2 for its sub-projects, and so on."""
        return len(self.lineage(project_id))

    def subtree_ids(self, project_id: str) -> Set[str]:
        """The project and every project below it, at any depth."""
        found: Set[str] = set()
        stack = [project_id]
        while stack:
            current = stack.pop()
            found.add(current)
            stack.extend(child.id for child in self._children.get(current, []))
        return found

    def walk(self) -> Iterator[Project]:
        """Every project, depth first, in the order the picker shows them."""
        stack = list(reversed(self._roots))
        while stack:
            project = stack.pop()
            yield project
            stack.extend(reversed(self._children.get(project.id, [])))

    def find_by_path(self, path: str) -> Project:
        """Resolve "Finance/Reporting/Monthly" (case-insensitive) to a project."""
        names = [part.strip() for part in path.split(PATH_SEPARATOR) if part.strip()]
        if not names:
            raise LookupError("The project path is empty.")
        level = self._roots
        parent: Optional[Project] = None
        for name in names:
            found = next((p for p in level if p.name.casefold() == name.casefold()), None)
            if found is None:
                where = f'under "{self.path(parent.id)}"' if parent else "at the top level"
                available = ", ".join(p.name for p in level)
                tail = f"Projects there: {available}." if available else "It has no sub-projects."
                raise LookupError(f'No project named "{name}" {where}. {tail}')
            parent = found
            level = self.children(found.id)
        assert parent is not None
        return parent

    def find_by_id(self, project_id: str) -> Project:
        try:
            return self.by_id[project_id]
        except KeyError:
            raise LookupError(f"No project with id {project_id} is visible to your account on this site.") from None


def fetch_project_tree(client: TableauClient, with_owner: bool = False) -> ProjectTree:
    """GET /sites/{site-id}/projects, every page.

    ``with_owner`` adds each project owner's name and email, through a field
    expression on the same request.
    """
    params = {"fields": PROJECT_OWNER_FIELDS} if with_owner else None
    projects = []
    for raw in client.paginate("/projects", "projects", "project", action="List projects", params=params):
        if raw["name"] in HIDDEN_TOP_LEVEL and not raw.get("parentProjectId"):
            continue
        owner = raw.get("owner") or {}
        projects.append(
            Project(
                id=raw["id"],
                name=raw["name"],
                parent_id=raw.get("parentProjectId") or None,
                description=raw.get("description") or "",
                content_permissions=raw.get("contentPermissions") or "",
                owner_name=owner.get("fullName") or owner.get("name") or "",
                owner_email=owner.get("email") or "",
            )
        )
    return ProjectTree(projects)


def resolve_project(
    tree: ProjectTree,
    path: Optional[str] = None,
    project_id: Optional[str] = None,
    *,
    here: str = "Publish here",
    title: str = "Choose a project",
) -> Project:
    """The project named by a --project-id or --project flag, else the interactive picker."""
    if project_id:
        return tree.find_by_id(project_id)
    if path:
        return tree.find_by_path(path)
    return pick_project(tree, here=here, title=title)


def pick_project(tree: ProjectTree, *, here: str = "Publish here", title: str = "Choose a project") -> Project:
    """Walk the hierarchy one level at a time.

    Top level: pick a project. Inside a project: choose it (``here``, e.g.
    "Publish here"), or go into a sub-project. Picking a project with no
    sub-projects selects it.
    """
    trail: List[Project] = []
    while True:
        current = trail[-1] if trail else None
        options = tree.children(current.id) if current else tree.roots()

        ui.heading(title)
        ui.detail(f"  Location: {tree.path(current.id) if current else '(top level)'}")
        labels = [_label(tree, p) for p in options]
        top = [("0", ui.style(f"{here} ({current.name})", "bold"))] if current else []
        bottom = ([("b", "Back")] if current else []) + [("q", "Quit")]

        choice = ui.menu(labels, top=top, bottom=bottom)
        if choice == "0" and current is not None:
            return current
        if choice == "b":
            trail.pop()
            continue
        assert isinstance(choice, int)
        picked = options[choice]
        if tree.children(picked.id):
            trail.append(picked)
        else:
            return picked


def _label(tree: ProjectTree, project: Project) -> str:
    count = len(tree.children(project.id))
    if not count:
        return project.name
    arrow = ui.symbol("›", ">")
    return f"{project.name}  " + ui.style(f"{arrow} {count} sub-project{'s' if count != 1 else ''}", "dim")
