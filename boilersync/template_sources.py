import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from boilersync.paths import paths

SOURCE_RESOLUTION = "source_ref"
DEFAULT_LEGACY_REPO_LOCATOR = "openbase-community/templates"
REPO_RENAMES = {
    ("openbase-community", "openbase-boilerplate"): (
        "openbase-community",
        "templates",
    ),
}


@dataclass(frozen=True)
class TemplateSource:
    ref: str
    resolution: Literal["source_ref"]
    template_dir: Path
    repo_url: str
    org: str
    repo: str
    subdir: str
    local_repo_path: Path

    @property
    def identifier(self) -> str:
        return f"source:{self.org}/{self.repo}#{self.subdir}"

    @property
    def canonical_ref(self) -> str:
        return f"{self.repo_url}#{self.subdir}"


def _normalize_subdir(subdir: str) -> str:
    cleaned = subdir.strip().strip("/")
    if not cleaned:
        raise ValueError("Template source ref must include a non-empty subdir after '#'.")
    if cleaned.startswith("../") or cleaned == ".." or "/../" in cleaned:
        raise ValueError("Template source subdir must stay within the source repository.")
    return cleaned


def _canonical_repo_identity(org: str, repo: str) -> tuple[str, str]:
    return REPO_RENAMES.get((org, repo), (org, repo))


def parse_repo_locator(repo_locator: str) -> tuple[str, str, str]:
    locator = repo_locator.strip()
    if not locator:
        raise ValueError("Template source repo cannot be empty.")

    if locator.startswith("git@"):
        raise ValueError(
            "SSH template refs are not supported. Use a GitHub HTTPS URL."
        )

    if "://" in locator:
        parsed = urlparse(locator)
        if parsed.scheme != "https":
            raise ValueError(f"Invalid template source repo URL: {repo_locator}")
        if parsed.netloc not in {"github.com", "www.github.com"}:
            raise ValueError(
                "Template source repo URL must be hosted on github.com."
            )
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[: -len(".git")]
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2:
            raise ValueError(
                "Template source repo URL must be in the format https://github.com/org/repo(.git)."
            )
        org, repo = _canonical_repo_identity(parts[0], parts[1])
        clone_url = f"https://github.com/{org}/{repo}.git"
        return org, repo, clone_url

    match = re.match(r"^(?P<org>[^/\s]+)/(?P<repo>[^/\s]+)$", locator)
    if match:
        org = match.group("org")
        repo = match.group("repo")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        org, repo = _canonical_repo_identity(org, repo)
        clone_url = f"https://github.com/{org}/{repo}.git"
        return org, repo, clone_url

    raise ValueError(
        "Invalid template source repo. Use org/repo or https://github.com/org/repo(.git)."
    )


def _parse_source_ref(template_ref: str) -> tuple[str, str]:
    if "#" not in template_ref:
        raise ValueError(
            "Template source ref must be in the format https://github.com/org/repo.git#subdir."
        )
    repo_locator, subdir = template_ref.split("#", 1)
    repo_locator = repo_locator.strip()
    if not repo_locator:
        raise ValueError("Template source ref must include a repo before '#'.")

    return repo_locator, _normalize_subdir(subdir)


def _ensure_repo_cloned(repo_url: str, repo_path: Path) -> None:
    repo_parent = repo_path.parent
    repo_parent.mkdir(parents=True, exist_ok=True)

    if repo_path.exists():
        if (repo_path / ".git").exists():
            return
        if any(repo_path.iterdir()):
            raise FileExistsError(
                f"Template repo path exists and is not a git repo: {repo_path}"
            )

    subprocess.run(["git", "clone", repo_url, str(repo_path)], check=True)


def resolve_template_source(
    template_ref: str,
    *,
    clone_missing_repo: bool = True,
) -> TemplateSource:
    source_ref = _parse_source_ref(template_ref)
    repo_locator, subdir = source_ref
    org, repo, repo_url = parse_repo_locator(repo_locator)

    local_repo_path = paths.template_root_dir / org / repo
    if clone_missing_repo:
        _ensure_repo_cloned(repo_url, local_repo_path)

    if not (local_repo_path / ".git").exists():
        raise FileNotFoundError(
            f"Template source repository is not initialized at {local_repo_path}. "
            "Run 'boilersync templates init <repo-url>' or use a source ref to auto-clone."
        )

    template_dir = local_repo_path / subdir
    if not template_dir.exists():
        raise FileNotFoundError(
            f"Template subdir '{subdir}' not found in template source repo: {local_repo_path}"
        )

    canonical_ref = f"{repo_url}#{subdir}"
    return TemplateSource(
        ref=canonical_ref,
        resolution=SOURCE_RESOLUTION,
        repo_url=repo_url,
        org=org,
        repo=repo,
        subdir=subdir,
        local_repo_path=local_repo_path,
        template_dir=template_dir,
    )


def resolve_source_from_boilersync(
    template_ref: str | None,
    *,
    clone_missing_repo: bool = True,
) -> TemplateSource:
    if not isinstance(template_ref, str) or not template_ref.strip():
        raise ValueError("Template reference missing in .boilersync metadata.")

    normalized_ref = template_ref.strip()
    if "#" not in normalized_ref:
        normalized_ref = f"{DEFAULT_LEGACY_REPO_LOCATOR}#{normalized_ref}"

    return resolve_template_source(
        normalized_ref,
        clone_missing_repo=clone_missing_repo,
    )
