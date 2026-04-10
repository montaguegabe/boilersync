from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git import Repo

from boilersync.commands.init import init
from boilersync.interpolation_context import interpolation_context


def _commit_template_repo(repo_dir: Path) -> None:
    repo = Repo.init(repo_dir)
    with repo.config_writer() as config:
        config.set_value("user", "name", "BoilerSync Tests")
        config.set_value("user", "email", "tests@example.com")
    repo.git.add(A=True)
    if repo.is_dirty(untracked_files=True):
        repo.index.commit("Update template fixtures")


def _write_template(
    template_root_dir: Path,
    *,
    org: str,
    repo: str,
    subdir: str,
    files: dict[str, str],
    config: dict[str, object] | None = None,
) -> None:
    repo_dir = template_root_dir / org / repo
    template_dir = repo_dir / subdir
    template_dir.mkdir(parents=True, exist_ok=True)

    if config is not None:
        (template_dir / "template.json").write_text(json.dumps(config), encoding="utf-8")

    for relative_path, contents in files.items():
        output_path = template_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(contents, encoding="utf-8")

    _commit_template_repo(repo_dir)


class TestInterpolationContextNameVariants(unittest.TestCase):
    def setUp(self) -> None:
        interpolation_context.clear()

    def tearDown(self) -> None:
        interpolation_context.clear()

    def test_generates_missing_snake_and_kebab_variants(self) -> None:
        interpolation_context.set_custom_variable("api_package_name_snake", "demo_api")
        interpolation_context.set_custom_variable("worker_name_kebab", "demo-worker")

        context = interpolation_context.get_context()

        self.assertEqual(context["api_package_name_kebab"], "demo-api")
        self.assertEqual(context["worker_name_snake"], "demo_worker")

    def test_does_not_override_explicit_variant(self) -> None:
        interpolation_context.set_custom_variable("api_package_name_snake", "demo_api")
        interpolation_context.set_collected_variable(
            "api_package_name_kebab",
            "custom-kebab",
        )

        context = interpolation_context.get_context()

        self.assertEqual(context["api_package_name_kebab"], "custom-kebab")


class TestDerivedNameVariantsInInit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.template_root_dir = self.root / "templates"
        self.template_root_dir.mkdir()
        self.org = "acme"
        self.repo = "templates"
        self.env_patcher = patch.dict(
            os.environ,
            {"BOILERSYNC_TEMPLATE_DIR": str(self.template_root_dir)},
            clear=False,
        )
        self.env_patcher.start()
        interpolation_context.clear()

    def tearDown(self) -> None:
        interpolation_context.clear()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _template_ref(self, subdir: str) -> str:
        return f"{self.org}/{self.repo}#{subdir}"

    def test_child_path_can_use_derived_kebab_variable(self) -> None:
        target_dir = self.root / "workspace"
        target_dir.mkdir()

        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template",
            files={"README.md.boilersync": "Workspace\n"},
            config={
                "children": [
                    {
                        "template": self._template_ref("child-template"),
                        "path": "$${api_package_name_kebab}",
                    }
                ],
                "skip_git": True,
            },
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"child.txt.boilersync": "child\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("workspace-template"),
            target_dir=target_dir,
            no_input=True,
            template_variables={
                "name_snake": "demo_workspace",
                "api_package_name_snake": "demo_api",
            },
        )

        self.assertTrue((target_dir / "demo-api" / "child.txt").exists())
