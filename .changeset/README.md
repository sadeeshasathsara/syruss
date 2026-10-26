# Syruss Changesets

This folder holds pending changelog fragments.

## How to add a changeset
When you create a Pull Request that changes code in `server/`, `agentic_framework/`, `www/`, `site/`, or `admin/`, run:

```bash
python scripts/changeset.py add
```

Follow the prompts to select which services changed, whether it's a `patch`, `minor`, or `major` bump, and enter a plain-English summary.

Commit the generated file (e.g. `.changeset/bold-fox-123.md`) along with your pull request.

When your PR is merged, the release pipeline automatically:
1. Reads these files
2. Updates `CHANGELOG.md`
3. Tags the release
4. Deletes the processed changeset files
