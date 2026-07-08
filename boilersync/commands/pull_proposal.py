import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
from git import Repo

from boilersync.commands.pull import pull
from boilersync.file_ignores import copy_project_files_to_directory
from boilersync.paths import paths
from boilersync.project_context import resolve_project_template_context


@dataclass(frozen=True)
class PullProposal:
    project_dir: Path
    workspace_dir: Path
    proposal_dir: Path
    changed_files: list[dict[str, str]]


def _is_safe_relative_path(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def _copy_project_to_proposal(project_dir: Path, proposal_dir: Path) -> None:
    shutil.rmtree(proposal_dir, ignore_errors=True)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    copy_project_files_to_directory(project_dir, proposal_dir)


def _commit_proposal_baseline(proposal_dir: Path) -> Repo:
    repo = Repo.init(proposal_dir)
    with repo.config_writer() as config:
        config.set_value("user", "name", "BoilerSync")
        config.set_value("user", "email", "boilersync@example.invalid")
    repo.git.add(A=True)
    repo.index.commit("Current project before BoilerSync pull")
    return repo


def _changed_files(repo: Repo) -> list[dict[str, str]]:
    output = repo.git.diff("--name-status")
    entries: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        if len(parts) == 2:
            entries.append({"status": parts[0], "path": parts[1]})
        elif len(parts) >= 3:
            entries.append({"status": parts[0], "old_path": parts[1], "path": parts[2]})
    return entries


def create_pull_proposal(
    project_dir: Path | None = None,
    *,
    include_starter: bool = False,
    include_children: bool = False,
    workspace_dir: Path | None = None,
) -> PullProposal:
    resolved_project_dir = (project_dir or paths.root_dir).resolve()
    path_hash = hashlib.md5(str(resolved_project_dir).encode()).hexdigest()[:8]
    resolved_workspace_dir = (
        workspace_dir.expanduser()
        if workspace_dir
        else Path(tempfile.gettempdir()) / f"boilersync-pull-{path_hash}"
    )
    proposal_dir = resolved_workspace_dir / "proposal"

    _copy_project_to_proposal(resolved_project_dir, proposal_dir)
    repo = _commit_proposal_baseline(proposal_dir)
    project_context = resolve_project_template_context(proposal_dir)
    collected_variables = dict(project_context.variables)
    if project_context.name_snake is not None:
        collected_variables["name_snake"] = project_context.name_snake
    if project_context.name_pretty is not None:
        collected_variables["name_pretty"] = project_context.name_pretty

    original_cwd = Path.cwd()
    try:
        click.echo(f"Creating pull proposal in {proposal_dir}", err=True)
        os.chdir(proposal_dir)
        pull(
            project_context.template_ref,
            collected_variables=collected_variables,
            allow_non_empty=True,
            include_starter=include_starter,
            no_input=True,
            target_dir=proposal_dir,
            _recursive=include_children,
        )
        repo.git.add("-N", A=True)
    finally:
        os.chdir(original_cwd)

    return PullProposal(
        project_dir=resolved_project_dir,
        workspace_dir=resolved_workspace_dir,
        proposal_dir=proposal_dir,
        changed_files=_changed_files(repo),
    )


def proposal_to_json(proposal: PullProposal) -> str:
    payload = {
        "project_dir": str(proposal.project_dir),
        "workspace_dir": str(proposal.workspace_dir),
        "proposal_dir": str(proposal.proposal_dir),
        "changed_count": len(proposal.changed_files),
        "changed_files": proposal.changed_files,
    }
    return json.dumps(payload, indent=2)


def proposal_to_text(proposal: PullProposal) -> str:
    lines = [
        f"Project: {proposal.project_dir}",
        f"Proposal: {proposal.proposal_dir}",
        "",
        "Review proposed changes with:",
        f"  git -C {proposal.proposal_dir} diff",
        "",
        "Apply a whole proposed file from the project root with:",
        f"  boilersync pull-proposal apply-file {proposal.proposal_dir} PATH",
        "",
        "Apply selected hunks by saving a patch and running:",
        "  boilersync pull-proposal apply-patch PATCH",
        "",
        f"Changed files: {len(proposal.changed_files)}",
    ]
    for entry in proposal.changed_files:
        path = entry.get("path", "")
        lines.append(f"  {entry.get('status', '')}\t{path}")
    return "\n".join(lines)


def apply_proposed_file(
    proposal_dir: Path,
    relative_path: Path,
    *,
    project_dir: Path | None = None,
) -> Path:
    if not _is_safe_relative_path(relative_path):
        raise ValueError(
            f"Path must be relative and stay inside the project: {relative_path}"
        )

    resolved_project_dir = (project_dir or paths.root_dir).resolve()
    source_path = proposal_dir.expanduser() / relative_path
    target_path = resolved_project_dir / relative_path

    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Proposed file does not exist: {source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path


def apply_patch_file(
    patch_path: Path,
    *,
    project_dir: Path | None = None,
) -> None:
    resolved_project_dir = (project_dir or paths.root_dir).resolve()
    subprocess.run(
        ["git", "apply", str(patch_path.expanduser())],
        cwd=resolved_project_dir,
        check=True,
    )


@click.group(name="pull-proposal")
def pull_proposal_cmd() -> None:
    """Create and apply reviewable BoilerSync pull proposals."""


@pull_proposal_cmd.command(name="create")
@click.option(
    "--include-starter",
    is_flag=True,
    help="Include starter files in the proposed pull.",
)
@click.option(
    "--children",
    "include_children",
    is_flag=True,
    help="Include child projects in the proposed pull.",
)
@click.option(
    "--workspace-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for the proposal workspace. Defaults to /tmp/boilersync-pull-<hash>.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def create_cmd(
    include_starter: bool,
    include_children: bool,
    workspace_dir: Path | None,
    json_output: bool,
) -> None:
    """Create a temp git repo containing proposed pull changes."""
    proposal = create_pull_proposal(
        include_starter=include_starter,
        include_children=include_children,
        workspace_dir=workspace_dir,
    )
    click.echo(
        proposal_to_json(proposal) if json_output else proposal_to_text(proposal)
    )


@pull_proposal_cmd.command(name="apply-file")
@click.argument("proposal_dir", type=click.Path(file_okay=False, path_type=Path))
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
def apply_file_cmd(proposal_dir: Path, path: Path) -> None:
    """Copy one file from a proposal into the current project."""
    target_path = apply_proposed_file(proposal_dir, path)
    click.echo(f"Applied proposed file: {target_path}")


@pull_proposal_cmd.command(name="apply-patch")
@click.argument("patch_path", type=click.Path(dir_okay=False, path_type=Path))
def apply_patch_cmd(patch_path: Path) -> None:
    """Apply a selected patch to the current project with git apply."""
    apply_patch_file(patch_path)
    click.echo(f"Applied patch: {patch_path}")
