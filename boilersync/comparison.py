import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from git import Repo

from boilersync.file_ignores import copyable_project_files
from boilersync.project_context import (
    resolve_project_template_context,
    set_interpolation_context,
)
from boilersync.template_ownership import TemplateOwnership
from boilersync.template_workspace import copy_rendered_template_chain


@dataclass(frozen=True)
class ComparisonWorkspace:
    project_dir: Path
    comparison_dir: Path
    template_ref: str
    ownership_map: dict[str, TemplateOwnership]


def is_starter_source_path(path: str) -> bool:
    return any(part.split(".")[1:2] == ["starter"] for part in Path(path).parts)


def changed_paths(repo: Repo) -> list[str]:
    output = repo.git.diff("--cached", "--name-only")
    return [line for line in output.splitlines() if line.strip()]


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def copy_project_files_for_comparison(
    source_dir: Path,
    target_dir: Path,
    boilersync_data: dict[str, object],
) -> None:
    child_paths = [
        Path(child)
        for child in boilersync_data.get("children", [])
        if isinstance(child, str)
    ]

    for rel_path in copyable_project_files(source_dir):
        item = source_dir / rel_path
        if item.name in [".boilersync", ".git"] or ".git/" in str(rel_path):
            continue
        if any(
            rel_path == child or is_relative_to(rel_path, child)
            for child in child_paths
        ):
            continue

        target_file = target_dir / rel_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target_file)


def filtered_changed_paths(
    repo: Repo,
    ownership_map: dict[str, TemplateOwnership],
    *,
    include_starter: bool,
) -> list[str]:
    paths = changed_paths(repo)
    if include_starter:
        return paths

    return [
        path
        for path in paths
        if not is_starter_source_path(
            ownership_map.get(
                path, TemplateOwnership("", Path(), Path(), path)
            ).source_relative_path
        )
    ]


def build_comparison_workspace(project_dir: Path) -> ComparisonWorkspace:
    project_context = resolve_project_template_context(project_dir)
    template_ref = project_context.template_ref

    path_hash = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:8]
    comparison_dir = (
        Path(tempfile.gettempdir()) / f"boilersync-diff-{path_hash}" / "project"
    )

    shutil.rmtree(comparison_dir, ignore_errors=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)

    context = set_interpolation_context(project_context)
    ownership_map = copy_rendered_template_chain(
        project_context.inheritance_chain,
        comparison_dir,
        context,
    )

    repo = Repo.init(comparison_dir)
    with repo.config_writer() as config:
        config.set_value("user", "name", "BoilerSync")
        config.set_value("user", "email", "boilersync@example.invalid")
    repo.git.add(A=True)
    repo.index.commit(f"Fresh template: {template_ref}")

    for item in comparison_dir.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    copy_project_files_for_comparison(
        project_dir,
        comparison_dir,
        project_context.metadata,
    )

    repo.git.add(A=True)

    return ComparisonWorkspace(
        project_dir=project_dir,
        comparison_dir=comparison_dir,
        template_ref=template_ref,
        ownership_map=ownership_map,
    )


def git_diff(
    comparison: ComparisonWorkspace,
    *,
    include_starter: bool,
    mode: str,
) -> str:
    repo = Repo(comparison.comparison_dir)
    paths = filtered_changed_paths(
        repo,
        comparison.ownership_map,
        include_starter=include_starter,
    )
    if not paths:
        return ""

    args = ["--cached"]
    if mode == "stat":
        args.append("--stat")
    elif mode == "name-status":
        args.append("--name-status")
    elif mode != "patch":
        raise ValueError(f"Unknown diff mode: {mode}")

    args.extend(["--", *paths])
    return repo.git.diff(*args)


def diff_entries(
    comparison: ComparisonWorkspace,
    *,
    include_starter: bool,
) -> list[dict[str, object]]:
    repo = Repo(comparison.comparison_dir)
    paths = filtered_changed_paths(
        repo,
        comparison.ownership_map,
        include_starter=include_starter,
    )
    if not paths:
        return []

    output = repo.git.diff("--cached", "--name-status", "--", *paths)
    entries: list[dict[str, object]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        entry: dict[str, object] = {"status": parts[0]}
        if len(parts) == 2:
            entry["path"] = parts[1]
        elif len(parts) >= 3:
            entry["old_path"] = parts[1]
            entry["path"] = parts[2]
        entries.append(entry)
    return entries
