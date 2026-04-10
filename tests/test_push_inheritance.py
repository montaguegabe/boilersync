import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git import Repo

from boilersync.commands.pull import get_template_inheritance_chain
from boilersync.commands.push import (
    copy_changed_files_to_template,
    copy_template_chain_without_interpolation,
)


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
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
    template_dir.mkdir(parents=True, exist_ok=True)

    if config is not None:
        (template_dir / "template.json").write_text(json.dumps(config), encoding="utf-8")

    for relative_path, contents in files.items():
        output_path = template_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(contents, encoding="utf-8")


class TestPushInheritance(unittest.TestCase):
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

    def test_copy_changed_files_routes_parent_owned_files_to_parent_template(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent",
            files={"common.txt.boilersync": "parent\n"},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child",
            files={"child.txt.boilersync": "child\n"},
            config={"extends": "parent"},
        )

        inheritance_chain = get_template_inheritance_chain(self._template_ref("child"))
        temp_repo_dir = self.root / "diff"
        temp_repo_dir.mkdir()
        ownership_map = copy_template_chain_without_interpolation(
            inheritance_chain,
            temp_repo_dir,
        )

        repo = Repo.init(temp_repo_dir)
        repo.git.add(A=True)
        repo.index.commit("baseline")

        (temp_repo_dir / "common.txt").write_text("updated parent\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("update common")

        updated_files, updated_template_repos = copy_changed_files_to_template(
            temp_repo_dir,
            ownership_map,
            inheritance_chain[-1],
        )

        parent_file = (
            self.template_root_dir / self.org / self.repo / "parent" / "common.txt.boilersync"
        )
        child_file = (
            self.template_root_dir / self.org / self.repo / "child" / "common.txt.boilersync"
        )

        self.assertEqual(updated_files, ["common.txt"])
        self.assertEqual(parent_file.read_text(encoding="utf-8"), "updated parent\n")
        self.assertFalse(child_file.exists())
        self.assertEqual(
            updated_template_repos,
            {self.template_root_dir / self.org / self.repo},
        )

    def test_copy_changed_files_refuses_block_based_round_trip(self) -> None:
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="parent",
            files={"README.md.boilersync": "Parent heading\n"},
        )
        _write_template(
            self.template_root_dir,
            org=self.org,
            repo=self.repo,
            subdir="child",
            files={
                "README.md.boilersync": (
                    "$${% block readme %}\n$${ super() }\nChild extra\n$${% endblock %}\n"
                )
            },
            config={"extends": "parent"},
        )

        inheritance_chain = get_template_inheritance_chain(self._template_ref("child"))
        temp_repo_dir = self.root / "diff-blocks"
        temp_repo_dir.mkdir()
        ownership_map = copy_template_chain_without_interpolation(
            inheritance_chain,
            temp_repo_dir,
        )

        repo = Repo.init(temp_repo_dir)
        repo.git.add(A=True)
        repo.index.commit("baseline")

        (temp_repo_dir / "README.md").write_text("Flattened output\n", encoding="utf-8")
        repo.git.add(A=True)
        repo.index.commit("flattened change")

        with self.assertRaises(RuntimeError) as cm:
            copy_changed_files_to_template(
                temp_repo_dir,
                ownership_map,
                inheritance_chain[-1],
            )

        self.assertIn("Refusing to push files derived from block-based", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
