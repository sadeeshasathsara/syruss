#!/usr/bin/env python3
"""
Syruss Monorepo Changeset Tool

A unified, zero-dependency CLI for managing changelog fragments, version calculation,
and CHANGELOG.md generation across all services (Python, Next.js, and Infrastructure).

Commands:
  python scripts/changeset.py add       Create a new changelog fragment for your changes
  python scripts/changeset.py check     Verify that PR code changes include a valid changeset
  python scripts/changeset.py status    Display all pending changesets and projected versions
  python scripts/changeset.py apply     Apply changesets, bump versions, and update CHANGELOG.md
"""
import argparse
import datetime
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGESET_DIR = REPO_ROOT / ".changeset"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

SERVICES = {
    "server": "server",
    "agent": "agentic_framework",
    "www": "www",
    "site": "site",
    "admin": "admin",
}

BUMP_PRIORITY = {"patch": 1, "minor": 2, "major": 3}
ADJECTIVES = ["bold", "bright", "calm", "clever", "cool", "eager", "fresh", "gentle", "grand", "keen", "swift", "wise"]
ANIMALS = ["badger", "falcon", "fox", "hawk", "lion", "otter", "owl", "panther", "seal", "tiger", "wolf"]


def random_slug() -> str:
    return f"{random.choice(ADJECTIVES)}-{random.choice(ANIMALS)}-{random.randint(100, 999)}"


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Parses YAML-like frontmatter without external dependencies."""
    pattern = r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        raise ValueError("Invalid changeset format. Expected '---\\nservice: type\\n---\\n\\nMessage'")
    
    fm_raw = match.group(1).strip()
    body = match.group(2).strip()
    
    bumps: Dict[str, str] = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid frontmatter line: '{line}'. Expected 'service: type'")
        svc, btype = line.split(":", 1)
        svc = svc.strip().strip("'\"")
        btype = btype.strip().strip("'\"").lower()
        if svc not in SERVICES:
            raise ValueError(f"Unknown service '{svc}'. Valid services: {', '.join(SERVICES.keys())}")
        if btype not in BUMP_PRIORITY:
            raise ValueError(f"Invalid bump type '{btype}' for service '{svc}'. Must be 'patch', 'minor', or 'major'")
        bumps[svc] = btype
    
    if not bumps:
        raise ValueError("Changeset must declare at least one service bump in frontmatter.")
    if not body:
        raise ValueError("Changeset description body cannot be empty.")
        
    return bumps, body


def get_git_diff_files(base_ref: Optional[str] = None, head_ref: Optional[str] = None) -> List[str]:
    """Returns files changed between two refs or in the working directory."""
    try:
        if base_ref and head_ref:
            cmd = ["git", "diff", "--name-only", base_ref, head_ref]
        elif base_ref:
            cmd = ["git", "diff", "--name-only", f"{base_ref}...HEAD"]
        else:
            cmd = ["git", "diff", "--name-only", "HEAD"]
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception:
        return []


def read_pending_changesets() -> List[Tuple[Path, Dict[str, str], str]]:
    """Returns list of (filepath, bumps_dict, description_body)."""
    if not CHANGESET_DIR.exists():
        return []
    items = []
    for p in sorted(CHANGESET_DIR.glob("*.md")):
        if p.name.startswith(".") or p.name.lower() == "readme.md":
            continue
        try:
            bumps, body = parse_frontmatter(p.read_text(encoding="utf-8"))
            items.append((p, bumps, body))
        except Exception as exc:
            print(f"⚠️  Warning: skipping malformed changeset '{p.name}': {exc}", file=sys.stderr)
    return items


def cmd_add(args: argparse.Namespace) -> int:
    """Interactively or via flags creates a new changeset fragment."""
    CHANGESET_DIR.mkdir(parents=True, exist_ok=True)
    
    bumps: Dict[str, str] = {}
    message: str = ""

    # Non-interactive CLI mode
    if args.service and args.type:
        for s in args.service:
            if s not in SERVICES:
                print(f"❌ Error: Unknown service '{s}'. Available: {', '.join(SERVICES.keys())}")
                return 1
            bumps[s] = args.type.lower()
        message = args.message or ""
    else:
        # Interactive Mode
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Syruss Changeset Creator")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        
        # Check git status for changed paths to offer smart defaults
        changed_files = get_git_diff_files("origin/dev") or get_git_diff_files("dev") or get_git_diff_files()
        detected_svcs = set()
        for f in changed_files:
            for s, folder in SERVICES.items():
                if f.startswith(f"{folder}/"):
                    detected_svcs.add(s)

        if detected_svcs:
            print(f"🔍 Detected changes in services: {', '.join(sorted(detected_svcs))}\n")

        print("Select which services are affected by your changes:")
        svc_keys = list(SERVICES.keys())
        for idx, svc in enumerate(svc_keys, 1):
            hint = " (detected changes)" if svc in detected_svcs else ""
            print(f"  [{idx}] {svc:<8} ({SERVICES[svc]}){hint}")
        
        print("\nEnter service numbers separated by commas (e.g. 1, 3):")
        chosen_input = input("> ").strip()
        if not chosen_input:
            print("❌ Error: No services selected.")
            return 1
            
        chosen_svcs = []
        for part in chosen_input.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(svc_keys):
                chosen_svcs.append(svc_keys[int(part) - 1])
            elif part in svc_keys:
                chosen_svcs.append(part)

        if not chosen_svcs:
            print("❌ Error: No valid services selected.")
            return 1

        print("\nSelect version bump type for each service:")
        print("  1) patch  - backwards-compatible bug fixes")
        print("  2) minor  - backwards-compatible new features")
        print("  3) major  - breaking changes\n")

        for svc in chosen_svcs:
            print(f"Bump level for '{svc}' [1=patch, 2=minor, 3=major] (default: 1):")
            b_choice = input(f"[{svc}] > ").strip() or "1"
            level_map = {"1": "patch", "2": "minor", "3": "major", "patch": "patch", "minor": "minor", "major": "major"}
            bumps[svc] = level_map.get(b_choice.lower(), "patch")

        print("\nDescribe what changed in this PR (plain English summary):")
        message = input("> ").strip()
        while not message:
            print("Description cannot be empty. Please enter summary:")
            message = input("> ").strip()

    # Generate filename
    slug = args.name or random_slug()
    target_file = CHANGESET_DIR / f"{slug}.md"

    # Write changeset content
    fm_lines = [f"{s}: {bumps[s]}" for s in sorted(bumps.keys())]
    content = f"---\n" + "\n".join(fm_lines) + f"\n---\n\n{message}\n"
    target_file.write_text(content, encoding="utf-8")

    print(f"\n✔ Created changeset file: .changeset/{target_file.name}")
    print("──────────────────────────────────────────────────────────")
    print(content.strip())
    print("──────────────────────────────────────────────────────────")
    print("👉 Remember to commit this file to your branch before opening/merging your PR!\n")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Used in PR CI workflow to enforce that code changes include a valid changeset."""
    base_ref = args.base or os.environ.get("GITHUB_BASE_REF", "origin/dev")
    head_ref = args.head or os.environ.get("GITHUB_HEAD_REF")
    
    # Check what files changed
    changed_files = get_git_diff_files(base_ref, head_ref)
    
    # Check if any service code was touched
    touched_services = set()
    for f in changed_files:
        for s, folder in SERVICES.items():
            if f.startswith(f"{folder}/"):
                touched_services.add(s)

    if not touched_services:
        print("✔ No service code changed (docs or infrastructure only). Changeset not required.")
        return 0

    # Read pending changesets
    changesets = read_pending_changesets()
    if not changesets:
        print("\n❌ PR VALIDATION FAILED: No changeset fragment found in .changeset/")
        print("───────────────────────────────────────────────────────────────────")
        print(f"This PR modifies code in services: {', '.join(sorted(touched_services))}")
        print("To fix this, please run:")
        print("    python scripts/changeset.py add")
        print("and commit the generated .changeset/<name>.md file.")
        print("───────────────────────────────────────────────────────────────────\n")
        return 1

    # Verify that touched services are covered by at least one changeset
    covered_services = set()
    print("\n🔍 Validating pending changesets:")
    for path, bumps, body in changesets:
        print(f"  • {path.name}: {bumps} - {body[:60]}...")
        covered_services.update(bumps.keys())

    missing = touched_services - covered_services
    if missing:
        print(f"\n⚠️  Warning: Changes were detected in [{', '.join(sorted(missing))}] but no changeset declared them.")
        print("Please ensure your changeset frontmatter includes all modified services.\n")
        return 1

    print("\n✔ All modified services are covered by valid changesets. Checks passed!\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Shows pending changesets and projected version bumps."""
    changesets = read_pending_changesets()
    if not changesets:
        print("No pending changesets found in .changeset/.")
        return 0

    highest_bumps: Dict[str, str] = {}
    for _, bumps, _ in changesets:
        for svc, btype in bumps.items():
            cur = highest_bumps.get(svc)
            if not cur or BUMP_PRIORITY[btype] > BUMP_PRIORITY[cur]:
                highest_bumps[svc] = btype

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Pending Changesets ({len(changesets)} file(s))")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for path, bumps, body in changesets:
        print(f"\n📄 {path.name}")
        for s, t in bumps.items():
            print(f"   • {s}: {t}")
        print(f"   Message: {body}")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Projected Next Bumps on Release:")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for s in sorted(highest_bumps.keys()):
        print(f"  • {s:<8} → {highest_bumps[s].upper()} bump")
    print()
    return 0


def get_latest_base_version(service: str) -> Tuple[int, int, int]:
    """Finds highest base SemVer (major, minor, patch) for a service across all tags."""
    try:
        res = subprocess.run(["git", "tag", "-l", f"{service}-v*"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        tags = [t.strip() for t in res.stdout.splitlines() if t.strip()]
        parsed = []
        for t in tags:
            m = re.match(rf"^{service}-v(\d+)\.(\d+)\.(\d+)", t)
            if m:
                parsed.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        return max(parsed) if parsed else (0, 1, 0)
    except Exception:
        return (0, 1, 0)


def compute_next_version(service: str, bump_type: str, branch: str) -> str:
    """Computes next version and increments prerelease numbers if on dev or test."""
    major, minor, patch = get_latest_base_version(service)
    if bump_type == "major":
        major += 1; minor = 0; patch = 0
    elif bump_type == "minor":
        minor += 1; patch = 0
    elif bump_type == "patch":
        patch += 1

    base = f"{major}.{minor}.{patch}"
    if branch == "main":
        return base

    suffix = "rc" if branch == "test" else "dev"
    # Find existing prerelease tags for this exact base
    try:
        pattern = f"{service}-v{base}-{suffix}.*"
        res = subprocess.run(["git", "tag", "-l", pattern], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        increments = []
        for t in res.stdout.splitlines():
            m = re.search(rf"-{suffix}\.(\d+)$", t.strip())
            if m:
                increments.append(int(m.group(1)))
        next_inc = max(increments) + 1 if increments else 1
    except Exception:
        next_inc = 1

    return f"{base}-{suffix}.{next_inc}"


def cmd_apply(args: argparse.Namespace) -> int:
    """Applies pending changesets, updates CHANGELOG.md, and outputs new versions for CI."""
    changesets = read_pending_changesets()
    if not changesets:
        print("No changesets to apply.")
        return 0

    branch = args.branch or "dev"
    highest_bumps: Dict[str, str] = {}
    entries_by_service: Dict[str, List[str]] = {s: [] for s in SERVICES}

    for path, bumps, body in changesets:
        for svc, btype in bumps.items():
            cur = highest_bumps.get(svc)
            if not cur or BUMP_PRIORITY[btype] > BUMP_PRIORITY[cur]:
                highest_bumps[svc] = btype
            entries_by_service[svc].append(body)

    # Compute new versions per service
    new_versions: Dict[str, str] = {}
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    
    changelog_section = [f"## [{today}] - Release via {branch}\n"]

    for svc in sorted(highest_bumps.keys()):
        btype = highest_bumps[svc]
        final_ver = compute_next_version(svc, btype, branch)
        new_versions[svc] = final_ver
        tag = f"{svc}-v{final_ver}"

        changelog_section.append(f"### {svc} ({final_ver})")
        for entry in entries_by_service[svc]:
            for line in entry.splitlines():
                line = line.strip()
                if line:
                    changelog_section.append(f"- {line}" if not line.startswith("-") and not line.startswith("*") else line)
        changelog_section.append("")

    # Prepend to CHANGELOG.md
    existing_changelog = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else "# Changelog\n\nAll notable changes to Syruss services.\n\n"
    header_end = existing_changelog.find("\n## ")
    if header_end != -1:
        updated = existing_changelog[:header_end] + "\n" + "\n".join(changelog_section) + "\n" + existing_changelog[header_end+1:]
    else:
        updated = existing_changelog.rstrip() + "\n\n" + "\n".join(changelog_section) + "\n"
    
    CHANGELOG_PATH.write_text(updated, encoding="utf-8")
    print(f"✔ Updated {CHANGELOG_PATH.name}")

    # Remove applied changesets
    for path, _, _ in changesets:
        path.unlink()
        print(f"✔ Removed applied changeset: {path.name}")

    # Output for GitHub Actions
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output and Path(gh_output).exists():
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"has_releases={'true' if new_versions else 'false'}\n")
            for s in SERVICES:
                if s in new_versions:
                    f.write(f"{s}_version={new_versions[s]}\n")
                    f.write(f"{s}_tag={s}-v{new_versions[s]}\n")
                    f.write(f"{s}_changed=true\n")
                else:
                    f.write(f"{s}_changed=false\n")

    print("\n🎉 Applied Changesets successfully:")
    for s, v in new_versions.items():
        print(f"  • {s:<8} → {s}-v{v}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Syruss Monorepo Changeset Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = subparsers.add_parser("add", help="Create a new changeset file")
    p_add.add_argument("-s", "--service", action="append", help="Service(s) to bump (server, agent, www, site, admin)")
    p_add.add_argument("-t", "--type", choices=["patch", "minor", "major"], help="Bump type")
    p_add.add_argument("-m", "--message", help="Changelog description")
    p_add.add_argument("--name", help="Custom filename for the changeset")

    # check
    p_check = subparsers.add_parser("check", help="Verify PR code changes include a valid changeset")
    p_check.add_argument("--base", help="Git base ref (e.g. origin/dev)")
    p_check.add_argument("--head", help="Git head ref")

    # status
    subparsers.add_parser("status", help="Show pending changesets and projected versions")

    # apply
    p_apply = subparsers.add_parser("apply", help="Apply changesets, bump versions, and update CHANGELOG.md")
    p_apply.add_argument("--branch", choices=["dev", "test", "main"], default="dev", help="Target branch for version naming")

    args = parser.parse_args()

    if args.command == "add":
        return cmd_add(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "apply":
        return cmd_apply(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
