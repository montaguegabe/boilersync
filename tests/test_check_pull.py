import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from git import Repo

from boilersync.commands.check_pull import check_pull, check_pull_cmd
from boilersync.commands.init import init
from boilersync.commands.pull import pull


def _commit_template_repo(repo_dir: Path) -> Repo:
    repo = Repo.init(repo_dir)
    with repo.config_writer() as config:
        config.set_value("user", "name", "BoilerSync Tests")
        config.set_value("user", "email", "tests@example.com")
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
        (template_dir / "template.json").write_text(json.dumps(config), encoding="utf-8")

    for relative_path, contents in files.items():
        output_path = template_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(contents, encoding="utf-8")

    return _commit_template_repo(repo_dir)


class TestCheckPull(unittest.TestCase):
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

    def test_init_records_template_commit(self) -> None:
        target_dir = self.root / "project"
        target_dir.mkdir()
        repo = _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="service-template",
            files={"README.md.boilersync": "Service $${name_snake}\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("service-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_service"},
            no_input=True,
        )

        boilersync_data = json.loads((target_dir / ".boilersync").read_text())
        self.assertEqual(boilersync_data["template_commit"], repo.head.commit.hexsha)

    def test_pull_updates_template_commit_and_preserves_children(self) -> None:
        target_dir = self.root / "workspace"
        target_dir.mkdir()
        repo = _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="workspace-template",
            files={"README.md.boilersync": "Workspace v1\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("workspace-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_workspace"},
            no_input=True,
        )

        boilersync_path = target_dir / ".boilersync"
        initial_data = json.loads(boilersync_path.read_text())
        initial_data["children"] = ["apps/frontend"]
        boilersync_path.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

        repo_dir = self.template_root_dir / self.org / self.repo
        (repo_dir / "workspace-template" / "README.md.boilersync").write_text(
            "Workspace v2\n",
            encoding="utf-8",
        )
        repo = _commit_template_repo(repo_dir)

        original_cwd = Path.cwd()
        os.chdir(target_dir)
        try:
            pull(allow_non_empty=True, no_input=True, _recursive=False)
        finally:
            os.chdir(original_cwd)

        updated_data = json.loads(boilersync_path.read_text())
        self.assertEqual(updated_data["template_commit"], repo.head.commit.hexsha)
        self.assertEqual(updated_data["children"], ["apps/frontend"])

    def test_check_pull_reports_due_when_template_repo_head_changes(self) -> None:
        target_dir = self.root / "status-project"
        target_dir.mkdir()
        repo = _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="status-template",
            files={"README.md.boilersync": "Status v1\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("status-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "status_project"},
            no_input=True,
        )

        recorded_commit = repo.head.commit.hexsha
        repo_dir = self.template_root_dir / self.org / self.repo
        (repo_dir / "status-template" / "README.md.boilersync").write_text(
            "Status v2\n",
            encoding="utf-8",
        )
        repo = _commit_template_repo(repo_dir)

        status = check_pull(target_dir)

        self.assertTrue(status.due_for_pull)
        self.assertEqual(status.recorded_template_commit, recorded_commit)
        self.assertEqual(status.current_template_commit, repo.head.commit.hexsha)

    def test_check_pull_cmd_exits_nonzero_when_project_is_due(self) -> None:
        target_dir = self.root / "cmd-project"
        target_dir.mkdir()
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="cmd-template",
            files={"README.md.boilersync": "Command v1\n"},
            config={"skip_git": True},
        )

        init(
            self._template_ref("cmd-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "cmd_project"},
            no_input=True,
        )

        repo_dir = self.template_root_dir / self.org / self.repo
        (repo_dir / "cmd-template" / "README.md.boilersync").write_text(
            "Command v2\n",
            encoding="utf-8",
        )
        _commit_template_repo(repo_dir)

        runner = CliRunner()
        result = runner.invoke(
            check_pull_cmd,
            [],
            env={
                "BOILERSYNC_ROOT_DIR": str(target_dir),
                "BOILERSYNC_TEMPLATE_DIR": str(self.template_root_dir),
            },
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Due for pull: yes", result.output)


if __name__ == "__main__":
    unittest.main()
