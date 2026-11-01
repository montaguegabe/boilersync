"""Install the BoilerSync agent skills.

``boilersync skills install`` copies the published skills with the skills.sh
installer (``npx skills add``). ``--dev`` instead runs the skills repo's own
``scripts/install-dev.sh``, which symlinks the checked-out skills into
``~/.agents/skills`` and ``~/.claude/skills`` so edits are live for Codex and
Claude Code. The symlink logic lives in that script, shared by every skills
repo made from the BoilerSync ``skills`` template; this command only finds the
repo and runs it.
"""

import logging
import subprocess
from pathlib import Path

import click

logger = logging.getLogger(__name__)

SKILLS_REPO = "montaguegabe/boilersync-skills"
SKILLS_REPO_DIRNAME = "boilersync-skills"
PUBLISHED_SKILLS = ("boilersync", "updating-high-prio-projects")
DEV_INSTALL_SCRIPT = Path("scripts") / "install-dev.sh"
SKILLS_AGENTS = ("codex", "claude-code")


def _is_skills_repo(path: Path) -> bool:
    return (path / "skills").is_dir()


def locate_skills_repo(source: Path | None, cwd: Path | None = None) -> Path:
    """Return the local skills repo checkout.

    An explicit ``source`` wins. Otherwise look for a ``boilersync-skills``
    directory next to the current directory or any of its parents (the
    workspace layout), then next to this CLI's own checkout.
    """
    if source is not None:
        source = source.expanduser().resolve()
        if not _is_skills_repo(source):
            raise click.UsageError(f"{source} has no skills/ directory.")
        return source

    start = (cwd or Path.cwd()).resolve()
    candidates = [
        ancestor / SKILLS_REPO_DIRNAME for ancestor in (start, *start.parents)
    ]
    candidates.append(Path(__file__).resolve().parents[3] / SKILLS_REPO_DIRNAME)
    for candidate in candidates:
        if _is_skills_repo(candidate):
            return candidate.resolve()

    raise click.UsageError(
        f"Could not find a {SKILLS_REPO_DIRNAME} checkout near {start}. "
        "Pass --source PATH."
    )


def dev_install_command(repo: Path, skills: tuple[str, ...]) -> list[str]:
    script = repo / DEV_INSTALL_SCRIPT
    if not script.is_file():
        raise click.UsageError(
            f"{script} is missing. Pull the latest skills template into {repo} "
            "(it ships scripts/install-dev.sh) and retry."
        )
    return [str(script), *skills]


def published_install_commands(skills: tuple[str, ...]) -> list[list[str]]:
    agent_flags = [flag for agent in SKILLS_AGENTS for flag in ("-a", agent)]
    return [
        [
            "npx",
            "-y",
            "skills",
            "add",
            SKILLS_REPO,
            "--skill",
            skill,
            "-g",
            *agent_flags,
            "-y",
        ]
        for skill in skills
    ]


def install_skills(dev: bool, source: Path | None, skills: tuple[str, ...]) -> None:
    if dev:
        repo = locate_skills_repo(source)
        command = dev_install_command(repo, skills)
        logger.info("Linking skills from %s", repo)
        subprocess.run(command, check=True)
        return

    for command in published_install_commands(skills or PUBLISHED_SKILLS):
        logger.info("Running %s", " ".join(command))
        subprocess.run(command, check=True)


@click.group(name="skills")
def skills_cmd():
    """Manage the BoilerSync agent skills."""


@skills_cmd.command(name="install")
@click.option(
    "--dev",
    is_flag=True,
    help="Symlink the local skills repo checkout instead of copying the published skills.",
)
@click.option(
    "--source",
    type=click.Path(path_type=Path),
    default=None,
    help="Skills repo checkout to link with --dev (default: the boilersync-skills repo next to the current workspace or this CLI).",
)
@click.option(
    "--skill",
    "skills",
    multiple=True,
    help="Skill name to install. Repeatable. Default: all skills.",
)
def skills_install_cmd(dev: bool, source: Path | None, skills: tuple[str, ...]):
    """Install the BoilerSync skills for Codex and Claude Code.

    Without --dev this runs `npx skills add` for the published skills. With
    --dev it runs the skills repo's scripts/install-dev.sh, which symlinks the
    checkout into ~/.agents/skills and ~/.claude/skills so edits are live.
    """
    install_skills(dev, source, skills)
