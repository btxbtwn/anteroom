"""Match a category path onto one marketplace's own category list.

Each marketplace names the same item its own way: General's "Men's Clothing >
Shirts > T-Shirts" is "Men > Shirts > Tees - Short Sleeve" on Poshmark and
"Men > Tops > T-shirts" on Depop and Mercari. A row matches when its last step
names the same item (the same main noun, and no extra qualifier the category or
the listing does not say) inside the same department. A tie returns nothing, so
the seller picks rather than getting a guess.

The rows come from the same lists the marketplace forms show, so a match is
always one of that form's dropdown options.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, NamedTuple

_SPLIT = re.compile(r"\s*(?:>|›)\s*")
_ALTERNATIVES = re.compile(r"\s*(?:,|&|/|\band\b|\bor\b)\s*", re.I)
_PHRASES = (
    (re.compile(r"\bt[\s-]?shirts?\b", re.I), "tshirt"),
    (re.compile(r"\bathletic shoes?\b", re.I), "sneaker"),
    (re.compile(r"\bbutton[\s-]?(?:down|up|front)\b", re.I), "buttondown"),
)
_SYNONYMS = {
    "tshirt": "tee", "hoody": "hoodie", "trouser": "pant", "denim": "jean",
    "purse": "handbag", "trainer": "sneaker", "cap": "hat",
    "swimwear": "swim", "swimsuit": "swim", "sleepwear": "pajama", "pyjama": "pajama", "outerwear": "coat",
}
_STOP = {"the", "of", "for", "with", "up", "size", "sizes", "s", "in", "a", "and", "or", "mo", "mos"}
# Words that do not name an item: "Other", "None", "Men's Clothing", "Accessories".
_GENERIC = {
    "other", "none", "more", "all", "clothing", "apparel", "specialty", "fashion", "everything", "else",
    "accessory", "part", "supply", "equipment", "item", "product", "misc", "miscellaneous",
}
# Branches for a narrower kind of item. A row inside one needs the category or listing to say so.
_NICHE = {
    "vintage", "handmade", "uniform", "school", "costume", "maternity", "dancewear", "bridal",
    "wedding", "pet", "dog", "cat", "doll", "toy", "golf", "fishing", "scrub", "traditional",
}
_APPAREL = "clothing, shoes & accessories"
# Branches outside clothing a department's item could share a word with:
# Fragrance sets, Cleaning gloves, Storage organizers.
_HOUSEHOLD = {
    "beauty", "fragrance", "makeup", "skincare", "home", "cleaning", "kitchen", "storage", "office", "tool",
    "garden", "household",
}
# Branches that also name an item ("Men > Kimono / Yukata > Kimono"): a row
# inside one still needs the category or listing to say so.
_NICHE_BRANCH = {"kimono", "yukata"}
_DEPARTMENTS = {
    "women": "women", "woman": "women", "ladie": "women", "womenswear": "women",
    "men": "men", "man": "men", "menswear": "men",
    "boy": "kids", "girl": "kids", "kid": "kids", "baby": "kids", "toddler": "kids", "kidswear": "kids",
}


def _word(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        word = word[:-2]
    elif len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return _SYNONYMS.get(word, word)


def _normalized(text: Any) -> str:
    text = str(text or "")
    for pattern, replacement in _PHRASES:
        text = pattern.sub(replacement, text)
    return re.sub(r"['’]s?\b", "", text)


def words(text: Any) -> list[str]:
    return [_word(word) for word in re.findall(r"[A-Za-z0-9]+", _normalized(text)) if word.lower() not in _STOP]


def segments(path: Any) -> list[str]:
    return [part.strip() for part in _SPLIT.split(str(path or "")) if part.strip()]


def department(path: Any) -> str:
    for segment in segments(path):
        for word in words(segment):
            if word in _DEPARTMENTS:
                return _DEPARTMENTS[word]
    return ""


def _nouns(part: str) -> list[str]:
    return [
        word for word in words(part)
        if word not in _DEPARTMENTS and word not in _GENERIC and word not in _NICHE and not re.search(r"\d", word)
    ]


@lru_cache(maxsize=65536)
def _alternatives(segment: str) -> tuple[tuple[str, frozenset[str]], ...]:
    """A step's items as (main noun, qualifiers).

    "Tees - Long Sleeve" is tees qualified by long sleeve. "Film & Polaroid
    Cameras" is film cameras and Polaroid cameras.
    """
    main, _, rest = _normalized(segment).partition(" - ")
    parts = [found for found in (_nouns(part) for part in _ALTERNATIVES.split(main)) if found]
    extra = frozenset(words(rest)) - frozenset(_DEPARTMENTS)
    last = parts[-1] if parts else []
    items = []
    for part in parts:
        items.append((part[-1], frozenset(part[:-1]) | extra))
        if len(part) == 1 and len(last) > 1 and part is not last:
            items.append((last[-1], frozenset({part[0]}) | extra))
    return tuple(items)


def _baby(key: str) -> bool:
    return bool(re.search(r"\b(?:baby|infant|toddler|newborn|0 24)\b", key))


@lru_cache(maxsize=65536)
def _suffix(segment: str) -> frozenset[str]:
    """The words after " - " in a step: Long Sleeve in "Tees - Long Sleeve"."""
    return frozenset(words(_normalized(segment).partition(" - ")[2]))


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


class _Path(NamedTuple):
    key: str
    department: str
    words: frozenset[str]
    # The steps naming the item, last first, skipping Other, None, or an age range.
    steps: tuple[str, ...]
    generic_leaf: bool
    # Under General's "Clothing, Shoes & Accessories". A baby's books or a scout's mug are not.
    apparel: bool


@lru_cache(maxsize=65536)
def _match_key(path: str) -> str:
    return " > ".join(" ".join(words(step)) for step in segments(path))


@lru_cache(maxsize=65536)
def _parsed(path: str) -> _Path:
    steps = segments(path)
    while steps and not _alternatives(steps[-1]):
        steps.pop()
    # The item's own step, then the one right above it when that names an item too.
    named = [step for step in steps[-2:] if _alternatives(step)] if steps else []
    last = segments(path)[-1] if segments(path) else ""
    return _Path(
        key=_match_key(path),
        department=department(path),
        words=frozenset(words(path)),
        steps=tuple(reversed(named)) if named and named[-1] == steps[-1] else (),
        generic_leaf=not _alternatives(last),
        apparel=bool(segments(path)) and segments(path)[0].casefold() == _APPAREL,
    )


def _rank(parsed: list[tuple[dict, _Path]], want: _Path, steps: tuple[str, ...], hint: frozenset[str],
          dept: str, *, loose: bool = False) -> list[tuple[tuple, tuple[int, int], int, dict]]:
    """The rows naming the first of `steps` that any row names, best first, as (score, item order, row order, row).

    A step only a qualifier matches ("Girls swimwear" for "Swim Diapers") gives
    way to a later step a row names outright ("Diapering").

    `dept` is the department to match in: the category's own, or a nearby one
    when the closest match looks further. `loose` also accepts a row that adds
    a qualifier neither the category nor the listing says.
    """
    weak_only: list[tuple[tuple, tuple[int, int], int, dict]] = []
    for step in steps:
        items = _alternatives(step)
        nouns = {noun for noun, _ in items}
        order = [noun for noun, _ in items]
        qualifiers = frozenset().union(*(found for _, found in items))
        ranked = []
        for index, (row, found) in enumerate(parsed):
            if not found.steps:
                continue
            if dept and found.department and found.department != dept:
                continue
            if not dept and found.department:
                continue
            if (found.words & _NICHE) - want.words - hint:
                continue
            if found.words & _NICHE_BRANCH and not found.words & _NICHE_BRANCH & (want.words | hint):
                continue
            same_department = bool(dept) and found.department == dept
            said = want.words | hint | (_suffix(found.steps[0]) if loose else frozenset())
            row_items = [(noun, quals) for noun, quals in _alternatives(found.steps[0]) if quals <= said]
            strong = [(noun, quals) for noun, quals in row_items if noun in nouns]
            # "Button-front" for "Button-Down Shirts", only inside the same department.
            weak = [(noun, quals) for noun, quals in row_items
                    if same_department and dept == want.department and not loose and noun in qualifiers and noun not in nouns]
            if not strong and not weak:
                continue
            # Qualifiers the row says anywhere: Video in "Video games & consoles > Games".
            shared = len(found.words & qualifiers) if strong else 0
            context = same_department or bool((found.words - _GENERIC) & (want.words - nouns - _GENERIC))
            # Outside clothing the branch has to agree too: "Video Games" is not
            # "Puzzles and games", and a truck's lighting is not studio lighting.
            if not context and (not dept or (qualifiers and not shared)):
                continue
            # A department's clothing never lands in a household or beauty branch
            # unless that branch says so too: kids' sets are not Fragrance sets.
            if not context and dept and not found.department and want.apparel and found.words & _HOUSEHOLD:
                continue
            score = (
                int(bool(found.department) == bool(dept)),
                int(bool(strong)),
                len({noun for noun, _ in strong + weak}),
                shared,
                # The same name as written: "Athletic Shoes" over "Sneakers".
                int(_plain(found.steps[0]) == _plain(step)),
                len(want.words & found.words),
                len(found.words & hint),
                -len(found.words - want.words - hint - _GENERIC),
                -int(found.generic_leaf),
            )
            first = min((order.index(noun) for noun, _ in strong if noun in order), default=len(order))
            # A tie keeps the age group: a baby's item is "0-24 mos", not "Girls (4+)".
            first = (first, int(_baby(want.key) != _baby(found.key)))
            ranked.append((score, first, index, row))
        ranked.sort(key=lambda entry: (tuple(-value for value in entry[0]), entry[1], entry[2]))
        if any(entry[0][1] for entry in ranked):
            return ranked
        weak_only = weak_only or ranked
    return weak_only


def match_row(rows: list[dict], path: str, evidence: str = "") -> dict | None:
    """The one row in `rows` ({"id", "path"}) that names the item `path` names, else None.

    `evidence` is the listing's own words, such as its title and sleeve length.
    They break a tie between rows that differ only by a qualifier.
    """
    if not _text(path):
        return None
    # Most synced eBay paths match exactly. Do not build the expensive ranking
    # descriptors for every leaf unless the normalized key is absent or ambiguous.
    key = _match_key(_text(path))
    exact = [row for row in rows if _match_key(str(row.get("path") or "")) == key]
    if len(exact) == 1:
        return exact[0]
    want = _parsed(_text(path))
    parsed = [(row, _parsed(str(row.get("path") or ""))) for row in rows]
    # The item itself first, then the step above it ("Shirts" for "T-Shirts").
    ranked = _rank(parsed, want, want.steps, frozenset(words(evidence)), want.department)
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None
    return ranked[0][3]


# Words that place a department-free category: a bridesmaid dress is women's.
_INFERRED = {
    "bride": "women", "bridal": "women", "bridesmaid": "women", "maternity": "women", "lingerie": "women",
    "groom": "men", "groomsman": "men", "child": "kids", "children": "kids", "youth": "kids", "infant": "kids",
}
# A General branch's word for the marketplace's: "Activewear" is clothing.
_INFERRED_BRANCH = {"activewear": "clothing", "apparel": "clothing"}
# Beauty branches inside a department. Clothing never falls back into them.
_BEAUTY = {"makeup", "skincare", "skin", "hair", "bath", "body", "fragrance", "beauty", "grooming"}


def _own_department(path: str, want: _Path) -> str:
    if want.department:
        return want.department
    found = [_INFERRED[word] for word in words(path) if word in _INFERRED]
    return found[0] if len(set(found)) == 1 else ""


def _nearby(path: str, own: str) -> tuple[str, ...]:
    """Departments to borrow an item from when the category's own has none.

    A girl's item is the women's one and a boy's the men's. Grown-ups' clothing
    stays in its own department; only an accessory, such as a tie or an
    umbrella, may borrow from the other. An item with no department is unisex
    and may be either.
    """
    said = frozenset(words(" > ".join(segments(path)[1:])))
    if own == "kids":
        gender = said & {"girl", "boy"}
        return ("women",) if gender == {"girl"} else ("men",) if gender == {"boy"} else ()
    if own and "accessory" in said:
        return ("men",) if own == "women" else ("women",)
    if not own:
        return ("men", "women")
    return ()


def _in(parsed: list[tuple[dict, _Path]], dept: str) -> list[tuple[dict, _Path]]:
    """Clothing rows under that department's own top level: "Women > ...", never "Sports > Footwear > Men" or Skincare."""
    found = []
    for row, info in parsed:
        parts = segments(row.get("path"))
        if info.department != dept or department(parts[0]) != dept:
            continue
        if len(parts) > 2 and set(words(parts[1])) & _BEAUTY:
            continue
        found.append((row, info))
    return found


def _best(parsed, want, steps, hint, departments, *, loose: bool) -> dict | None:
    """The best-scoring row across `departments`; a tie goes to the department listed first."""
    found = []
    for order, dept in enumerate(departments):
        ranked = _rank(_in(parsed, dept), want, steps, hint, dept, loose=loose)
        if ranked:
            found.append((tuple(-value for value in ranked[0][0]), order, ranked[0][3]))
    return min(found, key=lambda entry: entry[:2])[2] if found else None


def closest_row(rows: list[dict], path: str, evidence: str = "") -> tuple[dict, bool] | None:
    """The row `match_row` finds, else the nearest sensible option. The flag is True for that nearest one.

    Only clothing, shoes and accessories look further than an exact match, and
    only inside apparel departments, in this order: a tie goes to the first item
    General names; a department-free category takes the one its words imply
    (bridal is women's); the same item in the nearest department ("Girls' Hats"
    in Women when kids have none); an option that adds a qualifier the category
    does not say ("Tees - Short Sleeve" for a T-shirt); then the generic
    leaf of the branch above the item, then the department's own Other.
    """
    hit = match_row(rows, path, evidence)
    if hit:
        return hit, False
    text = _text(path)
    if not text:
        return None
    want = _parsed(text)
    hint = frozenset(words(evidence))
    parsed = [(row, _parsed(str(row.get("path") or ""))) for row in rows]
    # "Other Clothing, Shoes & Accessories" names no item; General's root is not one.
    steps = tuple(step for step in want.steps
                  if step != segments(text)[0] and not step.casefold().startswith(("other ", "mixed ")))
    ranked = _rank(parsed, want, steps, hint, want.department)
    if ranked:
        return ranked[0][3], True
    if not want.apparel:
        return None
    own = _own_department(text, want)
    # Another department lends the item itself, never its branch: "Work Clothing" is not work boots.
    tiers = (((own,), steps) if own else ((), ()), (_nearby(text, own), steps[:1]))
    for loose in (False, True):
        for departments, named in tiers:
            if found := _best(parsed, want, named, hint, departments, loose=loose):
                return found, True
    if not own:
        return None
    options = _in(parsed, own)
    if found := _branch(options, text):
        return found, True
    other = [row for row, info in options if info.generic_leaf and len(segments(row.get("path"))) == 2
             and not (info.words & _NICHE) - want.words]
    return (other[0], True) if other else None


def _branch(options: list[tuple[dict, _Path]], path: str) -> dict | None:
    """The generic leaf ("Other", "None") of the branch nearest the item: "Accessories > None" for "Ties"."""
    want_words = frozenset(words(path))
    for step in reversed(segments(path)[1:]):
        names = frozenset(words(step)) - frozenset(_DEPARTMENTS) - _STOP
        names = frozenset(_INFERRED_BRANCH.get(name, name) for name in names)
        if not names:
            continue
        for row, found in options:
            parts = segments(row.get("path"))
            if not found.generic_leaf or len(parts) < 3 or (found.words & _NICHE) - want_words:
                continue
            branch = frozenset(words(parts[-2])) - frozenset(_DEPARTMENTS)
            if branch and branch <= names:
                return row
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def evidence(listing: dict) -> str:
    """The listing's own words that can settle a tie: its title and sleeve length."""
    ebay = listing.get("ebay_specifics")
    if not isinstance(ebay, dict):
        ebay = {}
    return " ".join(_text(value) for value in (
        listing.get("title"), listing.get("sleeveLength"), ebay.get("sleeveLength"),
    ) if _text(value))


def native_rows() -> dict[str, list[dict]]:
    """Each direct form's own category options as {"id", "path"}, read from the files the forms read."""
    from vendoo_studio.services import depop_form, mercari_form, poshmark_form

    poshmark = next(row for row in poshmark_form.catalog()["fields"] if row["path"] == "direct_category_ids.poshmark")
    depop = depop_form.catalog()
    retired = set(depop.get("retired_category_ids") or [])
    mercari = mercari_form.categories()
    parents = {row["path"].rsplit(" > ", 1)[0] for row in mercari if " > " in row["path"]}
    return {
        "poshmark": [{"id": value, "path": label} for label, value in poshmark["option_values"].items()],
        "depop": [{"id": row["id"], "path": row["path"]} for row in depop["categories"] if row["id"] not in retired],
        "mercari": [dict(row) for row in mercari if row["path"] not in parents],
    }
