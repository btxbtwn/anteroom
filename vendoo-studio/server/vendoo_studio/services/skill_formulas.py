"""Always-on list-this title/description/pricing formulas for generation prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
# Chosen when the seller turns every description section off. Distinct from an
# empty setting, which still means the built-in formula.
NO_SECTION_DESCRIPTION = "{one short factual description with no labeled sections}"
_SELLER_FORMULA_NOTE = (
    "\n\nSeller description formula: follow the description template above exactly, "
    "including any section it adds and omitting any section it leaves out."
)
_STOCK_DESCRIPTION_LIMIT = (
    "Do not add Size, a full condition write-up, shipping speed, or bundle/discount lines "
    "— those are covered by marketplace fields and marketplace-level promos already."
)
_CUSTOM_DESCRIPTION_LIMIT = (
    "Do not add a section the description template omits. Include a section the template "
    "shows when the item supports it, including size, condition, shipping, or bundles. "
    "Leave a templated line out only when the item does not support that fact."
)


@dataclass(frozen=True)
class DescriptionBlock:
    id: str
    label: str
    detail: str
    snippet: str
    default: bool
    optional: bool = False


# Catalog order is the order a generated description uses. The default ids
# compile to DEFAULT_DESCRIPTION_FORMULA exactly.
DESCRIPTION_BLOCKS: tuple[DescriptionBlock, ...] = (
    DescriptionBlock(
        "vibe",
        "Vibe",
        "One short sentence of style and trend keywords.",
        "{trendy vibe/style keyword sentence with period}",
        True,
    ),
    DescriptionBlock(
        "item",
        "Brand, item, color, and size",
        "The brand, what it is, its color, and the tag size.",
        "Item: {brand, item, color, and tag size}",
        False,
    ),
    DescriptionBlock(
        "trend",
        "Trend",
        "One supported decade or trend, such as Y2K or 90s.",
        "Trend: {one supported decade or trend, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "fit",
        "Fit",
        "One recorded fit, such as runs small or oversized.",
        "Fit: {one recorded fit clause, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "material",
        "Material",
        "Fiber from the label, with blend percentages.",
        "Material: {fiber from the label with blend percentages, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "pattern",
        "Pattern",
        "The print, such as stripe or floral.",
        "Pattern: {the print, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "occasion",
        "Occasion",
        "One real use, such as work or a wedding.",
        "Occasion: {one real use, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "details",
        "Construction",
        "Pockets, closure, neckline, rise, or hem when the photos hide them.",
        "Details: {pockets, closure, neckline, rise, or hem the photos hide, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "flaws",
        "Flaws",
        "Each flaw on its own line. Leave the block out when there are none.",
        "Flaws:\n- {one flaw per line}\nSee photos for details.",
        True,
    ),
    DescriptionBlock(
        "measurements",
        "Measurements",
        "Flat measurements, or See photos.",
        "Measurements: {See photos OR specific measurements}",
        True,
    ),
    DescriptionBlock(
        "condition",
        "Condition",
        "The same grade as the condition field, in a few words.",
        "Condition: {the condition grade in words, matching the condition field}",
        False,
    ),
    DescriptionBlock(
        "care",
        "Care",
        "Care from the label.",
        "Care: {care from the label, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "home",
        "Smoke-free or pet-free",
        "Only when it is true.",
        "Home: {smoke-free or pet-free, only when true, or leave this line out}",
        False,
        True,
    ),
    DescriptionBlock(
        "shipping",
        "Shipping and bundles",
        "A ship window and one bundle note.",
        "Shipping: {ship window and one bundle note, or leave this line out}",
        False,
        True,
    ),
)


@lru_cache(maxsize=1)
def _base_formula_rules(*, max_chars: int = 8000) -> str:
    """Return the non-negotiable Formula Reference section from list-this SKILL.md."""
    skill_md = skills_dir() / "list-this" / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"The list-this skill is missing: {skill_md} does not exist")
    text = skill_md.read_text(encoding="utf-8")
    start = _FORMULA_HEADING.search(text)
    if not start:
        raise ValueError(f"{skill_md} has no Formula Reference section")
    rest = text[start.start() :]
    end = len(rest)
    for match in _SECTION_HEADING.finditer(rest):
        if match.start() > 0:
            end = match.start()
            break
    section = rest[:end].strip()
    if "TITLE Formula" not in section or "DESCRIPTION Formula" not in section:
        raise ValueError(f"{skill_md} Formula Reference lacks the TITLE or DESCRIPTION formula")
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
        if description.strip() != DEFAULT_DESCRIPTION_FORMULA.strip():
            text = text.replace(_STOCK_DESCRIPTION_LIMIT, _CUSTOM_DESCRIPTION_LIMIT, 1)
            text += _SELLER_FORMULA_NOTE
    return text[:max_chars]


def active_description_template() -> str:
    return get_listing_formulas().get("description") or DEFAULT_DESCRIPTION_FORMULA


def description_has_section(label: str) -> bool:
    """True when the active description formula has a ``Label:`` line."""
    return re.search(rf"(?im)^{re.escape(label)}\s*:", active_description_template()) is not None


def _optional_description_markers() -> frozenset[str]:
    labels = set(OPTIONAL_DESCRIPTION_MARKERS)
    for block in DESCRIPTION_BLOCKS:
        if not block.optional:
            continue
        for match in _MARKER_LINE_RE.finditer(block.snippet):
            labels.add(f"{match.group(1).strip().lower()}:")
    return frozenset(labels)


def default_description_block_ids() -> tuple[str, ...]:
    return tuple(block.id for block in DESCRIPTION_BLOCKS if block.default)


def compile_description_blocks(ids: list[str] | tuple[str, ...]) -> str:
    """Join the chosen sections in catalog order. Unknown ids are rejected."""
    known = {block.id for block in DESCRIPTION_BLOCKS}
    unknown = [item for item in ids if item not in known]
    if unknown:
        raise ValueError(f"Unknown description section: {unknown[0]}")
    chosen = set(ids)
    parts = [block.snippet for block in DESCRIPTION_BLOCKS if block.id in chosen]
    if not parts:
        return NO_SECTION_DESCRIPTION
    return "\n\n".join(parts)


def match_description_blocks(text: str) -> tuple[str, ...] | None:
    """Block ids that compile to this formula, or None when it was typed freely."""
    stripped = text.strip()
    if stripped == NO_SECTION_DESCRIPTION:
        return ()
    present = tuple(block.id for block in DESCRIPTION_BLOCKS if block.snippet in stripped)
    if compile_description_blocks(present).strip() == stripped:
        return present
    return None


def description_block_selection(stored: str) -> tuple[tuple[str, ...], bool]:
    """Enabled block ids and whether the stored formula is free text.

    An empty setting is the built-in formula, not a custom one.
    """
    if not stored.strip():
        return default_description_block_ids(), False
    matched = match_description_blocks(stored)
    if matched is None:
        return (), True
    return matched, False


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
    custom = get_listing_formulas().get("description")
    template = custom or DEFAULT_DESCRIPTION_FORMULA
    labels = {f"{m.group(1).strip().lower()}:" for m in _MARKER_LINE_RE.finditer(template)}
    found = tuple(sorted(labels - _optional_description_markers()))
    if custom:
        return found
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
