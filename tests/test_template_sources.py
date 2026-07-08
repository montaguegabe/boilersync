import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from boilersync.template_sources import (
    SOURCE_RESOLUTION,
    parse_repo_locator,
    resolve_source_from_boilersync,
    resolve_template_source,
)


class TestTemplateSources(unittest.TestCase):
    def test_parse_repo_locator_org_repo(self):
        org, repo, clone_url = parse_repo_locator("acme/template-kit")
        self.assertEqual(org, "acme")
        self.assertEqual(repo, "template-kit")
        self.assertEqual(clone_url, "https://github.com/acme/template-kit.git")

    def test_parse_repo_locator_https_canonicalizes_to_git_url(self):
        org, repo, clone_url = parse_repo_locator("https://github.com/acme/template-kit")
        self.assertEqual(org, "acme")
        self.assertEqual(repo, "template-kit")
        self.assertEqual(clone_url, "https://github.com/acme/template-kit.git")

    def test_parse_repo_locator_uses_known_repo_renames(self):
        org, repo, clone_url = parse_repo_locator(
            "openbase-community/openbase-boilerplate"
        )
        self.assertEqual(org, "openbase-community")
        self.assertEqual(repo, "templates")
        self.assertEqual(
            clone_url,
            "https://github.com/openbase-community/templates.git",
        )

    def test_parse_repo_locator_rejects_non_github_host(self):
        with self.assertRaises(ValueError):
            parse_repo_locator("https://gitlab.com/acme/template-kit.git")

    def test_parse_repo_locator_rejects_ssh(self):
        with self.assertRaises(ValueError):
            parse_repo_locator("git@github.com:acme/template-kit.git")

    def test_resolve_template_source_rejects_ref_without_subdir(self):
        with self.assertRaises(ValueError):
            resolve_template_source("acme/template-kit", clone_missing_repo=False)

    def test_resolve_source_ref_from_existing_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_root_dir = Path(tmp)
            local_repo = template_root_dir / "acme" / "template-kit"
            (local_repo / ".git").mkdir(parents=True)
            (local_repo / "python/service-template").mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"BOILERSYNC_TEMPLATE_DIR": str(template_root_dir)},
                clear=True,
            ):
                source = resolve_template_source(
                    "acme/template-kit#python/service-template",
                    clone_missing_repo=False,
                )

        self.assertEqual(source.resolution, SOURCE_RESOLUTION)
        self.assertEqual(
            source.ref,
            "https://github.com/acme/template-kit.git#python/service-template",
        )
        self.assertEqual(source.org, "acme")
        self.assertEqual(source.repo, "template-kit")
        self.assertEqual(source.subdir, "python/service-template")

    def test_resolve_source_from_boilersync_uses_template_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_root_dir = Path(tmp)
            local_repo = template_root_dir / "acme" / "template-kit"
            (local_repo / ".git").mkdir(parents=True)
            (local_repo / "python/service-template").mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"BOILERSYNC_TEMPLATE_DIR": str(template_root_dir)},
                clear=True,
            ):
                source = resolve_source_from_boilersync(
                    template_ref="https://github.com/acme/template-kit#python/service-template",
                    clone_missing_repo=False,
                )

        self.assertEqual(
            source.ref,
            "https://github.com/acme/template-kit.git#python/service-template",
        )
        self.assertEqual(source.template_dir, local_repo / "python/service-template")

    def test_resolve_source_from_boilersync_accepts_legacy_short_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_root_dir = Path(tmp)
            local_repo = template_root_dir / "openbase-community" / "templates"
            (local_repo / ".git").mkdir(parents=True)
            (local_repo / "react-app").mkdir(parents=True)

            with patch.dict(
                os.environ,
                {"BOILERSYNC_TEMPLATE_DIR": str(template_root_dir)},
                clear=True,
            ):
                source = resolve_source_from_boilersync(
                    template_ref="react-app",
                    clone_missing_repo=False,
                )

        self.assertEqual(
            source.ref,
            "https://github.com/openbase-community/templates.git#react-app",
        )
        self.assertEqual(source.template_dir, local_repo / "react-app")

    def test_resolve_source_from_boilersync_requires_template_field(self):
        with self.assertRaises(ValueError):
            resolve_source_from_boilersync(template_ref=None, clone_missing_repo=False)


if __name__ == "__main__":
    unittest.main()
