from vendoo_studio.services.answer_links import LinkGuard


def test_listing_link_without_its_prefix_is_given_one():
    # The model sometimes links a listing as `#<id>`; the apps open only `#listing-<id>`.
    text = "| [Jurassic World Tee](#0a398113547b) | poshmark |\n[Fiore tank](listing-674fb62ea1fb)"
    assert LinkGuard().clean(text) == (
        "| [Jurassic World Tee](#listing-0a398113547b) | poshmark |\n[Fiore tank](#listing-674fb62ea1fb)"
    )


def test_listing_link_in_the_right_form_is_left_alone():
    text = "[Chore Coat](#listing-0a398113547b) and [section](#notes)"
    assert LinkGuard().clean(text) == text
