## Development workflow

`boilersync` uses **trunk-based development**. `main` is the only long-lived
branch — commit and push directly to `main`. There is no `staging` branch and no
PR-based integration branch; do not create one. Pushing to `main` triggers the
auto-tag workflow to cut a release, so keep `main` releasable.
