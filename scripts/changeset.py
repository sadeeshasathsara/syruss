#!/usr/bin/env python3
"""
Syruss Monorepo Changeset & Versioning Tool
Zero external dependencies (Python standard library only).

Commands:
  add     - Interactively create a new changeset fragment in .changeset/
  write   - Non-interactive 1-line creation of a changeset (agent & CI friendly)
  check   - Verify that code changes in a PR branch include a valid changeset (used in CI)
  status  - List pending changesets and projected SemVer bumps
  apply   - Apply changesets / promote changelog, update environment table, and bump versions
  table   - Regenerate the Environment Versions table in CHANGELOG.md based on git tags
"""

import argparse
import datetime
import os
from pathlib import Path
import random
import re
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

# Ensure stdout handles UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGESET_DIR = REPO_ROOT / ".changeset"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

# Import from registry if available, else fallback
try:
    from registry import SERVICES as REG_SERVICES, detect_changed_services
    SERVICES: Dict[str, str] = {name: cfg.path for name, cfg in REG_SERVICES.items()}
except Exception:
    SERVICES: Dict[str, str] = {
        "server": "server",
        "agent": "agentic_framework",
        "www": "www",
        "site": "site",
        "admin": "admin",
    }
    def detect_changed_services(changed_files: List[str]) -> List[str]:
        detected = set()
        for f in changed_files:
            for s, folder in SERVICES.items():
                if f.startswith(f"{folder}/"):
                    detected.add(s)
        return sorted(detected)

BUMP_PRIORITY: Dict[str, int] = {
    "patch": 1,
    "minor": 2,
    "major": 3,
}

ADJECTIVES = ["quick", "bright", "silent", "calm", "clever", "bold", "keen", "swift", "gentle", "brave", "cool", "warm"]
NOUNS = ["falcon", "tiger", "fox", "owl", "wolf", "badger", "hawk", "bear", "otter", "lynx", "hare", "eagle"]


def random_slug() -> str:
    """Generates human-readable random slug for changeset filename (e.g. swift-fox-482)."""
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    num = random.randint(100, 999)
    return f"{adj}-{noun}-{num}"


def get_git_diff_files(base_ref: str = "HEAD~1", head_ref: Optional[str] = None) -> List[str]:
    """Returns list of changed files between git refs."""
    try:
        cmd = ["git", "diff", "--name-only", base_ref]
        if head_ref:
            cmd.append(head_ref)
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError:
        try:
            result = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
            files = []
            for line in result.stdout.splitlines():
                if len(line) > 3:
                    files.append(line[3:].strip())
            return files
        except Exception:
            return []


def read_pending_changesets() -> List[Tuple[Path, Dict[str, str], str]]:
    """
    Reads all pending changeset markdown files in .changeset/ (ignoring README.md).
    Returns list of tuples: (file_path, {service: bump_type}, description)
    """
    if not CHANGESET_DIR.exists():
        return []

    changesets = []
    for file_path in sorted(CHANGESET_DIR.glob("*.md")):
        if file_path.name.lower() in ("readme.md", "template.md"):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
        if not match:
            continue

        frontmatter_raw = match.group(1).strip()
        body = match.group(2).strip()

        bumps: Dict[str, str] = {}
        for line in frontmatter_raw.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            svc, btype = line.split(":", 1)
            svc = svc.strip()
            btype = btype.strip().lower()
            if svc in SERVICES and btype in BUMP_PRIORITY:
                bumps[svc] = btype

        if bumps:
            changesets.append((file_path, bumps, body))

    return changesets


def parse_ver_tuple(v_str: str) -> Tuple[int, int, int, int, int]:
    """Parses SemVer string into sortable tuple: (major, minor, patch, stage_weight, increment)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(dev|rc)\.(\d+))?$", v_str)
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    stage = 3 if not m.group(4) else (1 if m.group(4) == "dev" else 2)
    inc = int(m.group(5)) if m.group(5) else 0
    return (major, minor, patch, stage, inc)


def get_current_env_versions() -> Dict[str, Dict[str, Optional[str]]]:
    """Scans existing CHANGELOG.md table and git tags to find latest version per service across dev, test, and main."""
    env_map: Dict[str, Dict[str, Optional[str]]] = {s: {"dev": None, "test": None, "main": None} for s in SERVICES}
    
    # 1. Parse existing table in CHANGELOG.md if present
    if CHANGELOG_PATH.exists():
        try:
            for line in CHANGELOG_PATH.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\|\s*\*\*(\w+)\*\*\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|", line)
                if m:
                    svc = m.group(1).strip()
                    d = m.group(2).strip().strip("`— ")
                    t = m.group(3).strip().strip("`— ")
                    m_val = m.group(4).strip().strip("`— ")
                    if svc in env_map:
                        env_map[svc]["dev"] = d or None
                        env_map[svc]["test"] = t or None
                        env_map[svc]["main"] = m_val or None
        except Exception:
            pass

    # 2. Merge with git tags (keeping highest SemVer)
    try:
        res = subprocess.run(["git", "tag", "-l"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        tags = [t.strip() for t in res.stdout.splitlines() if t.strip()]
        for s in SERVICES:
            for t in tags:
                m_dev = re.match(rf"^{s}-v(\d+\.\d+\.\d+-dev\.\d+)$", t)
                if m_dev:
                    v = m_dev.group(1)
                    cur = env_map[s]["dev"]
                    if not cur or parse_ver_tuple(v) > parse_ver_tuple(cur):
                        env_map[s]["dev"] = v
                m_rc = re.match(rf"^{s}-v(\d+\.\d+\.\d+-rc\.\d+)$", t)
                if m_rc:
                    v = m_rc.group(1)
                    cur = env_map[s]["test"]
                    if not cur or parse_ver_tuple(v) > parse_ver_tuple(cur):
                        env_map[s]["test"] = v
                m_main = re.match(rf"^{s}-v(\d+\.\d+\.\d+)$", t)
                if m_main:
                    v = m_main.group(1)
                    cur = env_map[s]["main"]
                    if not cur or parse_ver_tuple(v) > parse_ver_tuple(cur):
                        env_map[s]["main"] = v
    except Exception:
        pass
    return env_map


def format_env_table(env_map: Dict[str, Dict[str, Optional[str]]]) -> str:
    """Formats top Markdown table displaying current versions per service across environments."""
    lines = [
        "## Current Environment Versions",
        "",
        "| Service | Development (`dev`) | Staging (`test`) | Production (`main`) |",
        "| :--- | :---: | :---: | :---: |",
    ]
    for s in sorted(SERVICES.keys()):
        d = f"`{env_map[s]['dev']}`" if env_map[s]["dev"] else "—"
        t = f"`{env_map[s]['test']}`" if env_map[s]["test"] else "—"
        m = f"`{env_map[s]['main']}`" if env_map[s]["main"] else "—"
        lines.append(f"| **{s}** | {d} | {t} | {m} |")
    return "\n".join(lines)


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
    return compute_version_from_base(service, base, branch)


def compute_version_from_base(service: str, base: str, branch: str) -> str:
    """Computes version string for a given base version according to the target branch."""
    if branch == "main":
        return base

    suffix = "rc" if branch == "test" else "dev"
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


def parse_unpromoted_entries(changelog_text: str, target_branch: str) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """
    Parses unpromoted sections from CHANGELOG.md for promotion to test or main.
    Returns (entries_by_service, base_versions_by_service).
    """
    blocks = re.split(r"(?=\n## \[)", "\n" + changelog_text)
    entries: Dict[str, List[str]] = {}
    base_versions: Dict[str, str] = {}

    for block in blocks:
        block = block.strip()
        if not block.startswith("## ["):
            continue
        first_line = block.splitlines()[0]

        # Stop criteria when scanning downward
        if target_branch == "test":
            if "Release Candidate (test)" in first_line or "Production Release (main)" in first_line:
                break
        elif target_branch == "main":
            if "Production Release (main)" in first_line:
                break

        # Parse services and bullet lines in this block
        cur_svc = None
        for line in block.splitlines():
            m_svc = re.match(r"^###\s+(\w+)\s+\((\d+\.\d+\.\d+).*?\)", line)
            if m_svc:
                cur_svc = m_svc.group(1)
                base = m_svc.group(2)
                if cur_svc in SERVICES:
                    if cur_svc not in base_versions:
                        base_versions[cur_svc] = base
                    if cur_svc not in entries:
                        entries[cur_svc] = []
            elif cur_svc and (line.startswith("- ") or line.startswith("* ")):
                bullet = line.strip()
                if bullet not in entries[cur_svc]:
                    entries[cur_svc].append(bullet)

    return entries, base_versions


def update_changelog_file(new_section_lines: Optional[List[str]], new_versions: Dict[str, str], branch: str) -> None:
    """Updates CHANGELOG.md: refreshes the environment table and prepends new release notes."""
    env_map = get_current_env_versions()
    for svc, ver in new_versions.items():
        if branch == "dev":
            env_map[svc]["dev"] = ver
        elif branch == "test":
            env_map[svc]["test"] = ver
        elif branch == "main":
            env_map[svc]["main"] = ver

    existing = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else "# Changelog\n\nAll notable changes to Syruss services.\n\n"

    history_marker = "## Release History"
    if history_marker in existing:
        history = existing.split(history_marker, 1)[1].lstrip("\n")
    else:
        first_rel = existing.find("\n## [")
        history = existing[first_rel + 1:] if first_rel != -1 else ""

    if new_section_lines:
        section_text = "\n".join(new_section_lines).strip()
        if history.strip():
            history = section_text + "\n\n" + history.strip() + "\n"
        else:
            history = section_text + "\n"

    table_text = format_env_table(env_map)
    full_text = f"# Changelog\n\nAll notable changes to Syruss services.\n\n{table_text}\n\n---\n\n## Release History\n\n{history}"
    CHANGELOG_PATH.write_text(full_text.rstrip() + "\n", encoding="utf-8")
    print(f"[OK] Updated {CHANGELOG_PATH.name} (table & release notes)")


# ------------------------------------------------------------------------------
# CLI Commands
# ------------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    """Interactively creates a new changeset fragment file in .changeset/."""
    CHANGESET_DIR.mkdir(parents=True, exist_ok=True)
    bumps: Dict[str, str] = {}
    message: str = ""

    print("------------------------------------------------------------")
    print("  Syruss Changeset Creator")
    print("------------------------------------------------------------\n")
    
    changed_files = get_git_diff_files("origin/dev") or get_git_diff_files("dev") or get_git_diff_files()
    detected_svcs = detect_changed_services(changed_files)

    if detected_svcs:
        print(f"[INFO] Detected changes in services: {', '.join(detected_svcs)}\n")

    print("Select which services are affected by your changes:")
    svc_keys = sorted(SERVICES.keys())
    for idx, svc in enumerate(svc_keys, 1):
        hint = " (detected changes)" if svc in detected_svcs else ""
        print(f"  [{idx}] {svc:<8} ({SERVICES[svc]}){hint}")
    
    print("\nEnter service numbers separated by commas (e.g. 1, 3):")
    chosen_input = input("> ").strip()
    if not chosen_input:
        print("[ERROR] No services selected.")
        return 1
        
    chosen_svcs = []
    for part in chosen_input.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(svc_keys):
            chosen_svcs.append(svc_keys[int(part) - 1])
        elif part in svc_keys:
            chosen_svcs.append(part)

    if not chosen_svcs:
        print("[ERROR] No valid services selected.")
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

    slug = args.name or random_slug()
    target_file = CHANGESET_DIR / f"{slug}.md"

    fm_lines = [f"{s}: {bumps[s]}" for s in sorted(bumps.keys())]
    content = f"---\n" + "\n".join(fm_lines) + f"\n---\n\n{message}\n"
    target_file.write_text(content, encoding="utf-8")

    print(f"\n[OK] Created changeset file: .changeset/{target_file.name}")
    print("------------------------------------------------------------")
    print(content.strip())
    print("------------------------------------------------------------")
    print("[NOTE] Remember to commit this file to your branch before opening/merging your PR!\n")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """
    Non-interactive single-command creation of a changeset fragment.
    Designed for AI coding agents and automated CI scripts.
    Auto-detects modified services from git diff if not explicitly specified.
    """
    CHANGESET_DIR.mkdir(parents=True, exist_ok=True)
    bumps: Dict[str, str] = {}

    # 1. Parse explicit --bump flags (e.g. --bump www=minor --bump server=patch)
    if args.bump:
        for b in args.bump:
            delim = "=" if "=" in b else (":" if ":" in b else None)
            if not delim:
                print(f"[ERROR] Invalid --bump format '{b}'. Expected 'service=type' (e.g. www=minor).")
                return 1
            s_name, b_type = b.split(delim, 1)
            s_name = s_name.strip().lower()
            b_type = b_type.strip().lower()
            if s_name not in SERVICES:
                print(f"[ERROR] Unknown service '{s_name}'. Registered services: {', '.join(sorted(SERVICES.keys()))}")
                return 1
            if b_type not in BUMP_PRIORITY:
                print(f"[ERROR] Unknown bump level '{b_type}'. Allowed: patch, minor, major.")
                return 1
            bumps[s_name] = b_type

    # 2. Auto-detect from git if requested or if no bumps provided
    if args.auto_detect or not bumps:
        changed_files = get_git_diff_files("origin/dev") or get_git_diff_files("dev") or get_git_diff_files()
        detected = detect_changed_services(changed_files)
        for s in detected:
            if s not in bumps:
                bumps[s] = "patch"  # Default unassigned detected services to patch

    if not bumps:
        print("[ERROR] No services specified or detected with changes.")
        print("Please use --bump <service>=<type> or ensure git changes are present.")
        return 1

    # 3. Assemble message lines
    messages = args.message or []
    if not messages:
        print("[ERROR] Changeset description cannot be empty. Provide --message \"Description\".")
        return 1

    formatted_body_lines = []
    for m in messages:
        for line in m.strip().splitlines():
            line = line.strip()
            if line:
                if not (line.startswith("- ") or line.startswith("* ")):
                    formatted_body_lines.append(f"- {line}")
                else:
                    formatted_body_lines.append(line)
    body_text = "\n".join(formatted_body_lines)

    slug = args.name or random_slug()
    target_file = CHANGESET_DIR / f"{slug}.md"

    fm_lines = [f"{s}: {bumps[s]}" for s in sorted(bumps.keys())]
    content = f"---\n" + "\n".join(fm_lines) + f"\n---\n\n{body_text}\n"
    target_file.write_text(content, encoding="utf-8")

    print(f"[OK] Created changeset file: .changeset/{target_file.name}")
    print("------------------------------------------------------------")
    print(content.strip())
    print("------------------------------------------------------------")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Used in PR CI workflow to enforce that code changes include a valid changeset."""
    base_ref = args.base or os.environ.get("GITHUB_BASE_REF", "origin/dev")
    head_ref = args.head or os.environ.get("GITHUB_HEAD_REF")
    
    changed_files = get_git_diff_files(base_ref, head_ref)
    touched_services = set(detect_changed_services(changed_files))

    if not touched_services:
        print("[OK] No service code changed (docs or infrastructure only). Changeset not required.")
        return 0

    changesets = read_pending_changesets()
    if not changesets:
        print("\n[ERROR] PR VALIDATION FAILED: No changeset fragment found in .changeset/")
        print("------------------------------------------------------------")
        print(f"This PR modifies code in services: {', '.join(sorted(touched_services))}")
        print("To fix this, please run:")
        print("    syruss changeset write --bump <service>=<type> -m \"Description\"")
        print("or:")
        print("    syruss changeset add")
        print("and commit the generated .changeset/<name>.md file.")
        print("------------------------------------------------------------\n")
        return 1

    covered_services = set()
    print("\n[INFO] Validating pending changesets:")
    for path, bumps, body in changesets:
        first_line = body.splitlines()[0] if body else ""
        print(f"  * {path.name}: {bumps} - {first_line[:60]}...")
        covered_services.update(bumps.keys())

    missing = touched_services - covered_services
    if missing:
        print(f"\n[WARN] Changes were detected in [{', '.join(sorted(missing))}] but no changeset declared them.")
        print("Please ensure your changeset frontmatter includes all modified services.\n")
        return 1

    print("\n[OK] All modified services are covered by valid changesets. Checks passed!\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Shows pending changesets and projected version bumps."""
    changesets = read_pending_changesets()
    if not changesets:
        print("[INFO] No pending changesets found in .changeset/.")
        return 0

    highest_bumps: Dict[str, str] = {}
    for _, bumps, _ in changesets:
        for svc, btype in bumps.items():
            cur = highest_bumps.get(svc)
            if not cur or BUMP_PRIORITY[btype] > BUMP_PRIORITY[cur]:
                highest_bumps[svc] = btype

    print("------------------------------------------------------------")
    print(f"  Pending Changesets ({len(changesets)} file(s))")
    print("------------------------------------------------------------")
    for path, bumps, body in changesets:
        print(f"\n* File: {path.name}")
        for s, t in bumps.items():
            print(f"  - {s}: {t}")
        print(f"  Description:\n    {body}")

    print("\n------------------------------------------------------------")
    print("  Projected Next Bumps on Release:")
    print("------------------------------------------------------------")
    for s in sorted(highest_bumps.keys()):
        print(f"  * {s:<8} -> {highest_bumps[s].upper()} bump")
    print()
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """
    Applies changesets (on dev) or promotes changelog (on test/main),
    updates CHANGELOG.md and its top Environment Versions table,
    and outputs variables for GitHub Actions.
    """
    branch = args.branch or "dev"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    changesets = read_pending_changesets()

    new_versions: Dict[str, str] = {}
    changelog_section: List[str] = []

    title_map = {
        "dev": "Release via dev",
        "test": "Release Candidate (test)",
        "main": "Production Release (main)",
    }
    rel_title = title_map.get(branch, f"Release via {branch}")

    # Case 1: Pending changesets exist in .changeset/
    if changesets:
        highest_bumps: Dict[str, str] = {}
        entries_by_service: Dict[str, List[str]] = {s: [] for s in SERVICES}

        for path, bumps, body in changesets:
            for svc, btype in bumps.items():
                cur = highest_bumps.get(svc)
                if not cur or BUMP_PRIORITY[btype] > BUMP_PRIORITY[cur]:
                    highest_bumps[svc] = btype
                entries_by_service[svc].append(body)

        changelog_section.append(f"## [{today}] - {rel_title}\n")
        for svc in sorted(highest_bumps.keys()):
            btype = highest_bumps[svc]
            final_ver = compute_next_version(svc, btype, branch)
            new_versions[svc] = final_ver

            changelog_section.append(f"### {svc} ({final_ver})")
            for entry in entries_by_service[svc]:
                for line in entry.splitlines():
                    line = line.strip()
                    if line:
                        changelog_section.append(f"- {line}" if not (line.startswith("-") or line.startswith("*")) else line)
            changelog_section.append("")

        for path, _, _ in changesets:
            path.unlink()
            print(f"[OK] Removed applied changeset: {path.name}")

    # Case 2: No changesets in .changeset/, promote entries from CHANGELOG.md for test/main
    elif branch in ("test", "main"):
        existing_text = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else ""
        entries, base_versions = parse_unpromoted_entries(existing_text, branch)

        if not entries:
            print(f"[INFO] No unpromoted entries found in {CHANGELOG_PATH.name} for {branch}.")
            update_changelog_file(None, {}, branch)
            _write_gh_output({})
            return 0

        changelog_section.append(f"## [{today}] - {rel_title}\n")
        for svc in sorted(entries.keys()):
            base = base_versions.get(svc, "0.1.0")
            final_ver = compute_version_from_base(svc, base, branch)
            new_versions[svc] = final_ver

            changelog_section.append(f"### {svc} ({final_ver})")
            for bullet in entries[svc]:
                changelog_section.append(bullet)
            changelog_section.append("")

    else:
        print("[INFO] No changesets to apply.")
        update_changelog_file(None, {}, branch)
        _write_gh_output({})
        return 0

    update_changelog_file(changelog_section, new_versions, branch)
    _write_gh_output(new_versions)

    print("\n[SUCCESS] Applied Releases successfully:")
    for s, v in new_versions.items():
        print(f"  * {s:<8} -> {s}-v{v}")
    print()
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    """Regenerates the Environment Versions table in CHANGELOG.md without creating a release."""
    update_changelog_file(None, {}, "dev")
    return 0


def _write_gh_output(new_versions: Dict[str, str]) -> None:
    """Writes release variables to $GITHUB_OUTPUT if running in GitHub Actions."""
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


def build_changeset_parser(subparsers=None):
    """Constructs parser for changeset commands."""
    if subparsers is None:
        parser = argparse.ArgumentParser(description="Syruss Changeset Tool")
        sub = parser.add_subparsers(dest="command", required=True)
    else:
        sub = subparsers

    # add
    p_add = sub.add_parser("add", help="Interactively create a changeset file")
    p_add.add_argument("--name", help="Custom filename for changeset")

    # write (Agent & CI 1-liner)
    p_write = sub.add_parser("write", help="Non-interactive single-command changeset writer for agents & CI")
    p_write.add_argument("-b", "--bump", action="append", help="Service bump level in format <service>=<type> (e.g. www=minor)")
    p_write.add_argument("-m", "--message", action="append", required=True, help="Changelog bullet point (can be repeated)")
    p_write.add_argument("--auto-detect", action="store_true", default=True, help="Auto-detect modified services via git diff (default: True)")
    p_write.add_argument("--name", help="Custom filename for changeset")

    # check
    p_check = sub.add_parser("check", help="Verify PR code changes include a valid changeset")
    p_check.add_argument("--base", help="Git base ref (e.g. origin/dev)")
    p_check.add_argument("--head", help="Git head ref")

    # status
    sub.add_parser("status", help="Show pending changesets and projected versions")

    # apply
    p_apply = sub.add_parser("apply", help="Apply changesets / promote changelog, update table, and bump versions")
    p_apply.add_argument("--branch", choices=["dev", "test", "main"], default="dev", help="Target branch for version naming")

    # table
    sub.add_parser("table", help="Regenerate environment versions table in CHANGELOG.md")

    return sub


def main(custom_args=None) -> int:
    parser = argparse.ArgumentParser(description="Syruss Monorepo Changeset Tool")
    sub = parser.add_subparsers(dest="command", required=True)
    build_changeset_parser(sub)

    args = parser.parse_args(custom_args)

    if args.command == "add":
        return cmd_add(args)
    elif args.command == "write":
        return cmd_write(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "apply":
        return cmd_apply(args)
    elif args.command == "table":
        return cmd_table(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
