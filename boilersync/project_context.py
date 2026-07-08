from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boilersync.commands.pull import get_template_inheritance_chain
from boilersync.interpolation_context import interpolation_context
from boilersync.project_metadata import load_project_metadata
from boilersync.template_sources import TemplateSource, resolve_source_from_boilersync


@dataclass(frozen=True)
class ProjectTemplateContext:
    project_dir: Path
    metadata: dict[str, Any]
    template_source: TemplateSource
    inheritance_chain: list[TemplateSource]
    leaf_template_source: TemplateSource
    template_ref: str
    name_snake: str | None
    name_pretty: str | None
    variables: dict[str, Any]


def resolve_project_template_context(project_dir: Path) -> ProjectTemplateContext:
    metadata = load_project_metadata(project_dir)
    template_source = resolve_source_from_boilersync(metadata.get("template"))
    inheritance_chain = get_template_inheritance_chain(template_source.canonical_ref)
    leaf_template_source = inheritance_chain[-1]

    variables = dict(metadata.get("variables", {}))
    name_snake = metadata.get("name_snake")
    name_pretty = metadata.get("name_pretty")

    return ProjectTemplateContext(
        project_dir=project_dir,
        metadata=metadata,
        template_source=template_source,
        inheritance_chain=inheritance_chain,
        leaf_template_source=leaf_template_source,
        template_ref=leaf_template_source.canonical_ref,
        name_snake=name_snake if isinstance(name_snake, str) else None,
        name_pretty=name_pretty if isinstance(name_pretty, str) else None,
        variables=variables,
    )


def set_interpolation_context(project_context: ProjectTemplateContext) -> dict[str, Any]:
    interpolation_context.clear()
    interpolation_context.set_project_names(
        project_context.name_snake,
        project_context.name_pretty,
    )
    interpolation_context.set_collected_variables(project_context.variables)
    return interpolation_context.get_context()
