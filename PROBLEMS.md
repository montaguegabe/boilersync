# Problems: `boilersync push` and Template Inheritance

## 1. Push always targets the leaf template

`push` resolves the template reference from `.boilersync` and writes all changes back to the leaf template only. It has no concept of the inheritance chain. If a file originated from a parent template (e.g., `pip-package`), changes to it are incorrectly written into the child template (e.g., `cli`) instead.

`pull` has a `get_template_inheritance_chain()` function that walks the full parent→child chain. `push` has no equivalent.

## 2. `.boilersync` metadata does not store the inheritance chain

The `.boilersync` file only stores the leaf template reference. There is no record of parent templates. Even if `push` wanted to route changes to the correct level in the chain, it has no data to work from.

## 3. No file origin tracking

`pull` layers files from parent → child in sequence, with child files overwriting parent files. No record is kept of which template each output file came from. `push` therefore cannot determine whether a changed file belongs to a parent or child template.

## 4. Block overrides are not round-trip safe

Child templates use block syntax (`$${% block %}`, `$${ super() }`) to extend parent content. At pull time this is rendered into a flat output file. `push` reverse-interpolates variable values but does not reconstruct block syntax. If a user edits content that came from a `$${ super() }` expansion and pushes, the block structure in the child template is silently replaced with a flat file, breaking future inheritance.

## 5. Diff workspace only includes the leaf template

When `push` creates the temporary diff workspace, it copies only the leaf template files. Files that exist in a parent template but are not overridden in the child are absent from the diff baseline, causing misleading diffs and incorrect change detection for inherited files.
