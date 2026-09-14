"""Small, bounded validator for canonical NAD-<number> hotfix contracts."""

from __future__ import annotations

import re
from pathlib import Path

MAX_HOTFIX_BYTES = 64 * 1024
REQUIRED_METADATA = {
    "document_profile": "agent-primary",
    "canonicality": "canonical",
    "owner": "workflow",
    "workflow": "hotfix-v1",
}
REQUIRED_SECTIONS = ("Outcome", "Authorization", "Scope", "Origin", "Risks", "Validation")
ITEM_RE = re.compile(r"^NAD-[0-9]+$")


def validate_hotfix_text(text: str, item: str) -> list[str]:
    """Validate the closed frontmatter and required nonempty sections."""
    if len(text.encode("utf-8")) > MAX_HOTFIX_BYTES:
        return [f"hotfix exceeds {MAX_HOTFIX_BYTES} bytes."]
    problems: list[str] = []
    if not ITEM_RE.fullmatch(item):
        return ["item ID must match NAD-<number>."]
    lines = text.splitlines()
    if len(lines) < 3 or lines[0] != "---":
        return ["hotfix frontmatter is missing or malformed."]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return ["hotfix frontmatter is missing its closing delimiter."]

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([a-z_]+): ([A-Za-z0-9-]+)", line)
        if match is None:
            problems.append("hotfix frontmatter contains a malformed field.")
            continue
        key, value = match.groups()
        if key in metadata:
            problems.append(f"hotfix frontmatter repeats '{key}'.")
        metadata[key] = value
    expected = {**REQUIRED_METADATA, "item": item}
    if metadata != expected:
        if metadata.get("item") != item:
            problems.append(f"hotfix item must be '{item}'.")
        for key, value in REQUIRED_METADATA.items():
            if metadata.get(key) != value:
                problems.append(f"hotfix frontmatter requires {key}: {value}.")
        if set(metadata) - set(expected):
            problems.append("hotfix frontmatter contains an unsupported field.")

    sections: dict[str, list[str]] = {}
    active: str | None = None
    fence: tuple[str, int] | None = None
    for line in lines[end + 1 :]:
        if fence is not None:
            marker, width = fence
            if re.fullmatch(rf" {{0,3}}{re.escape(marker)}{{{width},}}[ \t]*", line):
                fence = None
            elif active is not None:
                sections[active].append(line)
            continue
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if opening is not None:
            marker, info = opening.groups()
            if marker[0] != "`" or "`" not in info:
                fence = (marker[0], len(marker))
                continue
        heading = re.fullmatch(r"## ([A-Za-z][A-Za-z ]*)", line)
        if heading is not None:
            active = heading.group(1)
            if active in sections:
                problems.append(f"hotfix section '{active}' is repeated.")
            sections.setdefault(active, [])
        elif active is not None:
            sections[active].append(line)
    if fence is not None:
        problems.append("hotfix contains an unclosed code fence.")
    for section in REQUIRED_SECTIONS:
        if not any(line.strip() for line in sections.get(section, [])):
            problems.append(f"hotfix section '{section}' is missing or empty.")
    if set(sections) - set(REQUIRED_SECTIONS):
        problems.append("hotfix contains an unsupported section.")
    return problems


def validate_hotfix_file(root: Path, path: Path, item: str) -> list[str]:
    """Validate a canonical repository file with a bounded UTF-8 read."""
    canonical = Path("docs") / "work" / item / "hotfix.md"
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return [f"hotfix must be the canonical path '{canonical.as_posix()}'."]
    if relative.as_posix() != canonical.as_posix() or not resolved.is_file():
        return [f"hotfix must be the canonical path '{canonical.as_posix()}'."]
    try:
        if resolved.stat().st_size > MAX_HOTFIX_BYTES:
            return [f"hotfix exceeds {MAX_HOTFIX_BYTES} bytes."]
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["hotfix is not UTF-8 text."]
    except OSError:
        return ["cannot read hotfix."]
    return validate_hotfix_text(text, item)
