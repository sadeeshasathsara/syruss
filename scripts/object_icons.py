"""
Writes the object icon list (server/app/services/object_icons.py) out to its readers:
- the agent's template-design guide, between its icon markers;
- www/src/lib/objectIcons.ts and admin/src/lib/objectIcons.ts, name -> lucide icon.

    python -m scripts.object_icons          # write
    python -m scripts.object_icons --check  # exit 1 if anything is out of step (CI, tests)
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "agentic_framework" / "agent" / "guides" / "template-design.md"
APPS = [ROOT / "www" / "src" / "lib" / "objectIcons.ts", ROOT / "admin" / "src" / "lib" / "objectIcons.ts"]
START, END = "<!-- object-icons:start (written by scripts/object_icons.py) -->", "<!-- object-icons:end -->"


def catalogue():
    spec = importlib.util.spec_from_file_location("object_icons", ROOT / "server" / "app" / "services" / "object_icons.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("-"))


def guide_block(mod) -> str:
    lines = [START]
    for group, icons in mod.GROUPS.items():
        lines.append(f"- **{group}:** " + "; ".join(f"`{n}` ({hint})" for n, hint in icons))
    lines.append(END)
    return "\n".join(lines)


def guide_text(mod, current: str) -> str:
    block = guide_block(mod)
    if START in current:
        return re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, current, flags=re.S)
    raise SystemExit(f"{GUIDE} has no icon markers")


def app_text(mod) -> str:
    names = mod.ICON_NAMES
    imports = ",\n  ".join(sorted({pascal(n) for n in names}))
    entries = "\n".join(f'  "{n}": {pascal(n)},' for n in names)
    groups = "\n".join(
        f'  {{ label: "{g}", icons: [{", ".join(f"{chr(34)}{n}{chr(34)}" for n, _ in icons)}] }},' for g, icons in mod.GROUPS.items())
    hints = "\n".join(f'  "{n}": "{h}",' for icons in mod.GROUPS.values() for n, h in icons)
    return f'''// Written by scripts/object_icons.py from server/app/services/object_icons.py. Don't edit by hand.
import type {{ LucideIcon }} from "lucide-react";
import {{
  {imports},
}} from "lucide-react";

export const DEFAULT_OBJECT_ICON = "{mod.DEFAULT_ICON}";

export const OBJECT_ICONS: Record<string, LucideIcon> = {{
{entries}
}};

/** The icons in groups, for the picker. */
export const OBJECT_ICON_GROUPS: {{ label: string; icons: string[] }}[] = [
{groups}
];

/** What each icon suits, so the picker can be searched by meaning as well as by name. */
export const OBJECT_ICON_HINTS: Record<string, string> = {{
{hints}
}};
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    check = ap.parse_args().check
    mod = catalogue()
    wanted = {GUIDE: guide_text(mod, GUIDE.read_text(encoding="utf-8"))}
    for path in APPS:
        wanted[path] = app_text(mod)
    stale = []
    for path, text in wanted.items():
        have = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if have != text:
            stale.append(path)
            if not check:
                path.write_bytes(text.encode("utf-8"))
    for path in stale:
        print(("out of step: " if check else "wrote: ") + str(path.relative_to(ROOT)))
    return 1 if check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
