"""Whether Mercari's prepaid Shipping label is answered: one rule for every check.

List sends, in order: the listing's own label id, the label the prepared form
already resolved (``form.specifics.shipping.carrierId``, set once Chrome has
read this package's labels), or the seller's saved carrier
(``form.mercari_shipping_label``). The saved carrier is a preference, not a
label Mercari has offered for this package: Mercari offers each carrier under a
different id per weight tier, and only for the tiers that carry the package.
It counts as the answer only where this package's labels are known; elsewhere
it is said as what List will try, never as a verified choice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PATH = "mercari_specifics.shippingClassId"
# The label List picks when the listing names none and the saved carrier is not
# offered (``mercariShippingListing`` in the extension; ``mercari_form.form``).
AUTOMATIC = "USPS Ground Advantage"


@dataclass(frozen=True)
class LabelAnswer:
    """Where the label List would send comes from, and its id or carrier name."""

    source: Literal["listing", "form", "default", "none"]
    value: str


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _label_id(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    # Mercari's 0 means "ship on your own", never a prepaid label.
    return "" if text == "0" else text


def needs_label(snapshot: dict) -> bool:
    """Prepaid label shipping asks for a label; shipping on your own does not."""
    specifics = _dict(snapshot.get("mercari_specifics"))
    return str(specifics.get("shippingMethod") or "MERCARI").upper() != "SOYO"


def label_answer(snapshot: dict) -> LabelAnswer:
    specifics = _dict(snapshot.get("mercari_specifics"))
    own = _label_id(specifics.get(PATH.rsplit(".", 1)[-1]))
    if own:
        return LabelAnswer("listing", own)
    form = _dict(snapshot.get("form"))
    resolved = _label_id(_dict(_dict(form.get("specifics")).get("shipping")).get("carrierId"))
    if resolved:
        return LabelAnswer("form", resolved)
    # Trimmed, as the extension matches it against Mercari's carrier names.
    saved = str(_dict(form.get("mercari_shipping_label")).get("label") or "").strip()
    if saved:
        return LabelAnswer("default", saved)
    return LabelAnswer("none", "")


def unverified_default_message(label: str) -> str:
    """Said beside List while this package's labels have not been read."""
    return (f"Shipping label: List uses your saved label, {label}, if Mercari offers it for this package. "
            "Mercari's labels are checked when you list.")


def automatic_instead_message(label: str) -> str:
    return (f"Shipping label: Your saved label, {label}, isn't offered for this package. "
            f"List uses {AUTOMATIC} unless you choose another label.")


def unavailable_default_message(label: str) -> str:
    return f"Shipping label: Your saved label, {label}, isn't offered for this package. Choose one of Mercari's labels."


def offered_id(row: dict, label: str) -> str:
    """The id this form offers the saved carrier under, or "" when it does not offer it."""
    choices = row.get("option_values")
    if isinstance(choices, dict):
        return str(choices.get(label) or "").strip()
    return ""
