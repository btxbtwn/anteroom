"""Offline, deterministic scoring of listing outputs against seller-labelled cases."""
from __future__ import annotations

import copy

from vendoo_studio.models.validation import _description_follows_formula, _title_follows_formula, validate_listing
from vendoo_studio.services.seller_overrides import get_path


def evaluate_outputs(cases: list[dict], outputs: dict[str, dict]) -> dict:
    """Score every case, counting missing outputs as failures rather than dropping them."""
    from vendoo_studio.services.listing_generate import strip_pricing_from_description, strip_uncertainty_from_description

    results = []
    totals: dict[str, list[bool]] = {}
    for case in cases:
        listing = outputs.get(case["id"])
        checks: dict[str, list[bool]] = {"output_present": [isinstance(listing, dict)]}
        value = listing if isinstance(listing, dict) else {}
        checks["fact_accuracy"] = [
            get_path(value, field) == expected for field, expected in case.get("expected_fields", {}).items()
        ]
        checks["unsupported_facts_omitted"] = [
            get_path(value, field) in (None, "", [], {}) for field in case.get("unsupported_fields", [])
        ]
        checks["seller_details_preserved"] = [
            text in str(value.get("description") or "") for text in case.get("description_contains", [])
        ]
        checks["allowed_options"] = []
        for field, allowed in case.get("allowed_options", {}).items():
            selected = get_path(value, field)
            values = selected if isinstance(selected, list) else [selected]
            checks["allowed_options"].append(bool(values) and all(option in allowed for option in values))
        checks["buyer_copy"] = [
            not strip_pricing_from_description(copy.deepcopy(value)),
            not strip_uncertainty_from_description(copy.deepcopy(value)),
        ]
        if case.get("physical", True):
            checks["formula"] = [
                _description_follows_formula(str(value.get("description") or "")),
                _title_follows_formula(str(value.get("title") or ""),
                                       str(value.get("brand") or ""), str(value.get("size") or "")),
            ]
        # Completeness is reported separately: truthful unknowns are preferable
        # to fabricated product facts even though the draft remains incomplete.
        completeness = validate_listing(copy.deepcopy(value), require_photos=False,
                                        selected_marketplaces=case.get("marketplaces", [])).valid
        for metric, scores in checks.items():
            totals.setdefault(metric, []).extend(score and checks["output_present"][0] for score in scores)
        results.append({"id": case["id"], "passed": all(all(scores) for scores in checks.values()),
                        "complete": completeness, "checks": checks})
    return {
        "cases": results,
        "metrics": {metric: {"passed": sum(scores), "total": len(scores),
                             "rate": round(sum(scores) / len(scores), 4) if scores else None}
                    for metric, scores in totals.items()},
        "complete_drafts": sum(result["complete"] for result in results),
        "passed": bool(results) and all(result["passed"] for result in results),
    }
