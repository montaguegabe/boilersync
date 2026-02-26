# boilersync

`boilersync` is a boilerplate CLI tool that can not only generate projects from boilerplate templates, but keep the boilerplate "alive" and updated as you continue to develop the derivative projects.

## Quick Start

```bash
# Initialize local templates directory by cloning your template repo
boilersync templates init https://github.com/your-org/your-templates.git

# Initialize a new project from a template
boilersync init my-template-name

# Show pusherences between your project and the original template
boilersync push
```

When you run the init command, you'll be prompted for project details:

```bash
$ boilersync init my-web-app

🚀 Initializing project from template 'my-web-app'
==================================================
Project name (snake_case) [my_awesome_project]: my_cool_app
Pretty name for display [My Cool App]: My Cool Application
==================================================
```

## Template System

### Project Name Variables

When initializing a project, `boilersync` prompts you for a snake_case project name and a pretty display name, then generates variables in pusherent naming conventions:

**For file/folder names (uppercase, no special symbols):**

- `NAME_SNAKE`: `my_awesome_project`
- `NAME_PASCAL`: `MyAwesomeProject`
- `NAME_KEBAB`: `my-awesome-project`
- `NAME_CAMEL`: `myAwesomeProject`
- `NAME_PRETTY`: `My Awesome Project`

**For file contents (lowercase, used with Jinja2 delimiters):**

- `name_snake`: `my_awesome_project`
- `name_pascal`: `MyAwesomeProject`
- `name_kebab`: `my-awesome-project`
- `name_camel`: `myAwesomeProject`
- `name_pretty`: `My Awesome Project`

### File and Folder Name Interpolation

Use the naming variables directly in file and folder names:

```
src/NAME_SNAKE_service.py → src/my_awesome_project_service.py
docs/NAME_KEBAB-guide.md → docs/my-awesome-project-guide.md
NAME_PASCAL/ → MyAwesomeProject/
```

### Template Content Processing

Template files use custom Jinja2 delimiters to avoid conflicts:

- **Variables**: `$${variable_name}`
- **Blocks**: `$${% if condition %}...$${% endif %}`
- **Comments**: `$${# This is a comment #}`

Example template file:

```python
class $${name_pascal}Service:
    def __init__(self):
        self.name = "$${name_snake}"
        self.kebab_name = "$${name_kebab}"

$${# This comment will be removed #}
$${% if include_logging %}
import logging
$${% endif %}
```

### Interactive Variable Collection

When initializing a template, `boilersync` automatically scans template files (`.boilersync` files) for variables used in Jinja2 syntax. If it finds variables that aren't predefined (like the project name variables), it will prompt you to provide values:

```bash
$ boilersync init my-web-app

🔧 Additional variables needed for this template:
==================================================
Enter value for 'author_email' (email address): user@example.com
Enter value 'author_name' (name): John Doe
Enter value for 'api_version' (version number): v1.0
Enter value for 'database_url' (URL): postgresql://localhost:5432/mydb
==================================================
✅ All variables collected!
```

The system provides helpful prompts based on variable name patterns:

- Variables ending in `_email` → prompts for "email address"
- Variables ending in `_name` → prompts for "name"
- Variables ending in `_url` → prompts for "URL"
- Variables ending in `_version` → prompts for "version number"
- Variables ending in `_description` → prompts for "description"

Once collected, these values are remembered and reused if the same variable appears in multiple files.

## Project Tracking

After initialization, `boilersync` creates a `.boilersync` file in your project root to track the template and project information:

```json
{
  "template": "web-app",
  "name_snake": "my_awesome_project",
  "name_pretty": "My Awesome Project"
}
```

This file uses the same variable names that templates reference, making it easy to understand and potentially use in other tools.

## Push Command

The `push` command helps you see how your project has diverged from its original template. This is useful for:

- Understanding what changes you've made
- Deciding what to sync when templates are updated
- Reviewing project evolution

### How It Works

1. **Finds your project root**: Locates the nearest `.boilersync` file (created during `init`)
2. **Reads project info**: Gets the original template name and project names from `.boilersync`
3. **Creates fresh template**: Initializes the template in a temporary directory using saved names
4. **Sets up git**: Creates a git repo and commits the fresh template
5. **Overlays your changes**: Copies your current project files over the fresh template
6. **Opens push viewer**: Launches GitHub Desktop to show the pusherences

### Usage

```bash
$ cd my-project
$ boilersync push

🔍 Creating push for template 'web-app'...
📦 Initializing fresh template in temporary directory...
🚀 Initializing project from template 'web-app'
📝 Using saved project name: my_project
📝 Using saved pretty name: My Project
🔧 Setting up git repository...
📋 Copying current project files...
🚀 Opening in GitHub Desktop...
📂 Temporary directory created and opened in GitHub Desktop.
⏳ Press Enter when you're done reviewing the push...
```

The push will show:

- **Green (additions)**: Your custom changes and new files
- **Red (deletions)**: Template parts you've removed or modified
- **Modified files**: Side-by-side comparison of your changes vs template

### Special File Extensions

#### `.boilersync` Extension

Files ending with `.boilersync` are processed as templates and have the extension removed:

- `package.json.boilersync` → `package.json` (processed)
- `README.md.boilersync` → `README.md` (processed)
- `config.yaml` → `config.yaml` (copied as-is)

#### `.starter` Extension

Files with `.starter` as the first extension are "starter files" - they're used only during initialization and won't be synced in future updates:

- `example.starter.py` → `example.py` (init only, no future sync)
- `sample.starter.config.json` → `sample.config.json` (init only)
- `tutorial.starter.md.boilersync` → `tutorial.md` (processed + init only)

### Template Directory Structure

By default, templates are loaded from:

```bash
~/.boilersync/templates
```

You can override this with:

```bash
BOILERSYNC_TEMPLATE_DIR=/custom/templates/path
```

Initialize the templates directory with:

```bash
boilersync templates init [REPO_URL]
```

If `REPO_URL` is omitted, Boilersync prompts for it and then runs a `git clone`
into the configured templates directory.

```
boilerplate/
├── my-template/
│   ├── src/
│   │   ├── NAME_SNAKE_service.py.boilersync
│   │   └── utils.py
│   ├── docs/
│   │   ├── README.md.boilersync
│   │   └── getting-started.starter.md.boilersync
│   └── package.json.boilersync
```

After `boilersync init my-template` in directory `MyAwesomeProject`:

```
MyAwesomeProject/
├── src/
│   ├── my_awesome_project_service.py
│   └── utils.py
├── docs/
│   ├── README.md
│   └── getting-started.md
└── package.json
```

### Workspace Composition and Hooks

`template.json` can define children and hooks so one template can scaffold a full workspace.

Example:

```json
{
  "extends": "base-template",
  "skip_git": true,
  "children": [
    {
      "template": "pip-package",
      "path": "$${name_kebab}-api",
      "variables": {
        "package_description": "Generated for $${name_pretty}"
      }
    },
    {
      "template": "react-app",
      "path": "$${name_kebab}-react",
      "condition": "with_frontend"
    }
  ],
  "hooks": {
    "post_init": [
      {
        "id": "sync-workspace",
        "run": "multi sync"
      }
    ]
  },
  "github": {
    "create_repo": true,
    "repo_name": "$${name_kebab}",
    "private": true,
    "condition": "with_github"
  }
}
```

Notes:

- `children` are initialized after the parent template and registered in the parent `.boilersync`.
- `condition` supports simple truthy/equality checks against runtime variables/options.
- `hooks.pre_init` and `hooks.post_init` run shell commands in the target directory.
- `github.create_repo` uses `gh` to create the repository if it does not already exist.
