import json
import logging
import os
from pathlib import Path
from typing import Any, Set

import click
from git import InvalidGitRepositoryError, Repo

from boilersync.interpolation_context import interpolation_context
from boilersync.names import normalize_to_snake, snake_to_pretty
from boilersync.paths import paths
from boilersync.template_processor import (
    process_template_directory,
)
from boilersync.utils import prompt_or_default
from boilersync.variable_collector import collect_missing_variables

logger = logging.getLogger(__name__)


def is_git_repo_clean(target_dir: Path) -> bool:
    """Check if the git repository is clean (no uncommitted changes).

    Args:
        target_dir: Directory to check

    Returns:
        True if repo is clean or not a git repo, False if there are uncommitted changes
    """
    try:
        repo = Repo(target_dir)
        # Check if there are any uncommitted changes
        return not repo.is_dirty(untracked_files=True)
    except InvalidGitRepositoryError:
        # Not a git repository, consider it "clean"
        return True


def is_starter_file(file_path: Path) -> bool:
    """Check if a file is a starter file (has .starter extension).

    Args:
        file_path: Path to check

    Returns:
        True if the file has .starter as the first extension
    """
    # Split the filename into name and extensions
    parts = file_path.name.split(".")
    if len(parts) > 1:
        # Check if the first extension part is 'starter'
        return parts[1] == "starter"
    return False


def scan_template_for_variables_excluding_starter(source_dir: Path) -> Set[str]:
    """Scan template directory for variables, excluding starter files.

    Args:
        source_dir: Source template directory to scan

    Returns:
        Set of template variables found in non-starter template files
    """
    from boilersync.variable_collector import extract_variables_from_template_content

    template_variables = set()

    def scan_item(path: Path) -> None:
        """Recursively scan files for template variables, excluding starter files."""
        if path.is_file():
            # Skip starter files
            if is_starter_file(path):
                return

            # Scan all other files for template variables
            try:
                content = path.read_text(encoding="utf-8")
                content_vars = extract_variables_from_template_content(content)
                template_variables.update(content_vars)
            except Exception:
                # If we can't read the file (e.g., binary file), skip it
                pass
        elif path.is_dir():
            # Recursively scan directory contents
            for item in path.iterdir():
                scan_item(item)

    # Scan all items in the source directory
    for item in source_dir.iterdir():
        scan_item(item)

    return template_variables


def copy_and_process_template_excluding_starter(
    source_dir: Path, target_dir: Path, context: dict[str, Any], no_input: bool
) -> None:
    """Copy template directory and process files, excluding starter files.

    Args:
        source_dir: Source template directory
        target_dir: Target directory to copy to
        context: Variables for interpolation
    """
    import shutil

    from boilersync.template_processor import (
        interpolate_path_name,
        process_file_extensions,
        process_template_file,
    )

    def process_item(src_path: Path, dst_path: Path) -> None:
        """Recursively process files and directories, excluding starter files."""
        if src_path.is_file():
            # Skip starter files
            if is_starter_file(src_path):
                return

            # Interpolate the destination file name
            interpolated_name = interpolate_path_name(dst_path.name, context)

            # Remove .boilersync extension if present
            final_name = process_file_extensions(interpolated_name)
            final_dst_path = dst_path.parent / final_name

            # Copy the file
            shutil.copy2(src_path, final_dst_path)

            # Process the file content with Jinja2
            process_template_file(final_dst_path, context, no_input=no_input)

        elif src_path.is_dir():
            # Interpolate the destination directory name
            interpolated_name = interpolate_path_name(dst_path.name, context)
            final_dst_path = dst_path.parent / interpolated_name

            # Create the directory
            final_dst_path.mkdir(exist_ok=True)

            # Recursively process contents
            for item in src_path.iterdir():
                item_dst = final_dst_path / item.name
                process_item(item, item_dst)

    # Process all items in the source directory
    for item in source_dir.iterdir():
        item_dst = target_dir / item.name
        process_item(item, item_dst)


def process_template_directory_excluding_starter(
    template_dir: Path, target_dir: Path, no_input: bool
) -> None:
    """Process a template directory excluding starter files.

    Args:
        template_dir: Source template directory
        target_dir: Target directory to process into
    """
    # First, scan the template for all variables (excluding starter files)
    template_variables = scan_template_for_variables_excluding_starter(template_dir)

    # Collect any missing variables from the user
    collect_missing_variables(template_variables, no_input)

    # Now process the template with the complete context (excluding starter files)
    context = interpolation_context.get_context()
    copy_and_process_template_excluding_starter(
        template_dir, target_dir, context, no_input=no_input
    )


def pull_children(boilersync_path: Path, include_starter: bool = False) -> None:
    """Pull updates for all child projects listed in a .boilersync file.

    Args:
        boilersync_path: Path to the .boilersync file containing children
        include_starter: Whether to include starter files when pulling child updates
    """
    child_paths = paths.get_children_from_boilersync(boilersync_path)

    if not child_paths:
        return

    logger.info(f"\n🔄 Found {len(child_paths)} child project(s) to update:")

    for child_path in child_paths:
        logger.info(f"  📁 {child_path.relative_to(boilersync_path.parent)}")

    # Save current directory
    original_cwd = Path.cwd()

    try:
        for child_path in child_paths:
            logger.info(f"\n🔄 Updating child project: {child_path.name}")

            # Change to child directory
            os.chdir(child_path)

            try:
                # Pull updates for the child project (without recursing to avoid infinite loops)
                pull(
                    allow_non_empty=True,
                    include_starter=include_starter,
                    _recursive=False,
                )
                logger.info(f"✅ Updated child project: {child_path.name}")
            except Exception as e:
                logger.info(f"❌ Failed to update child project {child_path.name}: {e}")

    finally:
        # Always restore original directory
        os.chdir(original_cwd)


def get_template_config(template_dir: Path) -> dict[str, Any]:
    """Get the template.json configuration if it exists.

    Args:
        template_dir: The template directory to check

    Returns:
        The template configuration dict, or empty dict if not found
    """
    template_json_path = template_dir / "template.json"
    if not template_json_path.exists():
        return {}

    try:
        with open(template_json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return {}


def get_parent_template(template_dir: Path) -> str | None:
    """Get the parent template name from template.json if it exists.

    Args:
        template_dir: The template directory to check

    Returns:
        The parent template name if found, None otherwise
    """
    config = get_template_config(template_dir)
    # Support both "extends" and "parent" keys for backwards compatibility
    return config.get("extends") or config.get("parent")


def get_template_inheritance_chain(
    template_name: str, visited: Set[str] | None = None
) -> list[str]:
    """Get the full inheritance chain for a template, from root parent to child.

    Args:
        template_name: The template to get the inheritance chain for
        visited: Set of already visited templates to detect circular dependencies

    Returns:
        List of template names in order from root parent to the given template

    Raises:
        ValueError: If a circular dependency is detected
        FileNotFoundError: If a template in the chain doesn't exist
    """
    if visited is None:
        visited = set()

    if template_name in visited:
        raise ValueError(
            f"Circular dependency detected in template inheritance: {template_name}"
        )

    visited.add(template_name)

    template_dir = paths.boilerplate_dir / template_name
    if not template_dir.exists():
        raise FileNotFoundError(
            f"Template '{template_name}' not found in {paths.boilerplate_dir}"
        )

    parent_name = get_parent_template(template_dir)
    if parent_name is None:
        # This is the root template
        return [template_name]

    # Recursively get parent chain and append this template
    parent_chain = get_template_inheritance_chain(parent_name, visited.copy())
    return parent_chain + [template_name]


def should_skip_git(inheritance_chain: list[str]) -> bool:
    """Check if any template in the inheritance chain has skip_git: true.

    Args:
        inheritance_chain: List of template names in inheritance order

    Returns:
        True if any template has skip_git: true, False otherwise
    """
    for template_name in inheritance_chain:
        template_dir = paths.boilerplate_dir / template_name
        config = get_template_config(template_dir)
        if config.get("skip_git", False):
            return True
    return False


def pull(
    template_name: str | None = None,
    *,
    project_name: str | None = None,
    pretty_name: str | None = None,
    collected_variables: dict[str, Any] | None = None,
    allow_non_empty: bool = False,
    include_starter: bool = False,
    no_input: bool = False,
    target_dir: Path | None = None,
    _recursive: bool = True,
) -> None:
    """Pull template/boilerplate changes to the current project.

    Args:
        template_name: Name of the template to use from the boilerplate directory (auto-detected if None)
        project_name: Optional predefined project name (snake_case)
        pretty_name: Optional predefined pretty name
        collected_variables: Optional pre-collected variables to restore
        allow_non_empty: If True, allow pulling into non-empty directories (requires clean git repo)
        include_starter: If True, include starter files when pulling template changes
        _recursive: If True, also pull updates for child projects (internal parameter)

    Raises:
        FileNotFoundError: If the template directory doesn't exist or .boilersync file not found
        FileExistsError: If the target directory is not empty and allow_non_empty is False
        RuntimeError: If allow_non_empty is True but git repo has uncommitted changes
        ValueError: If a circular dependency is detected in template inheritance
    """
    target_dir = target_dir or Path.cwd()

    # Auto-detect template name from .boilersync file if not provided
    if template_name is None:
        try:
            boilersync_file = paths.boilersync_json_path
            with open(boilersync_file, "r", encoding="utf-8") as f:
                boilersync_data = json.load(f)
            template_name = boilersync_data["template"]
            # Also get the saved project details
            if project_name is None:
                project_name = boilersync_data.get("name_snake")
            if pretty_name is None:
                pretty_name = boilersync_data.get("name_pretty")
            if collected_variables is None:
                collected_variables = boilersync_data.get("variables", {})
            logger.info(
                f"📋 Auto-detected template '{template_name}' from .boilersync file"
            )
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
            raise FileNotFoundError(
                f"Could not auto-detect template name from .boilersync file: {e}. "
                f"Please specify template name explicitly or run from a directory with .boilersync file."
            ) from e

    # At this point template_name should not be None
    assert template_name is not None, "Template name should be set by now"

    # Get the full inheritance chain for the template
    try:
        inheritance_chain = get_template_inheritance_chain(template_name)
        if len(inheritance_chain) > 1:
            logger.info(
                f"🔗 Template inheritance chain: {' → '.join(inheritance_chain)}"
            )
    except ValueError as e:
        raise ValueError(f"Template inheritance error: {e}") from e

    # Verify all templates in the chain exist
    for tmpl_name in inheritance_chain:
        template_dir = paths.boilerplate_dir / tmpl_name
        if not template_dir.exists():
            raise FileNotFoundError(
                f"Template '{tmpl_name}' not found in {paths.boilerplate_dir}"
            )

    # Check if directory has any files besides .DS_Store
    has_files = any(p for p in target_dir.iterdir() if p.name != ".DS_Store")

    if has_files:
        if not allow_non_empty:
            raise FileExistsError(f"Target directory is not empty: {target_dir}")
        else:
            # Check if git repo is clean
            if not is_git_repo_clean(target_dir):
                raise RuntimeError(
                    "Cannot pull into non-empty directory with uncommitted changes. "
                    "Please commit or stash your changes first."
                )
            logger.info("⚠️  Pulling into non-empty directory (git repo is clean)")

    # If project names are provided, use them; otherwise prompt user
    if project_name is not None:
        snake_name = project_name
        # Auto-generate pretty name if not provided
        final_pretty_name = (
            pretty_name if pretty_name is not None else snake_to_pretty(project_name)
        )
        logger.info(f"\n🚀 Pulling from template '{template_name}'")
        logger.info(f"📝 Using project name: {snake_name}")
        logger.info(f"📝 Using pretty name: {final_pretty_name}")
    else:
        # Get the default snake_case name from the directory
        directory_name = target_dir.name
        default_snake_name = normalize_to_snake(directory_name)
        default_pretty_name = (
            pretty_name
            if pretty_name is not None
            else snake_to_pretty(default_snake_name)
        )

        # Prompt user for project names
        logger.info(f"\n🚀 Pulling from template '{template_name}'")
        logger.info("=" * 50)

        snake_name = prompt_or_default(
            "Project name (snake_case)",
            default=default_snake_name,
            type=str,
            no_input=no_input,
        )

        final_pretty_name = prompt_or_default(
            "Pretty name for display",
            default=default_pretty_name,
            type=str,
            no_input=no_input,
        )

        logger.info("=" * 50)

    # Set up interpolation context with project names
    interpolation_context.set_project_names(snake_name, final_pretty_name)

    # Restore any pre-collected variables
    if collected_variables:
        interpolation_context.set_collected_variables(collected_variables)

    # Process each template in the inheritance chain
    for i, tmpl_name in enumerate(inheritance_chain):
        template_dir = paths.boilerplate_dir / tmpl_name

        if len(inheritance_chain) > 1:
            if i == 0:
                logger.info(f"\n📦 Processing parent template '{tmpl_name}'...")
            elif i == len(inheritance_chain) - 1:
                logger.info(f"\n📦 Processing child template '{tmpl_name}'...")
            else:
                logger.info(f"\n📦 Processing intermediate template '{tmpl_name}'...")

        # Process the template directory with interpolation
        if include_starter:
            # Include all files including starter files
            process_template_directory(template_dir, target_dir, no_input=no_input)
        else:
            # Exclude starter files
            process_template_directory_excluding_starter(
                template_dir, target_dir, no_input=no_input
            )

    # Get all collected variables to save them
    collected_variables = interpolation_context.get_collected_variables()

    # Create .boilersync file to track the template (save the final child template name)
    boilersync_file = target_dir / ".boilersync"
    boilersync_data = {
        "template": template_name,
        "name_snake": snake_name,
        "name_pretty": final_pretty_name,
        "variables": collected_variables,
    }

    with open(boilersync_file, "w", encoding="utf-8") as f:
        json.dump(boilersync_data, f, indent=2)

    if has_files and allow_non_empty:
        logger.info(
            f"\n✅ Template '{template_name}' pulled successfully into existing project!"
        )
    else:
        logger.info(
            f"\n✅ Project initialized successfully from template '{template_name}'!"
        )
    logger.info("📁 Created .boilersync file to track template origin")

    # Initialize git repo and commit if include_starter is True and .git doesn't exist
    # Skip if any template in the inheritance chain has skip_git: true
    if include_starter and not should_skip_git(inheritance_chain):
        git_dir = target_dir / ".git"
        if not git_dir.exists():
            logger.info("\n🔧 Initializing git repository...")
            repo = Repo.init(target_dir)
            repo.git.add(".")
            repo.index.commit(f"Initial commit from template '{template_name}'")
            logger.info("✅ Git repository initialized and all changes committed")

    # Pull updates for child projects if recursive is enabled
    if _recursive:
        pull_children(boilersync_file, include_starter)


@click.command(name="pull")
@click.argument("template_name", required=False)
@click.option(
    "--include-starter",
    is_flag=True,
    help="Include starter files when pulling template changes",
)
@click.option("--no-children", is_flag=True, help="Skip updating child projects")
def pull_cmd(template_name: str | None, include_starter: bool, no_children: bool):
    """Pull template/boilerplate changes to the current project.

    TEMPLATE_NAME is the name of the template directory in the boilerplate directory.
    If not provided, will auto-detect from the nearest .boilersync file.
    Can be used in non-empty directories if the git repository is clean.

    Templates can inherit from parent templates by including a template.json file
    with a "parent" key. When pulling, parent templates are processed first,
    then child templates, allowing for hierarchical template inheritance.

    By default, starter files (files with .starter extension) are excluded.
    Use --include-starter to include them.

    This command will also automatically pull updates for any child projects
    listed in the .boilersync file. Use --no-children to skip child updates.
    """
    pull(
        template_name,
        allow_non_empty=True,
        include_starter=include_starter,
        _recursive=not no_children,
    )
