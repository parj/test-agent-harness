"""
Skill loader — reads markdown files from skills/, each describing a
step-by-step finance workflow. Skills are files, not database rows, so
adding one is just dropping a new .md file in the folder.

Expected file shape:

    name: Skill Name
    description: One-line description
    tools_required: [tool_one, tool_two]

    ## Instructions
    1. Step one...
    2. Step two...
"""
import os
import re
from dataclasses import dataclass

from config import settings


@dataclass
class Skill:
    name: str
    description: str
    tools_required: list[str]
    instructions: str
    source_path: str


_cache: dict[str, "Skill"] | None = None


def _parse_skill_file(path: str) -> Skill:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    meta_block, _, instructions = text.partition("## Instructions")
    meta = {}
    for line in meta_block.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    tools_raw = meta.get("tools_required", "[]")
    tools_required = [t.strip() for t in re.sub(r"[\[\]]", "", tools_raw).split(",") if t.strip()]

    return Skill(
        name=meta.get("name", os.path.splitext(os.path.basename(path))[0]),
        description=meta.get("description", ""),
        tools_required=tools_required,
        instructions=instructions.strip(),
        source_path=path,
    )


def load_skills(force_reload: bool = False) -> dict[str, Skill]:
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    skills = {}
    if os.path.isdir(settings.skills_dir):
        for fname in sorted(os.listdir(settings.skills_dir)):
            if fname.endswith(".md"):
                skill = _parse_skill_file(os.path.join(settings.skills_dir, fname))
                skills[skill.name] = skill

    _cache = skills
    return skills


def get_skill(name: str) -> Skill | None:
    return load_skills().get(name)
