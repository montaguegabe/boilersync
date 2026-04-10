import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boilersync.commands.pull import (
    get_parent_template,
    get_template_inheritance_chain,
)


class TestTemplateInheritance(unittest.TestCase):
    def setUp(self):
        """Set up test environment with temporary directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.template_root_dir = Path(self.temp_dir.name)
        self.org = "acme"
        self.repo = "templates"
        self.repo_dir = self.template_root_dir / self.org / self.repo
        (self.repo_dir / ".git").mkdir(parents=True)
        self.env_patcher = patch.dict(
            os.environ,
            {"BOILERSYNC_TEMPLATE_DIR": str(self.template_root_dir)},
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        """Clean up test environment."""
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _template_ref(self, name: str) -> str:
        return f"{self.org}/{self.repo}#{name}"

    def _canonical_ref(self, name: str) -> str:
        return f"https://github.com/{self.org}/{self.repo}.git#{name}"

    def create_template_dir(self, name: str, parent: str | None = None) -> Path:
        """Create a template directory with optional parent reference.

        Args:
            name: Template name (subdir within repo)
            parent: Parent template subdir if any

        Returns:
            Path to the created template directory
        """
        template_dir = self.repo_dir / name
        template_dir.mkdir(parents=True)

        if parent:
            template_json = template_dir / "template.json"
            with open(template_json, "w", encoding="utf-8") as f:
                json.dump({"parent": self._template_ref(parent)}, f)

        # Create a dummy file to make it a valid template
        (template_dir / "README.md").write_text("# {{name_pretty}}\n", encoding="utf-8")

        return template_dir

    def test_get_parent_template_with_parent(self):
        """Test getting parent template when template.json exists with parent."""
        template_dir = self.create_template_dir("child", "parent")

        parent = get_parent_template(template_dir)
        self.assertEqual(parent, self._template_ref("parent"))

    def test_get_parent_template_without_parent(self):
        """Test getting parent template when template.json doesn't exist."""
        template_dir = self.create_template_dir("standalone")

        parent = get_parent_template(template_dir)
        self.assertIsNone(parent)

    def test_get_parent_template_empty_json(self):
        """Test getting parent template when template.json exists but has no parent."""
        template_dir = self.create_template_dir("child")
        template_json = template_dir / "template.json"
        with open(template_json, "w", encoding="utf-8") as f:
            json.dump({"other_key": "value"}, f)

        parent = get_parent_template(template_dir)
        self.assertIsNone(parent)

    def test_get_parent_template_invalid_json(self):
        """Test getting parent template when template.json has invalid JSON."""
        template_dir = self.create_template_dir("child")
        template_json = template_dir / "template.json"
        template_json.write_text("invalid json", encoding="utf-8")

        parent = get_parent_template(template_dir)
        self.assertIsNone(parent)

    def test_inheritance_chain_single_template(self):
        """Test inheritance chain for a template with no parent."""
        self.create_template_dir("standalone")

        chain = get_template_inheritance_chain(self._template_ref("standalone"))
        self.assertEqual([item.ref for item in chain], [self._canonical_ref("standalone")])

    def test_inheritance_chain_two_levels(self):
        """Test inheritance chain for parent -> child."""
        self.create_template_dir("parent")
        self.create_template_dir("child", "parent")

        chain = get_template_inheritance_chain(self._template_ref("child"))
        self.assertEqual(
            [item.ref for item in chain],
            [self._canonical_ref("parent"), self._canonical_ref("child")],
        )

    def test_inheritance_chain_two_levels_with_relative_extends(self):
        """Relative extends values should resolve within the same source repo."""
        self.create_template_dir("parent")
        child_dir = self.create_template_dir("child")
        with open(child_dir / "template.json", "w", encoding="utf-8") as f:
            json.dump({"extends": "parent"}, f)

        chain = get_template_inheritance_chain(self._template_ref("child"))
        self.assertEqual(
            [item.ref for item in chain],
            [self._canonical_ref("parent"), self._canonical_ref("child")],
        )

    def test_inheritance_chain_three_levels(self):
        """Test inheritance chain for grandparent -> parent -> child."""
        self.create_template_dir("grandparent")
        self.create_template_dir("parent", "grandparent")
        self.create_template_dir("child", "parent")

        chain = get_template_inheritance_chain(self._template_ref("child"))
        self.assertEqual(
            [item.ref for item in chain],
            [
                self._canonical_ref("grandparent"),
                self._canonical_ref("parent"),
                self._canonical_ref("child"),
            ],
        )

    def test_inheritance_chain_circular_dependency(self):
        """Test that circular dependencies are detected."""
        self.create_template_dir("template_a", "template_b")
        self.create_template_dir("template_b", "template_a")

        with self.assertRaises(ValueError) as cm:
            get_template_inheritance_chain(self._template_ref("template_a"))

        self.assertIn("Circular dependency", str(cm.exception))

    def test_inheritance_chain_missing_parent(self):
        """Test handling of missing parent template."""
        self.create_template_dir("child", "nonexistent_parent")

        with self.assertRaises(FileNotFoundError) as cm:
            get_template_inheritance_chain(self._template_ref("child"))

        self.assertIn("nonexistent_parent", str(cm.exception))

    def test_inheritance_chain_missing_child(self):
        """Test handling of missing child template."""
        with self.assertRaises(FileNotFoundError) as cm:
            get_template_inheritance_chain(self._template_ref("nonexistent_child"))

        self.assertIn("nonexistent_child", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
