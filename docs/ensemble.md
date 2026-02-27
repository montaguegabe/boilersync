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

# Push committed project changes back to template source
boilersync push
```

### Template Reference Formats

- `org/repo#subdir`
- `https://host/org/repo.git#subdir`
- `git@host:org/repo.git#subdir`
- Legacy local name (deprecated)

Template cache root defaults to:

```bash
~/.boilersync/templates
```

Override with:

```bash
BOILERSYNC_TEMPLATE_DIR=/custom/path
```

BoilerSync writes `.boilersync` metadata in project roots after scaffold/pull so future sync operations can resolve template provenance.

## CLI Commands

### `boilersync init TEMPLATE_REF`

Use for first-time project generation.

- Requires an empty target directory
- Supports `--name`, `--pretty-name`, `--var KEY=VALUE`, and `--no-input`
- Resolves source-qualified refs and legacy cache refs
- Can run configured hooks and initialize child templates

### `boilersync pull [TEMPLATE_REF]`

Use to apply template changes into an existing project.

- Uses nearest `.boilersync` when `TEMPLATE_REF` is omitted
- Supports inheritance from `template.json` (`extends`/`parent`)
- Excludes `.starter` files by default (`--include-starter` to include)
- Pulls child projects by default (`--no-children` to skip)

### `boilersync push`

Use to promote committed project improvements back to template source.

- Creates a comparison workspace for template/project diff review
- Copies only committed changes back into the template source
- Supports `--add-files` for explicit additional file inclusion

### `boilersync templates init`

Use to initialize the local template source cache.

- Clones template source repositories into the BoilerSync template root
- Supports org/repo shorthand and full git URLs
- Supports non-interactive mode (`--no-input`)

## Desktop App Workflow (`boilersync-desktop`)

Use Desktop as the git-focused review and commit surface around CLI actions.

1. Run `boilersync pull` or `boilersync push` from project roots.
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
