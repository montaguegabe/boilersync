# BoilerSync CLI

`boilersync` scaffolds projects from templates and keeps templates alive as projects evolve.

## BoilerSync Ensemble

BoilerSync works as a small ensemble of tools:

- `boilersync-cli` for scaffold, pull, and push workflows.
- `boilersync-desktop` for repository diff review, staging, commit, and push workflows.
- Editor integration (Cursor/VS Code extension workflows) for fast editing and context switching.

For the full workflow guide, read [docs/ensemble.md](docs/ensemble.md).
For template metadata reference, read [docs/template-metadata.md](docs/template-metadata.md).

## Quick Start

```bash
# 1) Clone a template source into your local BoilerSync cache
boilersync templates init https://github.com/your-org/your-templates.git

# 2) Initialize a project from a source-qualified template ref
boilersync init your-org/your-templates#python/service-template

# 3) Pull template updates into the current project when needed
boilersync pull

# 4) Push committed project changes back into the template source
boilersync push
```

## Command Overview

- `boilersync init TEMPLATE_REF`: create a project from a template (empty target directory).
- `boilersync pull [TEMPLATE_REF]`: apply template updates to an existing project.
- `boilersync push`: review and copy committed project changes back to the template.
- `boilersync templates init`: clone a template source repository into the local cache.

Use command help for full flags:

```bash
boilersync --help
boilersync init --help
boilersync pull --help
boilersync push --help
boilersync templates --help
```

## Template References

Template commands accept:

- Legacy local template names (deprecated): `my-template`
- Source-qualified refs (recommended):
  - `org/repo#path/to/template`
  - `https://host/org/repo.git#path/to/template`
  - `git@host:org/repo.git#path/to/template`

Source-qualified refs clone (if missing) into:

```bash
${BOILERSYNC_TEMPLATE_DIR:-~/.boilersync/templates}/<org>/<repo>
```

## Project Tracking

After scaffold or pull, BoilerSync writes `.boilersync` metadata in the project root so future `pull`/`push` operations can resolve the original template source.

## Template Conventions

- Files ending in `.boilersync` are rendered and emitted without that extension.
- Files with `.starter` as the first extension are starter-only files.
- `template.json` supports inheritance (`extends`/`parent`), child templates, hooks, and optional GitHub repo creation.

## Documentation Policy

This repository intentionally avoids hand-written API reference docs. If API docs are ever included, they should be generated and linked from this README.
