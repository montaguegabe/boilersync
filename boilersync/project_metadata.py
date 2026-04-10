import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from git import Repo

from boilersync.paths import paths
from boilersync.template_sources import TemplateSource, resolve_source_from_boilersync

TEMPLATE_COMMIT_FIELD = "template_commit"


@dataclass(frozen=True)
class PullCheckStatus:
    project_dir: Path
    template_ref: str
    recorded_template_commit: str | None
    current_template_commit: str
    due_for_pull: bool


def load_project_metadata(project_dir: Path | None = None) -> dict[str, Any]:
    resolved_project_dir = project_dir or paths.root_dir
    metadata_path = resolved_project_dir / ".boilersync"

    with open(metadata_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Expected {metadata_path} to contain a JSON object.")

    return data


def get_template_repo_commit(template_source: TemplateSource) -> str:
    repo = Repo(template_source.local_repo_path)
    return repo.head.commit.hexsha


def write_project_metadata(
    project_dir: Path,
    *,
    template_source: TemplateSource,
    name_snake: str,
    name_pretty: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    metadata_path = project_dir / ".boilersync"
    existing_data: dict[str, Any] = {}
    if metadata_path.exists():
        existing_data = load_project_metadata(project_dir)

    metadata = dict(existing_data)
    metadata.update(
        {
            "template": template_source.canonical_ref,
            TEMPLATE_COMMIT_FIELD: get_template_repo_commit(template_source),
            "name_snake": name_snake,
            "name_pretty": name_pretty,
            "variables": variables,
        }
    )

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def get_pull_check_status(project_dir: Path | None = None) -> PullCheckStatus:
    resolved_project_dir = project_dir or paths.root_dir
    metadata = load_project_metadata(resolved_project_dir)
    template_source = resolve_source_from_boilersync(metadata.get("template"))

    raw_recorded_commit = metadata.get(TEMPLATE_COMMIT_FIELD)
    recorded_commit = (
        raw_recorded_commit.strip()
        if isinstance(raw_recorded_commit, str) and raw_recorded_commit.strip()
        else None
    )
    current_commit = get_template_repo_commit(template_source)

    return PullCheckStatus(
        project_dir=resolved_project_dir,
        template_ref=template_source.canonical_ref,
        recorded_template_commit=recorded_commit,
        current_template_commit=current_commit,
        due_for_pull=recorded_commit != current_commit,
    )
