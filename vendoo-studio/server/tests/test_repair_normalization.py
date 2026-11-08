import asyncio

from vendoo_studio.services import listing_generate


def test_full_mens_category_path_normalizes_size_without_a_department_field():
    listing = {"size": "34x32", "title": "Classic Blue Denim Jeans",
               "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Jeans"}
    assert listing_generate.normalize_mens_bottoms_size(listing)
    assert listing["size"] == "34"
    assert listing["ebay_specifics"]["inseam"] == "32"


def test_validation_repair_cannot_reintroduce_a_waist_inseam_size(monkeypatch):
    class Provider:
        async def structured_chat(self, messages, schema):
            yield '{"patches":[{"op":"replace","path":"/size","value":"34x32"}]}'

    monkeypatch.setattr(listing_generate, "_validation_blockers", lambda listing: (
        [{"field": "size", "message": "Choose the waist size"}] if listing["size"] != "34" else []
    ))
    listing = {"size": "", "title": "Classic Blue Denim Jeans", "department": "Men",
               "category_path": "Men > Clothing > Jeans", "ebay_specifics": {"department": "Men"}}
    result = asyncio.run(listing_generate.fill_validation_gaps(Provider(), listing))
    assert result is not None
    assert result["size"] == "34"
    assert result["ebay_specifics"] == listing["ebay_specifics"]
