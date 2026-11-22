import pytest

from anteroom.services.size_audit import aligned, size_problems


def _listing(size):
    # Titled as a save would title it, so only the photo size can stand out.
    return aligned({"title": "Sag Harbor Classic Blouse Red", "size": size})


@pytest.mark.parametrize("general, tag", [
    ("L", "Large (source: tag)"),
    ("L", "LARGE (12/14) (source: tag)"),
    ("M", "M (8-10) (source: tag)"),
    ("XL", "X-LARGE (source: tag)"),
    ("2XL", "2X (source: tag)"),
    ("2XL", "XXL (source: tag)"),
    ("2XS", "XXS (source: tag)"),
    ("3XL", "3X (source: tag)"),
    ("3XL", "3X-Large (source: tag)"),
    ("XS", "Extra Small (source: tag)"),
    ("L", "Unknown (source: unreadable)"),
])
def test_the_same_size_written_another_way_is_not_a_problem(general, tag):
    assert size_problems(_listing(general), tag) is None


@pytest.mark.parametrize("general, tag", [
    ("M", "36 (source: tag)"),
    ("L", "Petite (source: tag)"),
    ("XL", "XXL (source: tag)"),
    ("XS", "S (source: tag)"),
])
def test_a_different_size_on_the_tag_is_flagged(general, tag):
    assert size_problems(_listing(general), tag) == "the photos read a different size"


def test_aligned_does_not_mutate_input_listing_or_specifics():
    original = {
        "title": "Levi's Vintage Jeans Blue",
        "brand": "Levi's",
        "department": "Men",
        "category_path": "Men > Jeans",
        "size": "34x32",
        "ebay_specifics": {"size": "34x32"},
        "poshmark_specifics": {"size": "34x32"},
        "depop_specifics": {"size": "34x32"},
        "mercari_specifics": {"size": "34x32"},
        "etsy_specifics": {"size": "34x32"},
    }
    snapshot = {
        key: dict(val) if isinstance(val, dict) else val
        for key, val in original.items()
    }
    updated = aligned(original)
    assert original == snapshot
    assert updated["size"] == "34"
    assert updated["ebay_specifics"]["inseam"] == "32"
    assert updated["ebay_specifics"]["size"] == "34"
    assert updated["poshmark_specifics"]["size"] == "Waist 34"


def test_prefetch_schema_failure_keeps_each_category_without_per_listing_reads(monkeypatch):
    from unittest.mock import Mock
    from anteroom.services import category_fields, listing_generate, size_audit

    db = Mock()
    listings = [{"marketplace_category_ids": {"ebay": cid}} for cid in ("one", "two")]
    monkeypatch.setattr(category_fields, "load_rows_many", Mock(side_effect=RuntimeError("read failed")))
    load_fields = Mock(side_effect=AssertionError("unexpected per-listing schema read"))
    monkeypatch.setattr(category_fields, "load_fields", load_fields)

    choices = size_audit.prefetch_ebay_size_choices(db, listings)
    assert choices == {"one": [], "two": []}
    db.rollback.assert_called_once()
    with size_audit.with_ebay_size_choices(choices):
        assert [listing_generate.ebay_size_choices(listing) for listing in listings] == [[], []]
    load_fields.assert_not_called()


def test_prefetch_schema_failure_recovers_database_session(monkeypatch):
    from sqlalchemy import text
    from anteroom.database import SessionLocal
    from anteroom.services import category_fields, size_audit

    def failed_query(_keys, *, db):
        db.execute(text("SELECT * FROM nonexistent_category_schema_test"))

    monkeypatch.setattr(category_fields, "load_rows_many", failed_query)
    with SessionLocal() as db:
        listings = [{"marketplace_category_ids": {"ebay": "one"}}]
        assert size_audit.prefetch_ebay_size_choices(db, listings) == {"one": []}
        assert db.execute(text("SELECT 1")).scalar_one() == 1
