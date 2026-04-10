# Template Metadata (`template.json`)

BoilerSync templates must include a `template.json` file at the template root.

## Overview

`template.json` currently supports:

- Inheritance: `extends` (or legacy `parent`)
- Input metadata: `variables`, `options`
- Runtime behavior: `children`, `hooks`, `github`, `skip_git`

Unknown keys are ignored by current CLI commands.

## Full Example

```json
{
  "extends": "acme/templates#python/base-service",
  "variables": {
    "company_name": {
      "label": "Company Name",
      "description": "Displayed in generated docs",
      "type": "string",
      "default": "Acme"
    },
    "region": ["us", "eu"],
    "service_tier": {
      "choices": ["free", "pro", "enterprise"],
      "required": true
    }
  },
  "options": {
    "with_ci": {
      "type": "boolean",
      "description": "Enable CI workflow",
      "default": true
    }
  },
  "children": [
    {
      "template": "acme/templates#python/worker",
      "path": "$${name_kebab}-worker",
      "condition": "with_workers",
      "variables": {
        "queue_name": "$${name_kebab}-jobs"
      },
      "name_snake": "$${name_snake}_worker",
      "name_pretty": "$${name_pretty} Worker"
    }
  ],
  "hooks": {
    "pre_init": [
      {
        "id": "deps",
        "run": "uv sync",
        "cwd": ".",
        "env": {
          "PROJECT_NAME": "$${name_kebab}"
        }
      }
    ],
    "post_init": [
      {
        "id": "format",
        "run": "uv run ruff format .",
        "allow_failure": true
      }
    ]
  },
  "github": {
    "create_repo": true,
    "private": true,
    "repo_name": "$${name_kebab}",
    "condition": "with_github"
  },
  "skip_git": false
}
```

## Key Details

### `extends` / `parent`

- Type: `string`
- Purpose: Inherit from a parent template.
- Notes: `parent` is supported for backward compatibility.

### `variables` and `options`

- Type: object keyed by field name.
- Purpose: Declare input metadata shown by `boilersync templates details`.
- Each value supports three forms:
  - Object form:
    - `label` or `prompt`: display label
    - `description`: help text
    - `type`: field type (`string`, `boolean`, etc.)
    - `required`: boolean
    - `default`: default value (implicitly makes `required: false` unless overridden)
    - `choices` (or `options` / `enum`): allowed values
  - Array form: shorthand for `choices`
  - Scalar form: shorthand for `default`

Notes:

- `variables` are also auto-discovered from `$${...}` usage in template files and `NAME_*` path placeholders.
- Built-in naming variables such as `name_snake` and `name_pretty` are exposed through the normal variable/input flow.

Relative `extends` values are resolved within the same template source repository. Example:

```json
{
  "extends": "pip-package"
}
```

When used from `acme/templates#cli`, this resolves to `acme/templates#pip-package`.

### `children`

- Type: list of objects.
- Purpose: Initialize child projects after parent project init.
- Child object keys:
  - `template` (required): child template ref
  - `path` (required): target child directory (supports interpolation)
  - `condition` (optional): boolean expression
  - `variables` (optional): object of variables passed to child init
  - `name_snake` (optional): child project name override
  - `name_pretty` (optional): child pretty name override

### `hooks`

- Type: object with optional `pre_init` and `post_init` lists.
- Purpose: Run shell commands before/after init.
- Hook step keys:
  - `id` (optional): identifier for logs
  - `run` (required): shell command
  - `condition` (optional): boolean expression
  - `cwd` (optional): run directory relative to target dir
  - `env` (optional): key/value env map (values support interpolation)
  - `allow_failure` (optional, default `false`)

### `github`

- Type: object.
- Purpose: Optional `gh`-based repository creation during init.
- Keys:
  - `create_repo` (boolean)
  - `condition` (optional)
  - `repo_name` (default: `$${name_kebab}`)
  - `private` (default: `true`)

### `skip_git`

- Type: boolean.
- Purpose: If `true` on any template in the inheritance chain, BoilerSync skips git initialization in generated projects.
