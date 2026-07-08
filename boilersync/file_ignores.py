import shutil
import subprocess
from pathlib import Path


DEFAULT_COPY_IGNORED_NAMES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def is_default_copy_ignored(path: Path) -> bool:
    return any(part in DEFAULT_COPY_IGNORED_NAMES for part in path.parts)


def is_agents_compatibility_symlink(root_dir: Path, path: Path) -> bool:
    if path != Path("CLAUDE.md"):
        return False

    source = root_dir / path
    if not source.is_symlink():
        return False

    try:
        return source.readlink() == Path("AGENTS.md")
    except OSError:
        return False


def git_visible_files(root_dir: Path) -> list[Path] | None:
    """Return git-visible file paths relative to root_dir, or None outside git.

    Git-visible means tracked files plus untracked files that are not ignored by
    the repo's normal ignore rules. This keeps BoilerSync temp workspaces from
    copying ignored dependency/build/cache files.
    """
    inside = subprocess.run(
        ["git", "-C", str(root_dir), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None

    result = subprocess.run(
        [
            "git",
            "-C",
            str(root_dir),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--deduplicate",
            "--",
            ".",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None

    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        if path.is_absolute() or ".." in path.parts or is_default_copy_ignored(path):
            continue
        if is_agents_compatibility_symlink(root_dir, path):
            continue
        if (root_dir / path).is_file():
            paths.append(path)
    return paths


def copyable_project_files(root_dir: Path) -> list[Path]:
    git_paths = git_visible_files(root_dir)
    if git_paths is not None:
        return git_paths

    paths: list[Path] = []
    for item in root_dir.rglob("*"):
        if not item.is_file():
            continue
        relative_path = item.relative_to(root_dir)
        if is_default_copy_ignored(relative_path):
            continue
        if is_agents_compatibility_symlink(root_dir, relative_path):
            continue
        paths.append(relative_path)
    return paths


def copy_project_files_to_directory(
    source_dir: Path,
    target_dir: Path,
    *,
    exclude_names: set[str] | None = None,
) -> None:
    excluded = exclude_names or set()
    for relative_path in copyable_project_files(source_dir):
        if relative_path.name in excluded:
            continue

        source_file = source_dir / relative_path
        target_file = target_dir / relative_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
