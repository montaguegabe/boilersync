import logging
import os
from pathlib import Path
import subprocess
from typing import Any

import click

from boilersync.commands.pull import (
    get_template_config,
    get_template_inheritance_chain,
    pull,
)
from boilersync.interpolation_context import interpolation_context
from boilersync.paths import paths
from boilersync.variable_collector import (
    convert_string_to_appropriate_type,
    create_jinja_environment,
)

logger = logging.getLogger(__name__)


def _merge_runtime_config(inheritance_chain: list[str]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "children": [],
        "hooks": {
            "pre_init": [],
            "post_init": [],
        },
        "github": {},
    }

    for template_name in inheritance_chain:
        template_dir = paths.boilerplate_dir / template_name
        template_config = get_template_config(template_dir)

        children = template_config.get("children", [])
        if children:
            if not isinstance(children, list):
                raise ValueError(
                    f"Template '{template_name}' has invalid 'children' config: expected list"
                )
            config["children"].extend(children)

        hooks = template_config.get("hooks", {})
        if hooks:
            if not isinstance(hooks, dict):
                raise ValueError(
                    f"Template '{template_name}' has invalid 'hooks' config: expected object"
                )
            for hook_key in ("pre_init", "post_init"):
                hook_steps = hooks.get(hook_key, [])
                if hook_steps:
                    if not isinstance(hook_steps, list):
                        raise ValueError(
                            f"Template '{template_name}' has invalid '{hook_key}' config: expected list"
                        )
                    config["hooks"][hook_key].extend(hook_steps)

        github = template_config.get("github", {})
        if github:
            if not isinstance(github, dict):
                raise ValueError(
                    f"Template '{template_name}' has invalid 'github' config: expected object"
                )
            config["github"].update(github)

    return config


def _render_string(value: str, context: dict[str, Any]) -> str:
    env = create_jinja_environment()
    template = env.from_string(value)
    return template.render(context)


def _render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_string(value, context)
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {str(key): _render_value(val, context) for key, val in value.items()}
    return value


def _parse_condition_token(token: str, context: dict[str, Any]) -> Any:
    stripped = token.strip()
    if stripped in context:
        return context[stripped]

    lower = stripped.lower()
    if lower in {"true", "yes", "on", "1"}:
        return True
    if lower in {"false", "no", "off", "0"}:
        return False

    if (
        (stripped.startswith('"') and stripped.endswith('"'))
        or (stripped.startswith("'") and stripped.endswith("'"))
    ) and len(stripped) >= 2:
        return stripped[1:-1]

    try:
        if "." in stripped:
            return float(stripped)
        return int(stripped)
    except ValueError:
        return stripped


def _evaluate_condition(condition: Any, context: dict[str, Any]) -> bool:
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, (int, float)):
        return bool(condition)
    if not isinstance(condition, str):
        raise ValueError(f"Unsupported condition type: {type(condition).__name__}")

    rendered_condition = _render_string(condition, context).strip()
    if not rendered_condition:
        return False

    if rendered_condition.startswith("not "):
        token = rendered_condition[len("not ") :].strip()
        return not bool(_parse_condition_token(token, context))

    for operator in ("==", "!="):
        if operator in rendered_condition:
            left, right = rendered_condition.split(operator, 1)
            left_value = _parse_condition_token(left, context)
            right_value = _parse_condition_token(right, context)
            result = left_value == right_value
            return not result if operator == "!=" else result

    if rendered_condition in context:
        return bool(context[rendered_condition])

    return bool(_parse_condition_token(rendered_condition, context))


def _run_hooks(
    hook_steps: list[dict[str, Any]],
    *,
    hook_name: str,
    target_dir: Path,
    context: dict[str, Any],
) -> None:
    for index, step in enumerate(hook_steps):
        if not isinstance(step, dict):
            raise ValueError(f"Invalid hook step in {hook_name}: expected object")

        step_id = str(step.get("id", f"{hook_name}_{index + 1}"))
        if not _evaluate_condition(step.get("condition"), context):
            logger.info(f"⏭️  Skipping {hook_name} hook '{step_id}' (condition=false)")
            continue

        raw_command = step.get("run")
        if not isinstance(raw_command, str) or not raw_command.strip():
            raise ValueError(
                f"Hook '{step_id}' in {hook_name} must define non-empty 'run' command"
            )

        command = _render_string(raw_command, context)
        hook_cwd = target_dir
        cwd_value = step.get("cwd")
        if cwd_value is not None:
            rendered_cwd = _render_string(str(cwd_value), context)
            hook_cwd = (target_dir / rendered_cwd).resolve()

        raw_env = step.get("env", {})
        if not isinstance(raw_env, dict):
            raise ValueError(f"Hook '{step_id}' in {hook_name} has invalid 'env' value")

        hook_env = os.environ.copy()
        for key, value in raw_env.items():
            hook_env[str(key)] = str(_render_value(value, context))

        allow_failure = bool(step.get("allow_failure", False))
        logger.info(f"⚙️  Running {hook_name} hook '{step_id}'")
        result = subprocess.run(
            command,
            shell=True,  # noqa: S602
            cwd=hook_cwd,
            env=hook_env,
            check=False,
        )

        if result.returncode != 0:
            error_message = (
                f"Hook '{step_id}' failed with exit code {result.returncode}: {command}"
            )
            if allow_failure:
                logger.warning(error_message)
                continue
            raise RuntimeError(error_message)


def _create_github_repo(
    github_config: dict[str, Any],
    *,
    target_dir: Path,
    context: dict[str, Any],
) -> None:
    if not github_config:
        return

    create_repo = bool(github_config.get("create_repo", False))
    if not create_repo:
        return

    if not _evaluate_condition(github_config.get("condition"), context):
        return

    repo_name_template = str(github_config.get("repo_name", "$${name_kebab}"))
    repo_name = _render_string(repo_name_template, context).strip()
    if not repo_name:
        raise ValueError("GitHub repo_name resolved to an empty value")

    user_result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        check=True,
        capture_output=True,
        text=True,
    )
    github_user = user_result.stdout.strip()
    full_repo_name = f"{github_user}/{repo_name}"

    check_result = subprocess.run(
        ["gh", "repo", "view", full_repo_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if check_result.returncode == 0:
        logger.info(f"✅ GitHub repository already exists: {full_repo_name}")
        return

    visibility_flag = "--private" if github_config.get("private", True) else "--public"
    subprocess.run(
        ["gh", "repo", "create", full_repo_name, visibility_flag],
        cwd=target_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info(f"✅ Created GitHub repository: {full_repo_name}")


def _normalize_template_variables(
    collected_variables: dict[str, Any] | None,
    template_variables: dict[str, Any] | None,
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    if collected_variables:
        merged.update(collected_variables)
    if template_variables:
        merged.update(template_variables)
    return merged or None


def init(
    template_name: str,
    target_dir: Path,
    collected_variables: dict[str, Any] | None = None,
    template_variables: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    project_name: str | None = None,
    pretty_name: str | None = None,
    no_input: bool = False,
    run_hooks: bool = True,
    run_children: bool = True,
) -> None:
    """Initialize a new project from a template (empty directory only).

    Args:
        template_name: Name of the template to use from the boilerplate directory

    Raises:
        FileNotFoundError: If the template directory doesn't exist
        FileExistsError: If the target directory is not empty
    """

    normalized_template_variables = _normalize_template_variables(
        collected_variables,
        template_variables,
    )

    # Check for parent .boilersync files before initializing
    parent_dir = paths.find_parent_boilersync(target_dir)
    inheritance_chain = get_template_inheritance_chain(template_name)
    runtime_config = _merge_runtime_config(inheritance_chain)

    # Initialize the project
    pull(
        template_name,
        allow_non_empty=False,
        include_starter=True,
        _recursive=False,
        collected_variables=normalized_template_variables,
        target_dir=target_dir,
        project_name=project_name,
        pretty_name=pretty_name,
        no_input=no_input,
    )

    # If we found a parent .boilersync, register this project as a child
    if parent_dir is not None:
        parent_boilersync_path = parent_dir / ".boilersync"
        paths.add_child_to_parent(target_dir, parent_boilersync_path)
        logger.info(f"📎 Registered as child project in parent: {parent_dir}")

    runtime_context = interpolation_context.get_context()
    if options:
        runtime_context.update(options)

    if run_hooks:
        pre_init_hooks = runtime_config["hooks"]["pre_init"]
        _run_hooks(
            pre_init_hooks,
            hook_name="pre_init",
            target_dir=target_dir,
            context=runtime_context,
        )

    _create_github_repo(
        runtime_config["github"],
        target_dir=target_dir,
        context=runtime_context,
    )

    if run_children:
        children = runtime_config["children"]
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                raise ValueError(
                    f"Invalid child config at index {index}: expected object, got {type(child).__name__}"
                )

            if not _evaluate_condition(child.get("condition"), runtime_context):
                continue

            child_template_name = child.get("template")
            child_target_path = child.get("path")

            if not isinstance(child_template_name, str) or not child_template_name:
                raise ValueError(
                    f"Invalid child config at index {index}: missing non-empty 'template'"
                )
            if not isinstance(child_target_path, str) or not child_target_path:
                raise ValueError(
                    f"Invalid child config at index {index}: missing non-empty 'path'"
                )

            rendered_child_path = _render_string(child_target_path, runtime_context)
            child_target_dir = target_dir / rendered_child_path
            child_target_dir.mkdir(parents=True, exist_ok=True)

            child_variables: dict[str, Any] = {}
            raw_child_variables = child.get("variables", {})
            if raw_child_variables:
                if not isinstance(raw_child_variables, dict):
                    raise ValueError(
                        f"Invalid child config for '{child_template_name}': 'variables' must be an object"
                    )
                child_variables = {
                    str(key): _render_value(value, runtime_context)
                    for key, value in raw_child_variables.items()
                }

            child_project_name = child.get("name_snake")
            if child_project_name is not None:
                child_project_name = _render_string(
                    str(child_project_name),
                    runtime_context,
                )

            child_pretty_name = child.get("name_pretty")
            if child_pretty_name is not None:
                child_pretty_name = _render_string(
                    str(child_pretty_name),
                    runtime_context,
                )

            init(
                child_template_name,
                target_dir=child_target_dir,
                template_variables=child_variables or None,
                options=options,
                project_name=child_project_name,
                pretty_name=child_pretty_name,
                no_input=no_input,
                run_hooks=run_hooks,
                run_children=run_children,
            )

    if run_hooks:
        post_init_hooks = runtime_config["hooks"]["post_init"]
        _run_hooks(
            post_init_hooks,
            hook_name="post_init",
            target_dir=target_dir,
            context=runtime_context,
        )


def parse_var(ctx, param, value: tuple[str, ...]) -> dict[str, Any]:
    """Parse --var options into a dictionary."""
    result: dict[str, Any] = {}
    for item in value:
        if "=" not in item:
            raise click.BadParameter(
                f"Variable must be in KEY=VALUE format, got: {item}"
            )
        key, val = item.split("=", 1)
        result[key.strip()] = convert_string_to_appropriate_type(val)
    return result


@click.command(name="init")
@click.argument("template_name")
@click.option(
    "-n",
    "--name",
    "project_name",
    help="Project name in snake_case (defaults to directory name)",
)
@click.option(
    "--pretty-name",
    help="Pretty display name for the project",
)
@click.option(
    "-v",
    "--var",
    "variables",
    multiple=True,
    callback=parse_var,
    help="Template variable in KEY=VALUE format (can be used multiple times)",
)
@click.option("--no-input", is_flag=True, help="Do not prompt for input (use defaults)")
def init_cmd(
    template_name: str,
    project_name: str | None,
    pretty_name: str | None,
    variables: dict[str, Any],
    no_input: bool,
):
    """Initialize a new project from a template (empty directory only).

    TEMPLATE_NAME is the name of the template directory in the boilerplate directory.
    This command only works in empty directories.

    For non-interactive usage, provide --name and any required template variables:

    \b
      boilersync init my-template --name my_project --var author_name="John Doe"
    """
    init(
        template_name,
        target_dir=Path.cwd(),
        project_name=project_name,
        pretty_name=pretty_name,
        template_variables=variables if variables else None,
        no_input=no_input,
    )
