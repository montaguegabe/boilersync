from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TemplateOwnership:
    template_ref: str
    template_dir: Path
    template_repo_dir: Path
    source_relative_path: str
