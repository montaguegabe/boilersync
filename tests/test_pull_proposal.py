import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git import Repo

from boilersync.commands.init import init
from boilersync.commands.pull_proposal import (
    apply_patch_file,
    apply_proposed_file,
    create_pull_proposal,
)


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


class TestPullProposal(unittest.TestCase):
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

    def _init_project(self) -> Path:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="service-template",
            files={"app.txt.boilersync": "App v1 $${name_snake}\n"},
            config={"skip_git": True},
        )
        target_dir = self.root / "project"
        target_dir.mkdir()
        init(
            self._template_ref("service-template"),
            target_dir=target_dir,
            template_variables={"name_snake": "demo_service"},
            no_input=True,
        )

        repo_dir = self.template_root_dir / self.org / self.repo
        template_dir = repo_dir / "service-template"
        (template_dir / "app.txt.boilersync").write_text(
            "App v2 $${name_snake}\n",
            encoding="utf-8",
        )
        (template_dir / "new.txt.boilersync").write_text(
            "New $${name_snake}\n",
            encoding="utf-8",
        )
        _commit_template_repo(repo_dir)
        return target_dir

    def test_create_pull_proposal_does_not_mutate_project(self) -> None:
        target_dir = self._init_project()
        Repo.init(target_dir)
        (target_dir / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (target_dir / "ignored.txt").write_text(
            "ignored local output\n",
            encoding="utf-8",
        )
        (target_dir / "node_modules" / "left-pad").mkdir(parents=True)
        (target_dir / "node_modules" / "left-pad" / "index.js").write_text(
            "module.exports = null;\n",
            encoding="utf-8",
        )
        workspace_dir = self.root / "proposal-workspace"

        proposal = create_pull_proposal(
            target_dir,
            workspace_dir=workspace_dir,
        )

        self.assertEqual(
            (target_dir / "app.txt").read_text(encoding="utf-8"),
            "App v1 demo_service\n",
        )
        self.assertFalse((target_dir / "new.txt").exists())
        self.assertEqual(
            (proposal.proposal_dir / "app.txt").read_text(encoding="utf-8"),
            "App v2 demo_service\n",
        )
        self.assertEqual(
            (proposal.proposal_dir / "new.txt").read_text(encoding="utf-8"),
            "New demo_service\n",
        )
        self.assertFalse((proposal.proposal_dir / "ignored.txt").exists())
        self.assertFalse((proposal.proposal_dir / "node_modules").exists())
        self.assertEqual(
            proposal.changed_files,
            [
                {"status": "M", "path": ".boilersync"},
                {"status": "M", "path": "app.txt"},
                {"status": "A", "path": "new.txt"},
            ],
        )

    def test_apply_proposed_file_copies_one_file_to_project(self) -> None:
        target_dir = self._init_project()
        proposal = create_pull_proposal(
            target_dir,
            workspace_dir=self.root / "proposal-workspace",
        )

        applied_path = apply_proposed_file(
            proposal.proposal_dir,
            Path("new.txt"),
            project_dir=target_dir,
        )

        self.assertEqual(applied_path, (target_dir / "new.txt").resolve())
        self.assertEqual(
            (target_dir / "new.txt").read_text(encoding="utf-8"),
            "New demo_service\n",
        )
        self.assertEqual(
            (target_dir / "app.txt").read_text(encoding="utf-8"),
            "App v1 demo_service\n",
        )

    def test_apply_patch_applies_selected_hunk_to_project(self) -> None:
        target_dir = self._init_project()
        proposal = create_pull_proposal(
            target_dir,
            workspace_dir=self.root / "proposal-workspace",
        )
        patch_path = self.root / "app.patch"
        patch = subprocess.run(
            ["git", "-C", str(proposal.proposal_dir), "diff", "--", "app.txt"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        patch_path.write_text(patch, encoding="utf-8")

        apply_patch_file(patch_path, project_dir=target_dir)

        self.assertEqual(
            (target_dir / "app.txt").read_text(encoding="utf-8"),
            "App v2 demo_service\n",
        )
        self.assertFalse((target_dir / "new.txt").exists())


if __name__ == "__main__":
    unittest.main()
