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
