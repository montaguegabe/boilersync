import json
from dataclasses import dataclass
from pathlib import Path

import click

from boilersync.comparison import (
    build_comparison_workspace,
    diff_entries,
    git_diff,
)
from boilersync.paths import paths


@dataclass(frozen=True)
class ProjectDiff:
    project_dir: Path
    template_ref: str | None
    comparison_dir: Path | None
    entries: list[dict[str, object]]
    output: str
    error: str | None = None


def collect_project_dirs(
    root_dir: Path, *, include_children: bool, recursive: bool
) -> list[Path]:
    project_dirs = [root_dir]
    if not include_children and not recursive:
        return project_dirs

    visited = {root_dir.resolve()}
    queue = [root_dir]

    while queue:
        parent = queue.pop(0)
        for child in paths.get_children_from_boilersync(parent / ".boilersync"):
            resolved_child = child.resolve()
            if resolved_child in visited:
                continue
            visited.add(resolved_child)
            project_dirs.append(child)
            if recursive:
                queue.append(child)

    return project_dirs


def create_project_diff(
    project_dir: Path,
    *,
    include_starter: bool,
    mode: str,
) -> ProjectDiff:
    comparison = build_comparison_workspace(project_dir)
    entries = diff_entries(comparison, include_starter=include_starter)
    output = git_diff(comparison, include_starter=include_starter, mode=mode)
    return ProjectDiff(
        project_dir=project_dir,
        template_ref=comparison.template_ref,
        comparison_dir=comparison.comparison_dir,
        entries=entries,
        output=output,
    )


def create_child_error(project_dir: Path, error: Exception) -> ProjectDiff:
    return ProjectDiff(
        project_dir=project_dir,
        template_ref=None,
        comparison_dir=None,
        entries=[],
        output="",
        error=str(error),
    )


def create_diffs(
    root_dir: Path,
    *,
    include_starter: bool,
    include_children: bool,
    recursive: bool,
    mode: str,
) -> list[ProjectDiff]:
    project_dirs = collect_project_dirs(
        root_dir,
        include_children=include_children,
        recursive=recursive,
    )
    results = [
        create_project_diff(project_dirs[0], include_starter=include_starter, mode=mode)
    ]

    for project_dir in project_dirs[1:]:
        try:
            results.append(
                create_project_diff(
                    project_dir,
                    include_starter=include_starter,
                    mode=mode,
                )
            )
        except Exception as error:
            results.append(create_child_error(project_dir, error))

    return results


def results_to_json(
    results: list[ProjectDiff],
    *,
    include_starter: bool,
) -> str:
    payload = {
        "include_starter": include_starter,
        "projects": [
            {
                "project_dir": str(result.project_dir),
                "template_ref": result.template_ref,
                "comparison_dir": str(result.comparison_dir)
                if result.comparison_dir
                else None,
                "changed_count": len(result.entries),
                "changed_files": result.entries,
                "error": result.error,
            }
            for result in results
        ],
    }
    return json.dumps(payload, indent=2)


def results_to_text(results: list[ProjectDiff]) -> str:
    sections = []
    for result in results:
        header = f"Project: {result.project_dir}"
        if result.error:
            sections.append(f"{header}\nError: {result.error}")
            continue
        if not result.output.strip():
            sections.append(f"{header}\nNo divergence from template.")
            continue
        sections.append(
            "\n".join(
                [
                    header,
                    f"Template: {result.template_ref}",
                    f"Comparison directory: {result.comparison_dir}",
                    result.output,
                ]
            )
        )
    return "\n\n".join(sections)


def resolve_mode(stat: bool, name_status: bool, patch: bool, json_output: bool) -> str:
    selected = [
        option
        for option, enabled in (
            ("stat", stat),
            ("name-status", name_status),
            ("patch", patch),
            ("json", json_output),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise click.UsageError(
            "Choose only one of --stat, --name-status, --patch, or --json."
        )
    return selected[0] if selected else "stat"


def diff(
    *,
    include_starter: bool = False,
    include_children: bool = False,
    recursive: bool = False,
    mode: str = "stat",
) -> str:
    output_mode = "name-status" if mode == "json" else mode
    results = create_diffs(
        paths.root_dir,
        include_starter=include_starter,
        include_children=include_children,
        recursive=recursive,
        mode=output_mode,
    )
    if mode == "json":
        return results_to_json(results, include_starter=include_starter)
    return results_to_text(results)


@click.command(name="diff")
@click.option(
    "--stat",
    "stat",
    is_flag=True,
    help="Show a diffstat summary. This is the default text mode.",
)
@click.option(
    "--name-status",
    "name_status",
    is_flag=True,
    help="Show changed file names and statuses.",
)
@click.option("--patch", "patch", is_flag=True, help="Show the full patch.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON summary output.")
@click.option(
    "--include-starter",
    is_flag=True,
    help="Include files derived from .starter template files.",
)
@click.option(
    "--children",
    "include_children",
    is_flag=True,
    help="Include direct child projects listed in .boilersync.",
)
@click.option(
    "--recursive",
    is_flag=True,
    help="Include child projects recursively.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write output to a file instead of stdout.",
)
def diff_cmd(
    stat: bool,
    name_status: bool,
    patch: bool,
    json_output: bool,
    include_starter: bool,
    include_children: bool,
    recursive: bool,
    output_path: Path | None,
) -> None:
    """Show how the current project has diverged from its upstream template."""
    mode = resolve_mode(stat, name_status, patch, json_output)
    result = diff(
        include_starter=include_starter,
        include_children=include_children,
        recursive=recursive,
        mode=mode,
    )

    if output_path:
        output_path.expanduser().write_text(result + "\n", encoding="utf-8")
        click.echo(f"Wrote BoilerSync diff to {output_path}")
        return

    click.echo(result)
