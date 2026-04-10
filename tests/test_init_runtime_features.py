from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git import Repo

from boilersync.commands.init import (
    _create_github_repo,
    _evaluate_condition,
    _merge_runtime_config,
    init,
)
from boilersync.commands.pull import get_template_inheritance_chain


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


class TestInitRuntimeFeatures(unittest.TestCase):
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

    def _template_ref(self, subdir: str) -> str:
        return f"{self.org}/{self.repo}#{subdir}"

    def _canonical_template_ref(self, subdir: str) -> str:
        return f"https://github.com/{self.org}/{self.repo}.git#{subdir}"

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_merge_runtime_config_inheritance(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent-template",
            files={"README.md": "parent"},
            config={
                "hooks": {"post_init": [{"id": "parent_hook", "run": "echo parent"}]},
                "github": {"create_repo": True, "private": True},
                "children": [
                    {"template": self._template_ref("child-template"), "path": "child-dir"}
                ],
            },
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"README.md": "child"},
            config={
                "extends": self._template_ref("parent-template"),
                "hooks": {"post_init": [{"id": "child_hook", "run": "echo child"}]},
                "github": {"private": False},
            },
        )

        merged = _merge_runtime_config(
            get_template_inheritance_chain(self._template_ref("child-template"))
        )
        self.assertEqual(len(merged["children"]), 1)
        self.assertEqual(
            [hook["id"] for hook in merged["hooks"]["post_init"]],
            ["parent_hook", "child_hook"],
        )
        self.assertTrue(merged["github"]["create_repo"])
        self.assertFalse(merged["github"]["private"])

    def test_init_with_children_and_hooks(self) -> None:
        target_dir = self.root / "workspace"
        target_dir.mkdir()

        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template",
            files={"README.md.boilersync": "Workspace $${name_snake}\n"},
            config={
                "children": [
                    {
                        "template": self._template_ref("child-template"),
                        "path": "$${name_kebab}-child",
                        "condition": "with_frontend",
                        "variables": {"child_message": "$${workspace_message}"},
                    }
                ],
                "hooks": {
                    "post_init": [
                        {
                            "id": "write_hook_output",
                            "run": "printf '%s' \"$HOOK_VALUE\" > hook-output.txt",
                            "env": {"HOOK_VALUE": "$${workspace_message}"},
                        }
                    ]
                },
                "skip_git": True,
            },
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"child.txt.boilersync": "$${child_message}\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("workspace-template"),
            target_dir=target_dir,
            no_input=True,
            template_variables={
                "name_snake": "demo_workspace",
                "workspace_message": "hello child",
            },
            options={"with_frontend": True},
        )

        self.assertTrue((target_dir / "README.md").exists())
        self.assertEqual(
            (target_dir / "hook-output.txt").read_text(encoding="utf-8"),
            "hello child",
        )

        child_dir = target_dir / "demo-workspace-child"
        self.assertEqual(
            (child_dir / "child.txt").read_text(encoding="utf-8").strip(),
            "hello child",
        )

        root_boilersync_data = json.loads((target_dir / ".boilersync").read_text())
        self.assertEqual(
            root_boilersync_data["template"],
            self._canonical_template_ref("workspace-template"),
        )
        self.assertNotIn("source", root_boilersync_data)
        self.assertEqual(root_boilersync_data["children"], ["demo-workspace-child"])

    def test_init_with_local_child_template_name(self) -> None:
        target_dir = self.root / "workspace-local-child"
        target_dir.mkdir()

        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template-local",
            files={"README.md.boilersync": "Workspace\n"},
            config={
                "children": [
                    {
                        "template": "child-template-local",
                        "path": "child-project",
                    }
                ],
                "skip_git": True,
            },
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template-local",
            files={"child.txt.boilersync": "child\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("workspace-template-local"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_workspace"},
            no_input=True,
        )

        self.assertTrue((target_dir / "child-project" / "child.txt").exists())

    @patch("boilersync.commands.init.subprocess.run")
    def test_init_monorepo_adds_post_init_conversion_hook(self, mock_run) -> None:
        target_dir = self.root / "workspace-monorepo"
        target_dir.mkdir()

        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template-monorepo",
            files={
                "README.md.boilersync": "Workspace\n",
                "multi.json.boilersync": '{"repos": []}\n',
            },
            config={
                "hooks": {
                    "post_init": [
                        {"id": "existing-hook", "run": "echo existing"},
                    ]
                },
                "skip_git": True,
            },
        )

        mock_run.return_value = subprocess.CompletedProcess(
            args=["hook"],
            returncode=0,
        )

        init(
            self._template_ref("workspace-template-monorepo"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_workspace"},
            no_input=True,
            monorepo=True,
        )

        called_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("multi convert-monorepo --confirm", called_commands)

    @patch("boilersync.commands.init.subprocess.run")
    def test_init_monorepo_warns_when_multi_json_missing(self, mock_run) -> None:
        target_dir = self.root / "workspace-no-multi-json"
        target_dir.mkdir()

        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template-no-multi",
            files={"README.md.boilersync": "Workspace\n"},
            config={"skip_git": True},
        )

        with self.assertLogs("boilersync.commands.init", level="WARNING") as captured:
            init(
                self._template_ref("workspace-template-no-multi"),
                target_dir=target_dir,
                template_variables={"name_snake": "demo_workspace"},
                no_input=True,
                monorepo=True,
            )

        self.assertTrue(
            any(
                "--monorepo was requested but no multi.json was found"
                in message
                for message in captured.output
            )
        )
        mock_run.assert_not_called()

    @patch("boilersync.commands.init.subprocess.run")
    def test_create_github_repo_with_condition(self, mock_run) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                ["gh", "api", "user", "--jq", ".login"],
                0,
                stdout="gabe\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["gh", "repo", "view", "gabe/demo-repo"],
                1,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["gh", "repo", "create", "gabe/demo-repo", "--public"],
                0,
                stdout="",
                stderr="",
            ),
        ]

        _create_github_repo(
            {
                "create_repo": True,
                "repo_name": "demo-repo",
                "private": False,
                "condition": "with_github",
            },
            target_dir=self.root,
            context={"with_github": True},
        )

        self.assertEqual(
            mock_run.call_args_list[2].args[0],
            ["gh", "repo", "create", "gabe/demo-repo", "--public"],
        )

    @patch("boilersync.commands.init.subprocess.run")
    def test_create_github_repo_skips_when_condition_variable_missing(self, mock_run) -> None:
        _create_github_repo(
            {
                "create_repo": True,
                "repo_name": "demo-repo",
                "private": False,
                "condition": "with_github",
            },
            target_dir=self.root,
            context={},
        )
        mock_run.assert_not_called()

    def test_evaluate_condition_unknown_identifier_is_false(self) -> None:
        self.assertFalse(_evaluate_condition("with_github", {}))


if __name__ == "__main__":
    unittest.main()
