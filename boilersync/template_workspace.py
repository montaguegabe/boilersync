import shutil
from pathlib import Path
from typing import Any

from boilersync.template_ownership import TemplateOwnership
from boilersync.template_processor import (
    apply_template_defaults,
    copy_and_process_template,
    process_file_extensions,
)
from boilersync.template_sources import TemplateSource


def record_template_ownership(
    ownership_map: dict[str, TemplateOwnership],
    *,
    target_root: Path,
    template_source: TemplateSource,
    source_path: Path,
    target_path: Path,
) -> None:
    ownership_map[target_path.relative_to(target_root).as_posix()] = TemplateOwnership(
        template_ref=template_source.ref,
        template_dir=template_source.template_dir,
        template_repo_dir=template_source.local_repo_path,
        source_relative_path=source_path.relative_to(
            template_source.template_dir
        ).as_posix(),
    )


def copy_rendered_template(
    template_source: TemplateSource,
    target_dir: Path,
    ownership_map: dict[str, TemplateOwnership],
    context: dict[str, Any],
) -> None:
    def record_ownership(src_path: Path, final_dst_path: Path) -> None:
        record_template_ownership(
            ownership_map,
            target_root=target_dir,
            template_source=template_source,
            source_path=src_path,
            target_path=final_dst_path,
        )

    copy_and_process_template(
        template_source.template_dir,
        target_dir,
        context,
        on_file_copied=record_ownership,
    )


def copy_rendered_template_chain(
    inheritance_chain: list[TemplateSource],
    target_dir: Path,
    context: dict[str, Any],
) -> dict[str, TemplateOwnership]:
    ownership_map: dict[str, TemplateOwnership] = {}
    for template_source in inheritance_chain:
        apply_template_defaults(template_source.template_dir)
        copy_rendered_template(template_source, target_dir, ownership_map, context)
    return ownership_map


def copy_template_without_interpolation(
    template_source: TemplateSource,
    target_dir: Path,
    ownership_map: dict[str, TemplateOwnership],
) -> None:
    template_dir = template_source.template_dir

    def copy_item(src_path: Path, dst_path: Path) -> None:
        if src_path.is_file():
            if src_path.name == "template.json":
                return

            final_name = process_file_extensions(dst_path.name)
            final_dst_path = dst_path.parent / final_name
            final_dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, final_dst_path)
            record_template_ownership(
                ownership_map,
                target_root=target_dir,
                template_source=template_source,
                source_path=src_path,
                target_path=final_dst_path,
            )
            return

        if src_path.is_dir():
            final_dst_path = dst_path
            final_dst_path.mkdir(exist_ok=True)
            for item in src_path.iterdir():
                copy_item(item, final_dst_path / item.name)

    for item in template_dir.iterdir():
        copy_item(item, target_dir / item.name)


def copy_template_chain_without_interpolation(
    inheritance_chain: list[TemplateSource],
    target_dir: Path,
) -> dict[str, TemplateOwnership]:
    ownership_map: dict[str, TemplateOwnership] = {}
    for template_source in inheritance_chain:
        copy_template_without_interpolation(template_source, target_dir, ownership_map)
    return ownership_map
