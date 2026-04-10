import json
import os
import subprocess
from pathlib import Path
from typing import Any

import click

from boilersync.commands.pull import (
    get_template_config,
    get_template_inheritance_chain,
)
from boilersync.paths import paths
from boilersync.template_processor import scan_template_for_variables
from boilersync.template_sources import parse_repo_locator

EXCLUDED_TEMPLATE_SCAN_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

DEFAULT_CONTEXT_VARIABLES = {
    "NAME_SNAKE",
    "NAME_PASCAL",
    "NAME_KEBAB",
    "NAME_CAMEL",
    "NAME_PRETTY",
    "name_pascal",
    "name_kebab",
    "name_camel",
}

BUILT_IN_VARIABLE_DEFINITIONS = {
    "name_snake": {
        "name": "name_snake",
        "label": "Project Name",
        "type": "string",
        "required": False,
        "description": "Snake_case project name. Defaults to the target directory name.",
    },
    "name_pretty": {
        "name": "name_pretty",
        "label": "Pretty Name",
        "type": "string",
        "required": False,
        "description": "Display name. Defaults to a prettified version of name_snake.",
    },
}


def _iter_repo_template_subdirs(repo_dir: Path) -> list[str]:
    template_dirs: list[str] = []

    for root, dir_names, file_names in os.walk(repo_dir):
        dir_names[:] = sorted(
            [
                name
                for name in dir_names
                if not name.startswith(".") and name not in EXCLUDED_TEMPLATE_SCAN_DIRS
            ]
        )

        current_dir = Path(root)
        rel_path = current_dir.relative_to(repo_dir)
        if rel_path == Path("."):
            continue

        visible_files = [name for name in file_names if not name.startswith(".")]
        if not visible_files:
            continue

        if "template.json" not in visible_files:
            continue

        rel_str = str(rel_path).replace("\\", "/")
        template_dirs.append(rel_str)

        # A template root is identified by template.json, so child directories
        # should not be exposed as additional templates.
        dir_names[:] = []

    return sorted(set(template_dirs))


def list_local_templates() -> list[dict[str, Any]]:
    template_root_dir = paths.template_root_dir
    if not template_root_dir.exists():
        return []

    templates: list[dict[str, Any]] = []

    for org_dir in sorted(template_root_dir.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue

        for repo_dir in sorted(org_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            if not (repo_dir / ".git").exists():
                continue

            for subdir in _iter_repo_template_subdirs(repo_dir):
                template_dir = repo_dir / subdir
                templates.append(
                    {
                        "template_ref": f"{org_dir.name}/{repo_dir.name}#{subdir}",
                        "org": org_dir.name,
                        "repo": repo_dir.name,
                        "subdir": subdir,
                        "template_dir": str(template_dir),
                        "display_name": f"{org_dir.name}/{repo_dir.name}#{subdir}",
                    }
                )

    return templates


def _normalize_input_definition(
    name: str,
    raw_definition: Any,
    *,
    default_required: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "label": name.replace("_", " ").strip().title(),
        "type": "string",
        "required": default_required,
        "description": "",
    }

    if isinstance(raw_definition, dict):
        label = raw_definition.get("label") or raw_definition.get("prompt")
        if isinstance(label, str) and label.strip():
            result["label"] = label.strip()

        description = raw_definition.get("description")
        if isinstance(description, str) and description.strip():
            result["description"] = description.strip()

        value_type = raw_definition.get("type")
        if isinstance(value_type, str) and value_type.strip():
            result["type"] = value_type.strip().lower()

        if "default" in raw_definition:
            result["default"] = raw_definition.get("default")
            if "required" not in raw_definition:
                result["required"] = False

        required = raw_definition.get("required")
        if isinstance(required, bool):
            result["required"] = required

        choices = (
            raw_definition.get("choices")
            or raw_definition.get("options")
            or raw_definition.get("enum")
        )
        if isinstance(choices, list) and choices:
            result["choices"] = choices
        return result

    if isinstance(raw_definition, list) and raw_definition:
        result["choices"] = raw_definition
        return result

    if raw_definition is not None:
        result["default"] = raw_definition
    return result


def _merge_input_metadata(
    inheritance_chain: list[Any],
    key: str,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for template_source in inheritance_chain:
        template_config = get_template_config(template_source.template_dir)
        raw_definitions = template_config.get(key)
        if not isinstance(raw_definitions, dict):
            continue

        for name, raw_definition in raw_definitions.items():
            normalized = _normalize_input_definition(
                str(name),
                raw_definition,
                default_required=False,
            )
            existing = merged.get(str(name), {})
            merged[str(name)] = {**existing, **normalized}

    return merged


def get_template_details(template_ref: str) -> dict[str, Any]:
    inheritance_chain = get_template_inheritance_chain(template_ref)
    leaf_source = inheritance_chain[-1]

    variable_names: set[str] = set()
    for template_source in inheritance_chain:
        variable_names.update(scan_template_for_variables(template_source.template_dir))
    variable_names -= DEFAULT_CONTEXT_VARIABLES

    variable_metadata = _merge_input_metadata(inheritance_chain, "variables")
    option_metadata = _merge_input_metadata(inheritance_chain, "options")

    variable_names |= set(BUILT_IN_VARIABLE_DEFINITIONS.keys())

    variables: list[dict[str, Any]] = []
    for variable_name in sorted(variable_names | set(variable_metadata.keys())):
        definition = _normalize_input_definition(
            variable_name,
            variable_metadata.get(variable_name),
            default_required=variable_name in variable_names,
        )
        built_in_definition = BUILT_IN_VARIABLE_DEFINITIONS.get(variable_name)
        if built_in_definition:
            definition = {**definition, **built_in_definition}
        variables.append(definition)

    options: list[dict[str, Any]] = []
    for option_name in sorted(option_metadata.keys()):
        options.append(
            _normalize_input_definition(
                option_name,
                option_metadata[option_name],
                default_required=False,
            )
        )

    return {
        "template_ref": leaf_source.ref,
        "template_dir": str(leaf_source.template_dir),
        "template_root_dir": str(paths.template_root_dir),
        "inheritance_chain": [source.ref for source in inheritance_chain],
        "variables": variables,
        "options": options,
    }


def init_templates(
    repo_url: str | None,
    repo_url_option: str | None = None,
    no_input: bool = False,
) -> None:
    """Initialize local template sources by cloning a GitHub repository."""
    if repo_url and repo_url_option:
        raise click.UsageError(
            "Provide either REPO_URL argument or --repo-url, not both."
        )

    final_repo_url = repo_url_option or repo_url
    if not final_repo_url:
        if no_input:
            raise click.ClickException(
                "Template repository URL is required when --no-input is used."
            )
        final_repo_url = click.prompt(
            "GitHub repository URL to clone into local template cache",
            type=str,
        ).strip()
        if not final_repo_url:
            raise click.ClickException("Template repository URL cannot be empty.")

    try:
        org, repo, canonical_repo_url = parse_repo_locator(final_repo_url)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    target_dir = paths.template_root_dir / org / repo
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        if (target_dir / ".git").exists():
            click.echo(f"✅ Template source already initialized at: {target_dir}")
            return
        if any(target_dir.iterdir()):
            raise click.ClickException(
                f"Template source directory already exists and is not empty: {target_dir}"
            )

    click.echo(f"📦 Cloning template source into: {target_dir}")
    try:
        subprocess.run(["git", "clone", canonical_repo_url, str(target_dir)], check=True)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Failed to clone template source: {e}") from e
    click.echo("✅ Template source initialized successfully.")


@click.command(name="init")
@click.argument("repo_url", required=False)
@click.option(
    "--repo-url",
    "repo_url_option",
    help="GitHub repository URL to clone.",
)
@click.option("--no-input", is_flag=True, help="Do not prompt for input.")
def templates_init_cmd(
    repo_url: str | None,
    repo_url_option: str | None,
    no_input: bool,
) -> None:
    """Clone a template source GitHub repository into the configured template cache.

    REPO_URL is optional. If omitted, Boilersync will prompt for it.
    """
    init_templates(
        repo_url=repo_url,
        repo_url_option=repo_url_option,
        no_input=no_input,
    )


@click.command(name="list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def templates_list_cmd(json_output: bool) -> None:
    """List locally available templates in the template cache."""
    templates = list_local_templates()
    payload = {
        "template_root_dir": str(paths.template_root_dir),
        "count": len(templates),
        "templates": templates,
    }

    if json_output:
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Template root: {payload['template_root_dir']}")
    if not templates:
        click.echo("No templates found.")
        return

    click.echo("")
    for template in templates:
        click.echo(f"- {template['template_ref']}")
        click.echo(f"  path: {template['template_dir']}")


@click.command(name="details")
@click.argument("template_ref")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def templates_details_cmd(template_ref: str, json_output: bool) -> None:
    """Show input fields for a template reference."""
    details = get_template_details(template_ref)

    if json_output:
        click.echo(json.dumps(details, indent=2))
        return

    click.echo(f"Template: {details['template_ref']}")
    click.echo(f"Path: {details['template_dir']}")
    click.echo("")
    click.echo("Variables:")
    if details["variables"]:
        for variable in details["variables"]:
            required = "required" if variable.get("required") else "optional"
            click.echo(f"- {variable['name']} ({required})")
    else:
        click.echo("- none")

    click.echo("")
    click.echo("Options:")
    if details["options"]:
        for option in details["options"]:
            required = "required" if option.get("required") else "optional"
            click.echo(f"- {option['name']} ({required})")
    else:
        click.echo("- none")


@click.group(name="templates")
def templates_cmd() -> None:
    """Manage local template source cache configuration and setup."""
    pass


templates_cmd.add_command(templates_init_cmd)
templates_cmd.add_command(templates_list_cmd)
templates_cmd.add_command(templates_details_cmd)
