import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from git import Repo

from boilersync.commands.pull import get_template_inheritance_chain
from boilersync.paths import paths
from boilersync.template_processor import process_file_extensions
from boilersync.template_sources import TemplateSource, resolve_source_from_boilersync


@dataclass(frozen=True)
class TemplateOwnership:
    template_ref: str
    template_dir: Path
    template_repo_dir: Path
    source_relative_path: str


def copy_template_without_interpolation(
    template_source: TemplateSource,
    target_dir: Path,
    ownership_map: dict[str, TemplateOwnership],
) -> None:
    """Copy template directory without any interpolation.

    Args:
        template_source: Source template metadata
        target_dir: Target directory to copy to
        ownership_map: Mapping of rendered template paths to owning template sources
    """
    template_dir = template_source.template_dir

    def copy_item(src_path: Path, dst_path: Path) -> None:
        """Recursively copy files and directories without interpolation."""
        if src_path.is_file():
            if src_path.name == "template.json":
                return
            # Process file extensions but don't interpolate names
            final_name = process_file_extensions(dst_path.name)
            final_dst_path = dst_path.parent / final_name

            # Copy the file without processing content
            final_dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, final_dst_path)
            ownership_map[final_dst_path.relative_to(target_dir).as_posix()] = (
                TemplateOwnership(
                    template_ref=template_source.ref,
                    template_dir=template_source.template_dir,
                    template_repo_dir=template_source.local_repo_path,
                    source_relative_path=src_path.relative_to(template_dir).as_posix(),
                )
            )

        elif src_path.is_dir():
            # Create directory without interpolating name
            final_dst_path = dst_path

            # Create the directory
            final_dst_path.mkdir(exist_ok=True)

            # Recursively copy contents
            for item in src_path.iterdir():
                item_dst = final_dst_path / item.name
                copy_item(item, item_dst)

    # Copy all items in the source directory
    for item in template_dir.iterdir():
        item_dst = target_dir / item.name
        copy_item(item, item_dst)


def copy_template_chain_without_interpolation(
    inheritance_chain: list[TemplateSource],
    target_dir: Path,
) -> dict[str, TemplateOwnership]:
    ownership_map: dict[str, TemplateOwnership] = {}
    for template_source in inheritance_chain:
        copy_template_without_interpolation(template_source, target_dir, ownership_map)
    return ownership_map


def template_file_contains_block_syntax(file_path: Path) -> bool:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return False

    return any(
        token in content
        for token in ("$${% block", "$${ super()", "$${% endblock", "$${% extends")
    )


def reverse_interpolate_path_name(path_name: str, context: Dict[str, Any]) -> str:
    """Reverse interpolate variables in file or folder names.

    This replaces interpolated values with their template placeholders.
    Only handles uppercase NAME_* variables for filenames and folder names.

    Args:
        path_name: The interpolated path name
        context: Dictionary of variables that were used for interpolation

    Returns:
        Path name with interpolated values replaced by template placeholders
    """
    result = path_name

    # Sort NAME_* variables by value length (longest first) to avoid partial replacements
    name_vars = [
        (key, value)
        for key, value in context.items()
        if key.startswith("NAME_") and key.isupper()
    ]
    name_vars.sort(key=lambda x: len(str(x[1])), reverse=True)

    # Only reverse interpolate uppercase NAME_* variables for filenames and folder names
    for key, value in name_vars:
        str_value = str(value)
        if str_value in result:
            result = result.replace(str_value, key)

    return result


def reverse_interpolate_file_content(file_path: Path, context: Dict[str, Any]) -> None:
    """Reverse interpolate variables in file content.

    This replaces interpolated values with Jinja2 template syntax using lowercase variables.

    Args:
        file_path: Path to the file to reverse interpolate
        context: Dictionary of variables that were used for interpolation
    """
    try:
        content = file_path.read_text(encoding="utf-8")

        # Sort variables by value length (longest first) to avoid partial replacements
        sorted_vars = sorted(
            [(key, value) for key, value in context.items()],
            key=lambda x: len(str(x[1])),
            reverse=True,
        )

        # Replace interpolated values with template syntax
        for key, value in sorted_vars:
            # Convert value to string for replacement
            str_value = str(value)

            # Only replace if the value is substantial enough to avoid false positives
            # and doesn't contain common words that might cause issues
            if (
                len(str_value) >= 3
                and str_value not in ["true", "false", "True", "False", "1", "0"]
                and not str_value.isdigit()
            ):
                # For file content, use lowercase variable names with custom delimiters
                # Convert uppercase NAME_* variables to lowercase for content
                if key.startswith("NAME_") and key.isupper():
                    # Convert NAME_SNAKE to name_snake, etc.
                    lowercase_key = key.lower()
                else:
                    # Use the variable as-is for non-NAME variables
                    lowercase_key = key

                template_syntax = f"$${{{lowercase_key}}}"

                # Use word boundaries for more precise replacement when possible
                import re

                # For alphanumeric values, use word boundaries
                if str_value.replace("_", "").replace("-", "").isalnum():
                    pattern = r"\b" + re.escape(str_value) + r"\b"
                    content = re.sub(pattern, template_syntax, content)
                else:
                    # For other values, do simple replacement
                    content = content.replace(str_value, template_syntax)

        # Write the reverse interpolated content back to the file
        file_path.write_text(content, encoding="utf-8")
    except Exception:
        # If we can't process the file (e.g., binary file), skip it
        pass


def reverse_interpolate_project_files(
    target_dir: Path, context: Dict[str, Any]
) -> None:
    """Reverse interpolate all files in the target directory.

    Args:
        target_dir: Directory containing files to reverse interpolate
        context: Dictionary of variables that were used for interpolation
    """
    # First, collect all files and directories
    files_to_process = []
    dirs_to_process = []

    for item in target_dir.rglob("*"):
        if item.is_file():
            # Skip .boilersync file and git files
            if item.name in [".boilersync", ".git"] or ".git/" in str(
                item.relative_to(target_dir)
            ):
                continue
            files_to_process.append(item)
        elif item.is_dir():
            # Skip git directories
            if item.name == ".git" or ".git/" in str(item.relative_to(target_dir)):
                continue
            dirs_to_process.append(item)

    # Process files first (reverse interpolate content and rename if needed)
    for file_path in files_to_process:
        # Reverse interpolate file content
        reverse_interpolate_file_content(file_path, context)

        # Check if filename needs reverse interpolation
        original_name = file_path.name
        reverse_interpolated_name = reverse_interpolate_path_name(
            original_name, context
        )

        if reverse_interpolated_name != original_name:
            # Rename the file to use template placeholders
            new_path = file_path.parent / reverse_interpolated_name
            # If target file already exists (e.g., from template copy), remove it first
            # since it's stale and will be replaced by the project's version
            if new_path.exists():
                new_path.unlink()
            file_path.rename(new_path)

    # Process directories in reverse depth order (deepest first) to avoid conflicts
    dirs_to_process.sort(key=lambda p: len(p.parts), reverse=True)

    for dir_path in dirs_to_process:
        # Check if directory name needs reverse interpolation
        original_name = dir_path.name
        reverse_interpolated_name = reverse_interpolate_path_name(
            original_name, context
        )

        if reverse_interpolated_name != original_name:
            # Rename the directory to use template placeholders
            new_path = dir_path.parent / reverse_interpolated_name
            # If target directory already exists (e.g., from template copy), remove it first
            # since its contents are stale and will be replaced by the project's version
            if new_path.exists() and new_path.is_dir():
                shutil.rmtree(new_path)
            dir_path.rename(new_path)


def copy_changed_files_to_template(
    temp_repo_dir: Path,
    ownership_map: dict[str, TemplateOwnership],
    leaf_template_source: TemplateSource,
    files_to_add: Optional[List[str]] = None,
) -> tuple[List[str], set[Path]]:
    """Copy changed files from the temporary repo back to the template.

    Args:
        temp_repo_dir: Path to the temporary git repository
        ownership_map: Mapping of template-style file paths to owning template metadata
        leaf_template_source: Leaf template metadata for new files
        files_to_add: Optional list of additional files to copy from project root

    Returns:
        List of files that were updated in the template and the affected template repos
    """
    try:
        repo = Repo(temp_repo_dir)

        # Get the list of changed files between the initial commit and HEAD
        changed_files = []

        # Get all commits
        commits = list(repo.iter_commits())
        if len(commits) < 2:
            # If there's only one commit, check for any files that exist
            click.echo(
                "📝 Only initial commit found, checking for any committed files..."
            )
            for item in repo.git.ls_tree("-r", "--name-only", "HEAD").splitlines():
                changed_files.append(item)
        else:
            # Compare HEAD with the initial commit (last in the list)
            initial_commit = commits[-1]
            head_commit = commits[0]

            # Get differences between initial commit and HEAD
            for item in initial_commit.diff(head_commit):
                if item.a_path:
                    changed_files.append(item.a_path)
                if item.b_path and item.b_path != item.a_path:
                    changed_files.append(item.b_path)

        # Add any additional files specified by the user
        if files_to_add:
            changed_files.extend(files_to_add)
            changed_files = list(set(changed_files))  # Remove duplicates

        if not changed_files:
            click.echo("📝 No committed changes detected.")
            return [], set()

        click.echo(f"📋 Found {len(changed_files)} file(s) to process:")
        for file_path in changed_files:
            if files_to_add and file_path in files_to_add:
                click.echo(f"  • {file_path} (added to template)")
            else:
                click.echo(f"  • {file_path}")

        blocked_files: list[tuple[str, Path]] = []
        for file_path in changed_files:
            source_file = temp_repo_dir / file_path
            if not source_file.exists():
                continue

            ownership = ownership_map.get(
                file_path,
                TemplateOwnership(
                    template_ref=leaf_template_source.ref,
                    template_dir=leaf_template_source.template_dir,
                    template_repo_dir=leaf_template_source.local_repo_path,
                    source_relative_path=file_path,
                ),
            )
            source_template_file = ownership.template_dir / ownership.source_relative_path
            if source_template_file.exists() and template_file_contains_block_syntax(
                source_template_file
            ):
                blocked_files.append((file_path, source_template_file))

        if blocked_files:
            blocked_summary = "\n".join(
                f"- {file_path} (source: {source_template_file})"
                for file_path, source_template_file in blocked_files
            )
            raise RuntimeError(
                "Refusing to push files derived from block-based template sources "
                "because BoilerSync cannot safely reconstruct the block structure:\n"
                f"{blocked_summary}"
            )

        updated_files = []
        updated_template_repos: set[Path] = set()

        for file_path in changed_files:
            source_file = temp_repo_dir / file_path

            # Skip if source file doesn't exist (deleted files)
            if not source_file.exists():
                click.echo(f"⚠️  Skipping deleted file: {file_path}")
                continue

            ownership = ownership_map.get(
                file_path,
                TemplateOwnership(
                    template_ref=leaf_template_source.ref,
                    template_dir=leaf_template_source.template_dir,
                    template_repo_dir=leaf_template_source.local_repo_path,
                    source_relative_path=file_path,
                ),
            )
            final_target = ownership.template_dir / ownership.source_relative_path

            # Create parent directories if they don't exist
            final_target.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            try:
                shutil.copy2(source_file, final_target)
                updated_files.append(file_path)
                updated_template_repos.add(ownership.template_repo_dir)
                action = "Added to template" if files_to_add and file_path in files_to_add else "Updated"
                click.echo(
                    f"✅ {action} ({ownership.template_ref} -> {ownership.source_relative_path}): {file_path}"
                )
            except Exception as e:
                click.echo(f"❌ Failed to copy {file_path}: {e}")

        return updated_files, updated_template_repos

    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        click.echo(f"❌ Error processing git changes: {e}")
        return [], set()


def copy_additional_files_to_temp(
    root_dir: Path, temp_dir: Path, files_to_add: List[str]
) -> None:
    """Copy additional files from project root to temp directory.

    Args:
        root_dir: Project root directory
        temp_dir: Temporary directory
        files_to_add: List of file paths to copy
    """
    for file_path in files_to_add:
        source_file = root_dir / file_path
        target_file = temp_dir / file_path

        if not source_file.exists():
            click.echo(f"⚠️  File not found in project: {file_path}")
            continue

        # Check if a .boilersync version exists in the template and prefer that
        boilersync_target = temp_dir / f"{file_path}.boilersync"
        if boilersync_target.exists():
            # Use the .boilersync version as the target
            final_target = boilersync_target
            target_type = ".boilersync version"
        else:
            # Use the original target (without .boilersync suffix for new files)
            final_target = target_file
            target_type = "regular file"

        # Create parent directories if they don't exist
        final_target.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(source_file, final_target)
            click.echo(f"📋 Copied additional file ({target_type}): {file_path}")
        except Exception as e:
            click.echo(f"❌ Failed to copy additional file {file_path}: {e}")


def push(files_to_add: Optional[List[str]] = None) -> None:
    """Show differences between current project and its template, then copy committed changes back.

    Creates a temporary directory with a fresh template initialization,
    then copies the current project files over it to show differences.
    After the user reviews the diff and commits desired changes, those
    committed changes are copied back to the original template.

    Args:
        files_to_add: Optional list of additional files to add to the template

    Raises:
        FileNotFoundError: If no .boilersync file is found
        subprocess.CalledProcessError: If git or github commands fail
    """
    # Find the root directory (where .boilersync file is located)
    root_dir = paths.root_dir
    boilersync_file = paths.boilersync_json_path

    # Read the template metadata from .boilersync file
    try:
        with open(boilersync_file, "r", encoding="utf-8") as f:
            boilersync_data = json.load(f)
        template_source = resolve_source_from_boilersync(
            boilersync_data.get("template"),
        )
        inheritance_chain = get_template_inheritance_chain(template_source.canonical_ref)
        leaf_template_source = inheritance_chain[-1]
        template_ref = leaf_template_source.canonical_ref
        project_name = boilersync_data.get("name_snake")
        pretty_name = boilersync_data.get("name_pretty")
        collected_variables = boilersync_data.get("variables", {})
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as e:
        raise FileNotFoundError(
            f"Could not read template metadata from {boilersync_file}: {e}"
        ) from e

    click.echo(f"🔍 Creating diff for template '{template_ref}'...")

    # Create a hash-based temporary directory name
    root_path_str = str(root_dir.resolve())
    path_hash = hashlib.md5(root_path_str.encode()).hexdigest()[:8]
    temp_base_dir = Path(tempfile.gettempdir()) / f"boilersync-diff-{path_hash}"
    project_temp_dir = temp_base_dir / "project"

    # Create the directory if it doesn't exist
    project_temp_dir.mkdir(parents=True, exist_ok=True)

    # Change to temp directory and run init
    original_cwd = Path.cwd()
    try:
        import os

        # Clear temp directory before initializing
        shutil.rmtree(project_temp_dir, ignore_errors=True)
        project_temp_dir.mkdir(parents=True, exist_ok=True)

        os.chdir(project_temp_dir)

        # Copy the full inheritance chain without interpolation first
        click.echo("📦 Copying fresh template inheritance chain without interpolation...")
        if not leaf_template_source.template_dir.exists():
            raise FileNotFoundError(
                f"Template '{template_ref}' not found at {leaf_template_source.template_dir}"
            )

        ownership_map = copy_template_chain_without_interpolation(
            inheritance_chain,
            project_temp_dir,
        )

        # Initialize git repo if it doesn't exist
        git_dir = project_temp_dir / ".git"
        if not git_dir.exists():
            click.echo("🔧 Setting up git repository...")
            repo = Repo.init(project_temp_dir)
            repo.git.add(A=True)
            repo.git.commit(m=f"Fresh template: {template_ref}")
        else:
            raise Exception("Git repo already exists")

        # Copy files from root directory to temp directory, overwriting
        click.echo("📋 Copying current project files...")
        copy_project_files(root_dir, project_temp_dir)

        # Copy any additional files specified by the user
        if files_to_add:
            click.echo("📋 Copying additional files to add to template...")
            copy_additional_files_to_temp(root_dir, project_temp_dir, files_to_add)

        # Reverse interpolate the copied project files
        click.echo("🔄 Reverse interpolating project files...")

        # Build the context from the saved data
        from boilersync.interpolation_context import interpolation_context

        interpolation_context.clear()
        interpolation_context.set_project_names(project_name, pretty_name)
        interpolation_context.set_collected_variables(collected_variables)
        context = interpolation_context.get_context()

        reverse_interpolate_project_files(project_temp_dir, context)

        # Open in GitHub Desktop
        click.echo("🚀 Opening in GitHub Desktop...")
        subprocess.run(["github", str(project_temp_dir)], check=True)

        # Show the persistent directory path
        click.echo(f"📂 Persistent comparison directory: {project_temp_dir}")
        click.echo("💡 This directory will be reused for future diffs of this project.")
        click.echo(
            "⚠️  IMPORTANT: Please commit any changes you want to push back to the template!"
        )
        click.echo(
            "⏳ Press Enter when you're done reviewing and committing changes..."
        )
        input()

        # Perform a hard reset to the last commit before processing changes
        click.echo("\n🔄 Performing hard reset to last commit...")
        try:
            repo = Repo(project_temp_dir)
            repo.git.reset("--hard", "HEAD")
            click.echo("✅ Reset to last commit completed")
        except Exception as e:
            click.echo(f"❌ Failed to reset to last commit: {e}")
            click.echo("⚠️  Proceeding anyway, but uncommitted changes may be included")

        # After user presses enter, copy changed files back to template
        click.echo("\n🔄 Processing committed changes to update template...")
        updated_files, updated_template_repos = copy_changed_files_to_template(
            project_temp_dir,
            ownership_map,
            leaf_template_source,
            files_to_add,
        )

        if updated_files:
            click.echo(
                f"\n✅ Successfully updated {len(updated_files)} file(s) in template '{template_ref}':"
            )
            for file_path in updated_files:
                click.echo(f"  • {file_path}")
            for repo_dir in sorted(updated_template_repos):
                click.echo(f"\n📁 Updated template repository: {repo_dir}")

            # Open the affected template source repos in GitHub Desktop
            for repo_dir in sorted(updated_template_repos):
                click.echo(f"🚀 Opening template source repository in GitHub Desktop: {repo_dir}")
                subprocess.run(["github", str(repo_dir)], check=True)
        else:
            click.echo("\n📝 No files were updated in the template.")

    finally:
        os.chdir(original_cwd)


def copy_project_files(source_dir: Path, target_dir: Path) -> None:
    """Copy files from source to target, preserving structure and overwriting.

    Args:
        source_dir: Source directory (current project)
        target_dir: Target directory (temp directory with fresh template)
    """
    for item in source_dir.rglob("*"):
        if item.is_file():
            # Skip .boilersync file and git files
            if item.name in [".boilersync", ".git"] or ".git/" in str(
                item.relative_to(source_dir)
            ):
                continue

            # Calculate relative path and target location
            rel_path = item.relative_to(source_dir)
            target_file = target_dir / rel_path

            # Create parent directories if they don't exist
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            shutil.copy2(item, target_file)


@click.command(name="push")
@click.option(
    "--add-files",
    multiple=True,
    help="Additional files to add to the template (can be used multiple times)",
)
def push_cmd(add_files):
    """Show differences between current project and its template, then copy committed changes back to the template repo.

    Creates a temporary directory with a fresh template initialization,
    then copies the current project files over it to show differences in GitHub Desktop.
    After reviewing the diff and committing desired changes, those committed changes
    are copied back to the original template source repository.

    Only committed changes will be copied back - make sure to commit any changes
    you want to push to the template before pressing Enter.
    """
    files_to_add = list(add_files) if add_files else None
    push(files_to_add)
