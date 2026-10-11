"""Which projects a report covers: every project, all except some branches, or only some branches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Counter, List, Optional, Sequence, Set

from . import ui
from .projects import ProjectTree

EVERY_PROJECT = "Every project"


@dataclass(frozen=True)
class Scope:
    """``project_ids`` None means every project."""

    label: str
    project_ids: Optional[Set[str]] = None
    include: bool = False  # True: only the named branches. False: everything except them

    def covers(self, project_id: str, visible: bool = True) -> bool:
        """``visible`` False means the item is outside the project tree you can see
        (Personal Space, or a hidden project). Those belong to no named branch."""
        if self.project_ids is None:
            return True
        if not visible:
            return not self.include
        return project_id in self.project_ids


def choose_scope(
    tree: ProjectTree,
    *,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
    ask: bool = True,
    counts: Optional[Counter[str]] = None,
    noun: str = "item",
) -> Scope:
    """From --include / --exclude paths, else asked (every project when ``ask`` is False).

    ``counts`` (items per project id) labels each top-level project in the picker
    with how many ``noun``s sit in it and below it.
    """
    if include or exclude:
        picked = [tree.find_by_path(path) for path in (include or exclude or [])]
        return branches(tree, [p.id for p in picked], include=bool(include))
    if not ask or not len(tree):
        return Scope(EVERY_PROJECT)

    ui.heading("Which projects?")
    choice = ui.menu(
        [
            EVERY_PROJECT,
            "Every project except some top-level projects",
            "Only some top-level projects (and everything under them)",
        ],
        bottom=[("q", "Quit")],
    )
    if choice == 0:
        return Scope(EVERY_PROJECT)

    roots = tree.roots()
    labels = []
    for project in roots:
        label = project.name
        if counts is not None:
            total = sum(counts[i] for i in tree.subtree_ids(project.id))
            label += "  " + ui.style(f"{total} {noun}{'' if total == 1 else 's'}", "dim")
        labels.append(label)
    ui.heading("Top-level projects to " + ("leave out" if choice == 1 else "include"))
    picked_indexes = ui.pick_many(labels)
    return branches(tree, [roots[i].id for i in picked_indexes], include=choice == 2)


def branches(tree: ProjectTree, branch_ids: List[str], include: bool) -> Scope:
    """A scope of these branches (each project plus everything below it), or of everything else."""
    covered: Set[str] = set()
    for project_id in branch_ids:
        covered |= tree.subtree_ids(project_id)
    names = ", ".join(tree.path(i) for i in branch_ids)
    if include:
        return Scope(f"Only: {names}", covered, include=True)
    return Scope(f"{EVERY_PROJECT} except: {names}", set(tree.by_id) - covered)
