import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from git import Repo

from boilersync.commands.diff import diff_cmd
from boilersync.commands.init import init


def _commit_template_repo(repo_dir: Path) -> Repo:
    repo = Repo.init(repo_dir)
    with repo.config_writer() as config:
        config.set_value("user", "name", "BoilerSync Tests")
        config.set_value("user", "email", "tests@example.invalid")
    repo.git.add(A=True)
    if repo.is_dirty(untracked_files=True):
        repo.index.commit("Update template fixtures")
    return repo


def _write_template(
    template_root_dir: Path,
    *,
    org: str,
    repo: str,
    subdir: str,
    files: dict[str, str],
    config: dict[str, object] | None = None,
) -> Repo:
    repo_dir = template_root_dir / org / repo
    template_dir = repo_dir / subdir
    template_dir.mkdir(parents=True, exist_ok=True)

    if config is not None:
        (template_dir / "template.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

    for relative_path, contents in files.items():
        output_path = template_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(contents, encoding="utf-8")

    return _commit_template_repo(repo_dir)


class TestDiff(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _template_ref(self, subdir: str) -> str:
        return f"{self.org}/{self.repo}#{subdir}"

    def _invoke_diff(self, target_dir: Path, *args: str):
        runner = CliRunner()
        return runner.invoke(
            diff_cmd,
            list(args),
            env={
                "BOILERSYNC_ROOT_DIR": str(target_dir),
                "BOILERSYNC_TEMPLATE_DIR": str(self.template_root_dir),
            },
        )

    def test_name_status_reports_inherited_parent_file_changes(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent",
            files={"common.txt.boilersync": "Parent $${name_snake}\n"},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child",
            files={"child.txt.boilersync": "Child $${name_snake}\n"},
            config={"extends": "parent", "skip_git": True},
        )

        target_dir = self.root / "project"
        target_dir.mkdir()
        init(
            self._template_ref("child"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_project"},
            no_input=True,
        )
        (target_dir / "common.txt").write_text(
            "Updated demo_project\n", encoding="utf-8"
        )

        result = self._invoke_diff(target_dir, "--name-status")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("M\tcommon.txt", result.output)

    def test_diff_compares_against_rendered_template(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="rendered-template",
            files={
                "app.py.boilersync": 'class $${name_pascal}Config:\n    name = "$${name_snake}"\n'
            },
            config={"skip_git": True},
        )

        target_dir = self.root / "rendered-project"
        target_dir.mkdir()
        init(
            self._template_ref("rendered-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "rendered_project"},
            no_input=True,
        )

        clean_result = self._invoke_diff(target_dir, "--name-status")
        self.assertEqual(clean_result.exit_code, 0, clean_result.output)
        self.assertIn("No divergence from template.", clean_result.output)

        (target_dir / "app.py").write_text(
            'class RenderedProjectConfig:\n    name = "changed_project"\n',
            encoding="utf-8",
        )
        patch_result = self._invoke_diff(target_dir, "--patch")

        self.assertEqual(patch_result.exit_code, 0, patch_result.output)
        self.assertIn('name = "rendered_project"', patch_result.output)
        self.assertIn('name = "changed_project"', patch_result.output)
        self.assertNotIn("$${name_snake}", patch_result.output)

    def test_diff_ignores_dependency_directories(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="node-template",
            files={"package.json.boilersync": '{"name": "$${name_kebab}"}\n'},
            config={"skip_git": True},
        )

        target_dir = self.root / "node-project"
        target_dir.mkdir()
        init(
            self._template_ref("node-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "node_project"},
            no_input=True,
        )
        (target_dir / "node_modules" / "left-pad").mkdir(parents=True)
        (target_dir / "node_modules" / "left-pad" / "index.js").write_text(
            "module.exports = null;\n",
            encoding="utf-8",
        )

        result = self._invoke_diff(target_dir, "--name-status")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No divergence from template.", result.output)

    def test_diff_respects_project_gitignore(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="ignored-template",
            files={
                ".gitignore.boilersync": "ignored.txt\n",
                "package.json.boilersync": '{"name": "$${name_kebab}"}\n',
            },
            config={"skip_git": True},
        )

        target_dir = self.root / "ignored-project"
        target_dir.mkdir()
        init(
            self._template_ref("ignored-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "ignored_project"},
            no_input=True,
        )
        Repo.init(target_dir)
        (target_dir / "ignored.txt").write_text(
            "ignored local output\n",
            encoding="utf-8",
        )

        result = self._invoke_diff(target_dir, "--name-status")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No divergence from template.", result.output)

    def test_diff_ignores_claude_agents_compatibility_symlink(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="agents-template",
            files={"AGENTS.md.boilersync": "$${name_pretty} instructions\n"},
            config={"skip_git": True},
        )

        target_dir = self.root / "agents-project"
        target_dir.mkdir()
        init(
            self._template_ref("agents-template"),
            target_dir=target_dir,
            template_variables={
                "name_snake": "agents_project",
                "name_pretty": "Agents Project",
            },
            no_input=True,
        )
        (target_dir / "CLAUDE.md").symlink_to("AGENTS.md")

        result = self._invoke_diff(target_dir, "--name-status")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No divergence from template.", result.output)

    def test_starter_files_are_excluded_by_default_and_included_on_request(
        self,
    ) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="starter-template",
            files={"README.starter.md": "Starter $${name_snake}\n"},
            config={"skip_git": True},
        )

        target_dir = self.root / "starter-project"
        target_dir.mkdir()
        init(
            self._template_ref("starter-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "starter_project"},
            no_input=True,
        )
        (target_dir / "README.md").write_text(
            "Project-owned README\n", encoding="utf-8"
        )

        default_result = self._invoke_diff(target_dir, "--name-status")
        include_result = self._invoke_diff(
            target_dir,
            "--name-status",
            "--include-starter",
        )

        self.assertEqual(default_result.exit_code, 0, default_result.output)
        self.assertIn("No divergence from template.", default_result.output)
        self.assertEqual(include_result.exit_code, 0, include_result.output)
        self.assertIn("M\tREADME.md", include_result.output)

    def test_unregistered_extra_folder_remains_parent_drift(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent-template",
            files={"parent.txt.boilersync": "Parent $${name_snake}\n"},
            config={"skip_git": True},
        )

        parent_dir = self.root / "parent-project"
        parent_dir.mkdir()
        init(
            self._template_ref("parent-template"),
            target_dir=parent_dir,
            template_variables={"name_snake": "parent_project"},
            no_input=True,
        )
        extra_file = parent_dir / "extra" / "owned.txt"
        extra_file.parent.mkdir()
        extra_file.write_text("Project-owned extra file\n", encoding="utf-8")

        result = self._invoke_diff(parent_dir, "--json")
        payload = json.loads(result.output)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(payload["projects"][0]["changed_count"], 1)
        self.assertEqual(
            payload["projects"][0]["changed_files"][0],
            {"status": "A", "path": "extra/owned.txt"},
        )

    def test_json_reports_changed_files(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="json-template",
            files={"app.txt.boilersync": "App $${name_snake}\n"},
            config={"skip_git": True},
        )

        target_dir = self.root / "json-project"
        target_dir.mkdir()
        init(
            self._template_ref("json-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "json_project"},
            no_input=True,
        )
        (target_dir / "app.txt").write_text("Changed json_project\n", encoding="utf-8")

        result = self._invoke_diff(target_dir, "--json")
        payload = json.loads(result.output)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(payload["projects"][0]["changed_count"], 1)
        self.assertEqual(payload["projects"][0]["changed_files"][0]["path"], "app.txt")

    def test_children_option_reports_direct_child_changes(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent-template",
            files={"parent.txt.boilersync": "Parent $${name_snake}\n"},
            config={"skip_git": True},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"child.txt.boilersync": "Child $${name_snake}\n"},
            config={"skip_git": True},
        )

        parent_dir = self.root / "parent-project"
        child_dir = parent_dir / "child"
        parent_dir.mkdir()
        init(
            self._template_ref("parent-template"),
            target_dir=parent_dir,
            template_variables={"name_snake": "parent_project"},
            no_input=True,
        )
        child_dir.mkdir()
        init(
            self._template_ref("child-template"),
            target_dir=child_dir,
            template_variables={"name_snake": "child_project"},
            no_input=True,
        )
        (child_dir / "child.txt").write_text(
            "Changed child_project\n", encoding="utf-8"
        )

        result = self._invoke_diff(parent_dir, "--children", "--json")
        payload = json.loads(result.output)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(payload["projects"]), 2)
        self.assertEqual(payload["projects"][0]["changed_count"], 0)
        self.assertEqual(
            payload["projects"][1]["changed_files"][0]["path"], "child.txt"
        )

    def test_registered_child_is_excluded_from_parent_diff_by_default(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent-template",
            files={"parent.txt.boilersync": "Parent $${name_snake}\n"},
            config={"skip_git": True},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"child.txt.boilersync": "Child $${name_snake}\n"},
            config={"skip_git": True},
        )

        parent_dir = self.root / "parent-project"
        child_dir = parent_dir / "child"
        parent_dir.mkdir()
        init(
            self._template_ref("parent-template"),
            target_dir=parent_dir,
            template_variables={"name_snake": "parent_project"},
            no_input=True,
        )
        child_dir.mkdir()
        init(
            self._template_ref("child-template"),
            target_dir=child_dir,
            template_variables={"name_snake": "child_project"},
            no_input=True,
        )
        (child_dir / "child.txt").write_text(
            "Changed child_project\n", encoding="utf-8"
        )

        result = self._invoke_diff(parent_dir, "--json")
        payload = json.loads(result.output)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(payload["projects"]), 1)
        self.assertEqual(payload["projects"][0]["changed_count"], 0)

    def test_recursive_includes_descendants_but_children_does_not(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent-template",
            files={"parent.txt.boilersync": "Parent $${name_snake}\n"},
            config={"skip_git": True},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child-template",
            files={"child.txt.boilersync": "Child $${name_snake}\n"},
            config={"skip_git": True},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="grandchild-template",
            files={"grandchild.txt.boilersync": "Grandchild $${name_snake}\n"},
            config={"skip_git": True},
        )

        parent_dir = self.root / "parent-project"
        child_dir = parent_dir / "child"
        grandchild_dir = child_dir / "grandchild"
        parent_dir.mkdir()
        init(
            self._template_ref("parent-template"),
            target_dir=parent_dir,
            template_variables={"name_snake": "parent_project"},
            no_input=True,
        )
        child_dir.mkdir()
        init(
            self._template_ref("child-template"),
            target_dir=child_dir,
            template_variables={"name_snake": "child_project"},
            no_input=True,
        )
        grandchild_dir.mkdir()
        init(
            self._template_ref("grandchild-template"),
            target_dir=grandchild_dir,
            template_variables={"name_snake": "grandchild_project"},
            no_input=True,
        )
        (grandchild_dir / "grandchild.txt").write_text(
            "Changed grandchild_project\n", encoding="utf-8"
        )

        children_result = self._invoke_diff(parent_dir, "--children", "--json")
        children_payload = json.loads(children_result.output)
        recursive_result = self._invoke_diff(parent_dir, "--recursive", "--json")
        recursive_payload = json.loads(recursive_result.output)

        self.assertEqual(children_result.exit_code, 0, children_result.output)
        self.assertEqual(len(children_payload["projects"]), 2)
        self.assertEqual(
            [project["changed_count"] for project in children_payload["projects"]],
            [0, 0],
        )

        self.assertEqual(recursive_result.exit_code, 0, recursive_result.output)
        self.assertEqual(len(recursive_payload["projects"]), 3)
        self.assertEqual(
            [project["changed_count"] for project in recursive_payload["projects"]],
            [0, 0, 1],
        )
        self.assertEqual(
            recursive_payload["projects"][2]["changed_files"][0]["path"],
            "grandchild.txt",
        )


if __name__ == "__main__":
    unittest.main()
