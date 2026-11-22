"""Agent Skills SKILL.md YAML frontmatter parser and strict name validator (stdlib + yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_COMPATIBILITY_LENGTH = 500

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL)


@dataclass(frozen=True)
class ParsedSkillFrontmatter:
    """Validated metadata and markdown body extracted from a SKILL.md document."""

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    body: str = ""


def validate_skill_name(name: str) -> str:
    """Validate a skill identifier against the strict Agent Skills slug policy."""
    if not isinstance(name, str) or not SKILL_NAME_RE.fullmatch(name):
        raise ValueError(
            "Skill name must be 1-64 lowercase alphanumeric characters or single hyphens."
        )
    if name.endswith("-") or "--" in name:
        raise ValueError("Skill name must not end with '-' or contain consecutive hyphens '--'.")
    return name


def is_valid_skill_name(name: str) -> bool:
    """Return True when name satisfies the strict skill identifier grammar."""
    try:
        validate_skill_name(name)
    except ValueError:
        return False
    return True


def parse_skill_frontmatter(
    content: str,
    *,
    strict_name: bool = True,
) -> ParsedSkillFrontmatter:
    """Parse and validate YAML frontmatter from SKILL.md content."""
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise ValueError(f"SKILL.md exceeds maximum size of {MAX_SKILL_FILE_SIZE} bytes.")

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise ValueError("SKILL.md is missing valid YAML frontmatter delimited by '---'.")

    raw_yaml = match.group(1)
    try:
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in SKILL.md frontmatter: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping.")

    raw_name = loaded.get("name")
    raw_desc = loaded.get("description")
    name = str(raw_name).strip() if raw_name is not None else ""
    description = str(raw_desc).strip() if raw_desc is not None else ""
    if not name or not description:
        raise ValueError("SKILL.md frontmatter requires non-empty 'name' and 'description'.")

    if strict_name:
        validate_skill_name(name)

    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        description = description[:MAX_SKILL_DESCRIPTION_LENGTH]

    raw_license = loaded.get("license")
    license_val = str(raw_license).strip() or None if raw_license is not None else None

    raw_compat = loaded.get("compatibility")
    compat_val = str(raw_compat).strip() or None if raw_compat is not None else None
    if compat_val is not None and len(compat_val) > MAX_SKILL_COMPATIBILITY_LENGTH:
        compat_val = compat_val[:MAX_SKILL_COMPATIBILITY_LENGTH]

    raw_meta = loaded.get("metadata")
    metadata: dict[str, str] = {}
    if isinstance(raw_meta, dict):
        metadata = {str(k): str(v) for k, v in raw_meta.items()}

    body = content[match.end() :].strip()
    return ParsedSkillFrontmatter(
        name=name,
        description=description,
        license=license_val,
        compatibility=compat_val,
        metadata=metadata,
        body=body,
    )
