from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from boilersync.cli import main
from boilersync.commands.skills import (
    PUBLISHED_SKILLS,
    SKILLS_REPO,
    SKILLS_REPO_DIRNAME,
    dev_install_command,
    locate_skills_repo,
    published_install_commands,
)


def _make_skills_repo(root: Path, *, with_script: bool = True) -> Path:
    repo = root / SKILLS_REPO_DIRNAME
    (repo / "skills" / "boilersync").mkdir(parents=True)
    (repo / "skills" / "boilersync" / "SKILL.md").write_text(
        "---\nname: boilersync\n---\n"
    )
    if with_script:
        script = repo / "scripts" / "install-dev.sh"
        script.parent.mkdir()
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$REPO_MARKER"\n')
        script.chmod(0o755)
    return repo


def test_locate_uses_explicit_source(tmp_path: Path):
    repo = _make_skills_repo(tmp_path)
    assert locate_skills_repo(repo) == repo.resolve()


def test_locate_rejects_source_without_skills_dir(tmp_path: Path):
    with pytest.raises(click.UsageError):
        locate_skills_repo(tmp_path)


def test_locate_walks_up_from_cwd_to_workspace_sibling(tmp_path: Path):
    repo = _make_skills_repo(tmp_path)
    nested = tmp_path / "boilersync-cli" / "boilersync" / "commands"
    nested.mkdir(parents=True)
    assert locate_skills_repo(None, cwd=nested) == repo.resolve()


def test_locate_fails_clearly_when_nothing_found(tmp_path: Path):
    with pytest.raises(click.UsageError, match="--source"):
        # Point the package-relative fallback at an empty directory too.
        with patch(
            "boilersync.commands.skills.__file__",
            str(tmp_path / "a" / "b" / "c" / "d" / "skills.py"),
        ):
            locate_skills_repo(None, cwd=tmp_path / "elsewhere")


def test_dev_install_command_requires_script(tmp_path: Path):
    repo = _make_skills_repo(tmp_path, with_script=False)
    with pytest.raises(click.UsageError, match="install-dev.sh"):
        dev_install_command(repo, ())


def test_dev_install_command_passes_skill_names(tmp_path: Path):
    repo = _make_skills_repo(tmp_path)
    command = dev_install_command(repo, ("boilersync",))
    assert command == [str(repo / "scripts" / "install-dev.sh"), "boilersync"]


def test_published_install_commands_target_both_agents():
    commands = published_install_commands(PUBLISHED_SKILLS)
    assert len(commands) == len(PUBLISHED_SKILLS)
    for command, skill in zip(commands, PUBLISHED_SKILLS, strict=False):
        assert command[:5] == ["npx", "-y", "skills", "add", SKILLS_REPO]
        assert command[command.index("--skill") + 1] == skill
        assert "-g" in command
        assert command.count("-a") == 2


def test_cli_dev_install_runs_repo_script(tmp_path: Path, monkeypatch):
    repo = _make_skills_repo(tmp_path)
    marker = tmp_path / "marker"
    monkeypatch.setenv("REPO_MARKER", str(marker))

    result = CliRunner().invoke(
        main,
        ["skills", "install", "--dev", "--source", str(repo), "--skill", "boilersync"],
    )

    assert result.exit_code == 0, result.output
    assert marker.read_text().strip() == "boilersync"


def test_cli_published_install_invokes_npx_per_skill():
    with patch("boilersync.commands.skills.subprocess.run") as run:
        result = CliRunner().invoke(main, ["skills", "install"])

    assert result.exit_code == 0, result.output
    assert run.call_count == len(PUBLISHED_SKILLS)
    for call in run.call_args_list:
        assert call.args[0][:4] == ["npx", "-y", "skills", "add"]
        assert call.kwargs == {"check": True}
