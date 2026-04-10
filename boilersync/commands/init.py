import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import click

from boilersync.commands.pull import (
    get_template_config,
    get_template_inheritance_chain,
    pull,
)
from boilersync.interpolation_context import interpolation_context
from boilersync.paths import paths
from boilersync.template_sources import TemplateSource
from boilersync.variable_collector import (
    convert_string_to_appropriate_type,
    create_jinja_environment,
)

logger = logging.getLogger(__name__)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _merge_runtime_config(inheritance_chain: list[TemplateSource]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "children": [],
        "hooks": {
            "pre_init": [],
            "post_init": [],
        },
        "github": {},
    }

    for template_source in inheritance_chain:
        template_config = get_template_config(template_source.template_dir)

        children = template_config.get("children", [])
        if children:
            if not isinstance(children, list):
                raise ValueError(
                    f"Template '{template_source.ref}' has invalid 'children' config: expected list"
                )
            config["children"].extend(children)

        hooks = template_config.get("hooks", {})
        if hooks:
            if not isinstance(hooks, dict):
                raise ValueError(
                    f"Template '{template_source.ref}' has invalid 'hooks' config: expected object"
                )
            for hook_key in ("pre_init", "post_init"):
                hook_steps = hooks.get(hook_key, [])
                if hook_steps:
                    if not isinstance(hook_steps, list):
                        raise ValueError(
                            f"Template '{template_source.ref}' has invalid '{hook_key}' config: expected list"
                        )
                    config["hooks"][hook_key].extend(hook_steps)

        github = template_config.get("github", {})
        if github:
            if not isinstance(github, dict):
                raise ValueError(
                    f"Template '{template_source.ref}' has invalid 'github' config: expected object"
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

    if _IDENTIFIER_PATTERN.match(rendered_condition):
        return False

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


def _inject_monorepo_conversion_hook(
    runtime_config: dict[str, Any],
    *,
    target_dir: Path,
) -> None:
    multi_json_path = target_dir / "multi.json"
    if not multi_json_path.exists():
        logger.warning(
            "⚠️  --monorepo was requested but no multi.json was found at "
            f"{multi_json_path}; skipping monorepo conversion hook."
        )
        return

    runtime_config["hooks"]["post_init"].append(
        {
            "id": "convert-monorepo",
            "run": "multi convert-monorepo --confirm",
        }
    )


def _resolve_child_template_ref(parent_template_ref: str, child_template_ref: str) -> str:
    if "#" in child_template_ref:
        return child_template_ref

    if "#" not in parent_template_ref:
        raise ValueError(
            "Child templates without a source qualifier require a source-qualified parent template ref."
        )

    parent_repo_locator, _ = parent_template_ref.split("#", 1)
    child_subdir = child_template_ref.strip().lstrip("/")
    if not child_subdir:
        raise ValueError("Child template ref cannot be empty.")
    return f"{parent_repo_locator}#{child_subdir}"


def init(
    template_ref: str,
    target_dir: Path,
    collected_variables: dict[str, Any] | None = None,
    template_variables: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    no_input: bool = False,
    run_hooks: bool = True,
    run_children: bool = True,
    monorepo: bool = False,
) -> None:
    """Initialize a new project from a template (empty directory only).

    Args:
        template_ref: Template reference to initialize from

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
    inheritance_chain = get_template_inheritance_chain(template_ref)
    runtime_config = _merge_runtime_config(inheritance_chain)

    # Initialize the project
    pull(
        template_ref,
        allow_non_empty=False,
        include_starter=True,
        _recursive=False,
        collected_variables=normalized_template_variables,
        target_dir=target_dir,
        no_input=no_input,
    )

    # If we found a parent .boilersync, register this project as a child
    if parent_dir is not None:
        parent_boilersync_path = parent_dir / ".boilersync"
        paths.add_child_to_parent(target_dir, parent_boilersync_path)
        logger.info(f"📎 Registered as child project in parent: {parent_dir}")

    if monorepo:
        _inject_monorepo_conversion_hook(runtime_config, target_dir=target_dir)

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
                child_variables["name_snake"] = _render_string(
                    str(child_project_name),
                    runtime_context,
                )

            child_pretty_name = child.get("name_pretty")
            if child_pretty_name is not None:
                child_variables["name_pretty"] = _render_string(
                    str(child_pretty_name),
                    runtime_context,
                )

            child_template_ref = _resolve_child_template_ref(
                template_ref,
                child_template_name,
            )

            init(
                child_template_ref,
                target_dir=child_target_dir,
                template_variables=child_variables or None,
                options=options,
                no_input=no_input,
                run_hooks=run_hooks,
                run_children=run_children,
                monorepo=False,
            )

    if run_hooks:
        post_init_hooks = runtime_config["hooks"]["post_init"]
        _run_hooks(
            post_init_hooks,
            hook_name="post_init",
            target_dir=target_dir,
            context=runtime_context,
        )


def parse_key_value_options(value: tuple[str, ...]) -> dict[str, Any]:
    """Parse KEY=VALUE option values into a dictionary."""
    result: dict[str, Any] = {}
    for item in value:
        if "=" not in item:
            raise click.BadParameter(f"Value must be in KEY=VALUE format, got: {item}")
        key, val = item.split("=", 1)
        result[key.strip()] = convert_string_to_appropriate_type(val)
    return result


def parse_var(ctx, param, value: tuple[str, ...]) -> dict[str, Any]:
    """Parse --var options into a dictionary."""
    return parse_key_value_options(value)


def parse_option(ctx, param, value: tuple[str, ...]) -> dict[str, Any]:
    """Parse --option options into a dictionary."""
    return parse_key_value_options(value)


@click.command(name="init")
@click.argument("template_ref")
@click.option(
    "-v",
    "--var",
    "variables",
    multiple=True,
    callback=parse_var,
    help="Template variable in KEY=VALUE format (can be used multiple times)",
)
@click.option(
    "-o",
    "--option",
    "runtime_options",
    multiple=True,
    callback=parse_option,
    help="Template runtime option in KEY=VALUE format (can be used multiple times)",
)
@click.option(
    "--monorepo",
    is_flag=True,
    help="After init, convert the generated workspace to multi monorepo mode.",
)
@click.option(
    "--no-input",
    "--non-interactive",
    "no_input",
    is_flag=True,
    help="Do not prompt for input (non-interactive mode; use defaults and --var for required values)",
)
def init_cmd(
    template_ref: str,
    variables: dict[str, Any],
    runtime_options: dict[str, Any],
    monorepo: bool,
    no_input: bool,
):
    """Initialize a new project from a template (empty directory only).

    TEMPLATE_REF is either:
    - A source-qualified ref: ORG/REPO#SUBDIR
    - A GitHub URL ref: https://github.com/ORG/REPO.git#SUBDIR

    This command only works in empty directories.

    For non-interactive usage, provide --non-interactive and any required template variables:

    \b
      boilersync init your-org/your-templates#service --non-interactive --var name_snake=my_project --var author_name="John Doe"
    """
    init(
        template_ref,
        target_dir=Path.cwd(),
        template_variables=variables if variables else None,
        options=runtime_options if runtime_options else None,
        no_input=no_input,
        monorepo=monorepo,
    )
