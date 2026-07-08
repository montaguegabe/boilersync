# BoilerSync Ensemble Guide

BoilerSync treats boilerplate as a living system: scaffold from templates, evolve projects, then promote proven improvements back into templates.

This guide covers only:

- Getting started
- CLI commands
- Desktop app workflow (`boilersync-desktop`)
- Extension workflow (Cursor/VS Code)

If auto-generated API docs are added in the future, link them from `README.md`; this guide does not include a hand-written API reference.

For template `template.json` metadata fields and examples, see [template-metadata.md](template-metadata.md).

## Getting Started

### Prerequisites

- `git` installed
- `boilersync` available on PATH
- Optional but recommended: GitHub Desktop for the `push` review flow
- Optional for template-configured repo creation: GitHub CLI (`gh`) authenticated

### First Run

```bash
# Clone template source repo into local cache
boilersync templates init https://github.com/your-org/your-templates.git

# Initialize a project from a source-qualified template
boilersync init your-org/your-templates#python/service-template

# Pull template updates later
boilersync pull

# Check local divergence from the current upstream template
boilersync diff

# Create a reviewable proposed pull in /tmp
boilersync pull-proposal create

# Push committed project changes back to template source
boilersync push
```

### Template Reference Formats

- `org/repo#subdir`
- `https://github.com/org/repo#subdir`
- `https://github.com/org/repo.git#subdir`

Template cache root defaults to:

```bash
~/.boilersync/templates
```

Override with:

```bash
BOILERSYNC_TEMPLATE_DIR=/custom/path
```

BoilerSync writes `.boilersync` metadata in project roots after scaffold/pull so future sync operations can resolve template provenance.
For the exact schema, see [project-metadata.md](project-metadata.md).

## CLI Commands

### `boilersync init TEMPLATE_REF`

Use for first-time project generation.

- Requires an empty target directory
- Supports `--var KEY=VALUE` for template inputs, including `name_snake` / `name_pretty`, and non-interactive mode (`--non-interactive`, alias `--no-input`)
- Uses `template.json` `defaults` before prompting, so well-defaulted templates can be bootstrapped with one command after creating and entering the target directory
- Infers `name_snake` from the target directory and strips a trailing `-workspace` / `_workspace` suffix
- Attempts to infer `github_user` from `gh api user --jq .login` when a template references `github_user`
- Resolves source-qualified refs
- Can run configured hooks and initialize child templates

### `boilersync pull [TEMPLATE_REF]`

Use to apply template changes directly into an existing project.

- Uses nearest `.boilersync` when `TEMPLATE_REF` is omitted
- Supports inheritance from `template.json` (`extends`/`parent`)
- Excludes `.starter` files by default (`--include-starter` to include)
- Pulls child projects by default (`--no-children` to skip)
- For customized downstream projects, prefer `boilersync pull-proposal` so AI/manual review can apply only the useful files or hunks.

### `boilersync diff`

Use to inspect how the current project has diverged from its upstream template without opening an interactive review flow.

- Renders the upstream template with the project's saved `.boilersync` variables before comparing
- Shows diffstat output by default
- Supports `--name-status`, `--patch`, and `--json`
- Excludes files derived from `.starter` templates by default (`--include-starter` to include)
- Excludes paths registered in `.boilersync` `children` from the parent diff
- Supports `--children` for direct child project diffs and `--recursive` for descendants
- Does not auto-detect framework-specific children; unregistered extra folders remain parent-project drift
- Writes output to a file with `--output PATH`

### `boilersync pull-proposal`

Use when template updates need AI or manual review before touching the real project.

- `boilersync pull-proposal create` copies the current project to a temp git repo, commits that state, runs `boilersync pull` in the copy, and leaves the proposed update as a git diff.
- Add `--json` for agent-readable output including `proposal_dir` and changed files.
- Inspect with `git -C PROPOSAL_DIR diff`.
- Apply a whole file from the proposal with `boilersync pull-proposal apply-file PROPOSAL_DIR PATH`.
- Apply selected hunks by saving a patch from the proposal diff and running `boilersync pull-proposal apply-patch PATCH`.

### `boilersync push`

Use to promote committed project improvements back to template source.

- Creates an interactive comparison workspace for template/project diff review
- Copies only committed changes back into the template source
- Supports `--add-files` for explicit additional file inclusion

### `boilersync templates init`

Use to initialize the local template source cache.

- Clones template source repositories into the BoilerSync template root
- Supports org/repo shorthand and GitHub HTTPS URLs
- Supports non-interactive mode (`--no-input`)

## Desktop App Workflow (`boilersync-desktop`)

Use Desktop as the git-focused review and commit surface around CLI actions.

1. Run `boilersync pull`, `boilersync pull-proposal`, `boilersync diff`, or `boilersync push` from project roots.
2. Open `boilersync-desktop` to inspect diffs across tracked repositories.
3. Stage exact files/lines, commit, and push.
4. Return to CLI for the next template lifecycle action.

This pattern keeps template evolution auditable and reduces accidental broad commits.

## Extension Workflow (Cursor/VS Code)

Use extension workflows for editing speed; keep CLI metadata and behavior canonical.

1. Author and refactor template/project code in Cursor or VS Code.
2. Trigger CLI lifecycle operations (`init`, `pull`, `push`) from terminal/tasks.
3. Optionally hand off to Desktop via deep link: `boilersync://open?path=/absolute/path`.
4. Treat `.boilersync` plus CLI command outputs as the source of truth.
