import shutil
from pathlib import Path
from typing import Any, Dict, Set

from jinja2 import FileSystemLoader

from boilersync.interpolation_context import interpolation_context
from boilersync.variable_collector import (
    collect_missing_variables,
    create_jinja_environment,
    extract_variables_from_template_content,
)


def interpolate_path_name(path_name: str, context: Dict[str, Any]) -> str:
    """Interpolate variables in file or folder names.

    This does simple string replacement for uppercase NAME_* variables only.
    No Jinja2 syntax is used here - just direct substitution.

    Args:
        path_name: The original path name that may contain variables
        context: Dictionary of variables to substitute

    Returns:
        Path name with variables substituted
    """
    result = path_name
    # Only interpolate uppercase NAME_* variables for filenames and folder names
    for key, value in context.items():
        if key.startswith("NAME_") and key.isupper():
            result = result.replace(key, str(value))
    return result


def remove_boilersync_extension(file_name: str) -> str:
    """Remove .boilersync extension from file name if present.

    Args:
        file_name: Original file name

    Returns:
        File name with .boilersync extension removed if it was present
    """
    if file_name.endswith(".boilersync"):
        return file_name[: -len(".boilersync")]
    return file_name


def remove_starter_extension(file_name: str) -> str:
    """Remove .starter extension from file name if present at the beginning of the extension.

    Args:
        file_name: Original file name

    Returns:
        File name with .starter extension removed if it was present
    """
    # Split the filename into name and extensions
    parts = file_name.split(".")
    if len(parts) > 1:
        # Check if the first extension part is 'starter'
        if parts[1] == "starter":
            # Remove the .starter part and rejoin
            return parts[0] + "." + ".".join(parts[2:]) if len(parts) > 2 else parts[0]
    return file_name


def process_file_extensions(file_name: str) -> str:
    """Process file name to remove both .starter and .boilersync extensions.

    Args:
        file_name: Original file name

    Returns:
        File name with special extensions removed
    """
    # First remove .starter extension
    result = remove_starter_extension(file_name)
    # Then remove .boilersync extension
    result = remove_boilersync_extension(result)
    return result


def scan_template_for_variables(source_dir: Path) -> Set[str]:
    """Scan the entire template directory for variables in template content.

    Args:
        source_dir: Source template directory to scan

    Returns:
        Set of template variables found in all template files
    """
    template_variables = set()

    def scan_item(path: Path) -> None:
        """Recursively scan files for template variables."""
        if path.is_file():
            # Scan all files for template variables since all files are processed with Jinja2
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


def process_template_file(file_path: Path, context: Dict[str, Any]) -> None:
    """Process a single template file with Jinja2 using custom delimiters.

    Args:
        file_path: Path to the file to process
        context: Variables for template interpolation
    """
    # Create Jinja2 environment with custom delimiters and file loader
    env = create_jinja_environment(loader=FileSystemLoader(file_path.parent))
    env.keep_trailing_newline = True

    try:
        # Load and render the template
        template = env.get_template(file_path.name)
        rendered_content = template.render(context)

        # Write the rendered content back to the file
        file_path.write_text(rendered_content, encoding="utf-8")
    except Exception:
        # If Jinja2 processing fails (e.g., no template syntax in file),
        # leave the file as-is
        pass


def copy_and_process_template(
    source_dir: Path, target_dir: Path, context: Dict[str, Any]
) -> None:
    """Copy template directory and process all files and folders.

    Args:
        source_dir: Source template directory
        target_dir: Target directory to copy to
        context: Variables for interpolation
    """

    def process_item(src_path: Path, dst_path: Path) -> None:
        """Recursively process files and directories."""
        if src_path.is_file():
            if src_path.name == "template.json":
                return

            # Interpolate the destination file name
            interpolated_name = interpolate_path_name(dst_path.name, context)

            # Remove .boilersync extension if present
            final_name = process_file_extensions(interpolated_name)
            final_dst_path = dst_path.parent / final_name

            # Copy the file
            shutil.copy2(src_path, final_dst_path)

            # Process the file content with Jinja2
            process_template_file(final_dst_path, context)

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


def process_template_directory(
    template_dir: Path, target_dir: Path, no_input: bool
) -> None:
    """Process a template directory with the current interpolation context.

    Args:
        template_dir: Source template directory
        target_dir: Target directory to process into
    """
    # First, scan the template for all variables
    template_variables = scan_template_for_variables(template_dir)

    # Collect any missing variables from the user
    collect_missing_variables(template_variables, no_input)

    # Now process the template with the complete context
    context = interpolation_context.get_context()
    copy_and_process_template(template_dir, target_dir, context)
