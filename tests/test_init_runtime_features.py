from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boilersync.commands.init import (
    _create_github_repo,
    _merge_runtime_config,
    init,
)


def _write_template(
    boilerplate_dir: Path,
    template_name: str,
    *,
    files: dict[str, str],
    config: dict[str, object] | None = None,
) -> None:
    template_dir = boilerplate_dir / template_name
    template_dir.mkdir(parents=True, exist_ok=True)

    if config is not None:
        (template_dir / "template.json").write_text(json.dumps(config), encoding="utf-8")

    for relative_path, contents in files.items():
        output_path = template_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(contents, encoding="utf-8")


class TestInitRuntimeFeatures(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.boilerplate_dir = self.root / "boilerplate"
        self.boilerplate_dir.mkdir()
        self.env_patcher = patch.dict(
            os.environ,
            {"BOILERSYNC_TEMPLATE_DIR": str(self.boilerplate_dir)},
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_merge_runtime_config_inheritance(self) -> None:
        _write_template(
            self.boilerplate_dir,
            "parent-template",
            files={"README.md": "parent"},
            config={
                "hooks": {"post_init": [{"id": "parent_hook", "run": "echo parent"}]},
                "github": {"create_repo": True, "private": True},
                "children": [{"template": "child-template", "path": "child-dir"}],
            },
        )
        _write_template(
            self.boilerplate_dir,
            "child-template",
            files={"README.md": "child"},
            config={
                "extends": "parent-template",
                "hooks": {"post_init": [{"id": "child_hook", "run": "echo child"}]},
                "github": {"private": False},
            },
        )

        merged = _merge_runtime_config(["parent-template", "child-template"])
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
            self.boilerplate_dir,
            "workspace-template",
            files={"README.md.boilersync": "Workspace $${name_snake}\n"},
            config={
                "children": [
                    {
                        "template": "child-template",
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
            self.boilerplate_dir,
            "child-template",
            files={"child.txt.boilersync": "$${child_message}\n"},
            config={"skip_git": True},
        )

        init(
            "workspace-template",
            target_dir=target_dir,
            project_name="demo_workspace",
            no_input=True,
            template_variables={"workspace_message": "hello child"},
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
        self.assertEqual(root_boilersync_data["children"], ["demo-workspace-child"])

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


if __name__ == "__main__":
    unittest.main()
