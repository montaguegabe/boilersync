from pathlib import Path

import click

from boilersync.project_metadata import PullCheckStatus, get_pull_check_status


def check_pull(project_dir: Path | None = None) -> PullCheckStatus:
    return get_pull_check_status(project_dir)


@click.command(name="check-pull")
def check_pull_cmd() -> None:
    """Check whether the current project is due for a boilersync pull."""
    status = check_pull()

    click.echo(f"Template: {status.template_ref}")
    click.echo(
        "Recorded template commit: "
        + (status.recorded_template_commit or "missing")
    )
    click.echo(f"Current template commit: {status.current_template_commit}")

    if status.due_for_pull:
        click.echo("Due for pull: yes")
        raise click.exceptions.Exit(1)

    click.echo("Due for pull: no")
