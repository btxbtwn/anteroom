"""Always-on list-this title/description/pricing formulas for generation prompts."""

from __future__ import annotations

import re
from functools import lru_cache

from vendoo_studio.config import skills_dir
from vendoo_studio.services.user_settings import get_listing_formulas

_FORMULA_HEADING = re.compile(r"^##\s+Formula Reference\b.*$", re.M)
_SECTION_HEADING = re.compile(r"^##\s+\S", re.M)
_TITLE_BLOCK_RE = re.compile(r"(?s)(### TITLE Formula\n```\n).*?(\n```)")
_DESCRIPTION_BLOCK_RE = re.compile(r"(?s)(### DESCRIPTION Formula[^\n]*\n```\n).*?(\n```)")
_MARKER_LINE_RE = re.compile(r"(?m)^([A-Za-z][A-Za-z /]{1,20}):\s*\{")

DEFAULT_TITLE_FORMULA = "{BRAND} {SIZE} {VIBE} {ITEM} {COLOR} {FIT}"
DEFAULT_DESCRIPTION_FORMULA = (
    "{trendy vibe/style keyword sentence with period}\n\n"
    "Flaws:\n"
    "- {one flaw per line}\n"
    "See photos for details.\n\n"
    "Measurements: {See photos OR specific measurements}"
)
# A custom formula that still writes flaws on one line keeps that layout.
_FLAW_LIST_RE = re.compile(r"(?m)^[ \t]*-\s+\{")
DEFAULT_DESCRIPTION_MARKERS = ("measurements:",)
# A flawless item leaves its Flaws line out, so the marker is never required.
OPTIONAL_DESCRIPTION_MARKERS = frozenset({"flaws:"})

_FALLBACK_FORMULAS = f"""## Formula Reference (NON-NEGOTIABLE)

### TITLE Formula
```
{DEFAULT_TITLE_FORMULA}
```
- EXACT order
- Max 80 characters
- Example: `Levi's 33 Y2K 511 Slim Shorts Black Denim`

### DESCRIPTION Formula (Line breaks MANDATORY)
```
{DEFAULT_DESCRIPTION_FORMULA}
```
- Include the Flaws line only when the item has flaws; leave it out entirely when there are none.
- Max 918 characters, so the description still fits Mercari, Poshmark, and Depop together with the title.

### PRICING Formula
```
Listing Price = Market comp × 1.35 (round to nearest dollar)
Ceiling       = median asking price of 3+ similar live listings; price at or
                below it unless the item is clearly better (NWT, condition, size)
```
"""


@lru_cache(maxsize=1)
def _base_formula_rules(*, max_chars: int = 8000) -> str:
    """Return the non-negotiable Formula Reference section from list-this SKILL.md."""
    skill_md = skills_dir() / "list-this" / "SKILL.md"
    if not skill_md.is_file():
        return _FALLBACK_FORMULAS[:max_chars]
    text = skill_md.read_text(encoding="utf-8")
    start = _FORMULA_HEADING.search(text)
    if not start:
        return _FALLBACK_FORMULAS[:max_chars]
    rest = text[start.start() :]
    end = len(rest)
    for match in _SECTION_HEADING.finditer(rest):
        if match.start() > 0:
            end = match.start()
            break
    section = rest[:end].strip()
    if "TITLE Formula" not in section or "DESCRIPTION Formula" not in section:
        return _FALLBACK_FORMULAS[:max_chars]
    return section[:max_chars]


def listing_formula_rules(*, max_chars: int = 8000) -> str:
    """Base formula reference with any user-customized title/description swapped in."""
    text = _base_formula_rules(max_chars=max_chars)
    custom = get_listing_formulas()
    if not custom:
        return text
    title = custom.get("title")
    if title:
        text = _TITLE_BLOCK_RE.sub(lambda m: m.group(1) + title + m.group(2), text, count=1)
    description = custom.get("description")
    if description:
        text = _DESCRIPTION_BLOCK_RE.sub(lambda m: m.group(1) + description + m.group(2), text, count=1)
    return text[:max_chars]


def description_lists_flaws() -> bool:
    """True when the active description formula puts each flaw on its own line.

    A seller who customized the formula to a single Flaws line keeps that layout.
    """
    template = get_listing_formulas().get("description") or DEFAULT_DESCRIPTION_FORMULA
    return _FLAW_LIST_RE.search(template) is not None


def description_formula_markers() -> tuple[str, ...]:
    """Required `Label:` markers a generated description must contain.

    Derived from the active (custom or default) DESCRIPTION formula so validation
    stays in sync when the user edits the formula in Settings.
    """
    template = get_listing_formulas().get("description") or DEFAULT_DESCRIPTION_FORMULA
    labels = {f"{m.group(1).strip().lower()}:" for m in _MARKER_LINE_RE.finditer(template)}
    found = tuple(sorted(labels - OPTIONAL_DESCRIPTION_MARKERS))
    return found or DEFAULT_DESCRIPTION_MARKERS


@lru_cache(maxsize=1)
def etsy_when_made_rules() -> str:
    """Read the canonical date-estimation policy for every listing/repair prompt."""
    text = (skills_dir() / "list-this" / "SKILL.md").read_text(encoding="utf-8")
    heading = "## Etsy When Made Estimation"
    section = text[text.index(heading):]
    end = _SECTION_HEADING.search(section, len(heading))
    return section[:end.start() if end else len(section)].strip()


@lru_cache(maxsize=1)
def core_listing_rules() -> str:
    """Mandatory evidence and input policy, read from the canonical skill."""
    text = (skills_dir() / "list-this" / "SKILL.md").read_text(encoding="utf-8")
    sections = []
    for heading in ("## Input Requirements", "## Rules (VIOLATION = INCORRECT LISTING)"):
        start = text.index(heading)
        end = _SECTION_HEADING.search(text, start + len(heading))
        sections.append(text[start:end.start() if end else len(text)].strip())
    reference = (skills_dir() / "list-this" / "references" / "vendoo_listing_template.md").read_text(encoding="utf-8")
    start = reference.index("### **JSON Field Reference**")
    end = reference.index("\n# ", start)
    sections.append(reference[start:end].strip())
    return "\n\n".join(sections)


def with_pinned_formulas(skill_rules: str, *, max_chars: int = 20000) -> str:
    """Keep mandatory formulas and evidence policy ahead of complete retrieved sections."""
    pinned = f"{listing_formula_rules()}\n\n{etsy_when_made_rules()}\n\n{core_listing_rules()}"
    rest = str(skill_rules or "").strip()
    if rest.startswith(pinned):
        return rest[:max_chars]
    combined = pinned + (f"\n\n--- Additional listing rules ---\n\n{rest}" if rest else "")
    return combined[:max_chars]
