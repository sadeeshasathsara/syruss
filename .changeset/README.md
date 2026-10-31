# Syruss Changesets

This folder holds pending changelog fragments.

## How to Add a Changeset

When you create a Pull Request that changes code in `server/`, `agentic_framework/`, `www/`, `site/`, or `admin/`, you must provide a changeset.

### Option 1: AI Agent & Single-Command (Recommended)
```bash
syruss changeset write --bump "www=minor" --bump "server=patch" -m "Add entity badge in SchemaEditor" -m "Fix destination adapter"
```
Or let it auto-detect modified services:
```bash
syruss changeset write -m "Summary of changes"
```

### Option 2: Interactive CLI
```bash
syruss changeset add
```
Follow the interactive prompts to pick affected services, bump levels (`patch`, `minor`, `major`), and enter your description.

Commit the generated file (e.g. `.changeset/bold-fox-123.md`) along with your pull request.

When your PR is merged, the release pipeline automatically:
1. Reads these files
2. Updates `CHANGELOG.md`
3. Tags the release
4. Deletes the processed changeset files
