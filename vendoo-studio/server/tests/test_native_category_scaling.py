from vendoo_studio.services.native_field_fill import category_rows


def test_exact_native_category_match_does_not_build_all_ranking_descriptors(monkeypatch):
    from vendoo_studio.services import native_category_match as match

    target = {'id': 'tee', 'path': 'Men > Tops > T-Shirts'}
    rows = [{'id': str(number), 'path': f'Men > Other > Item {number}'} for number in range(1000)]
    rows.append(target)
    parsed = []
    original = match._parsed

    def record(path):
        parsed.append(path)
        return original(path)

    monkeypatch.setattr(match, '_parsed', record)
    assert match.match_row(rows, 'MEN > TOPS > T-Shirts') is target
    assert parsed == [], 'An exact category must not build ranking descriptors for the entire marketplace'


def test_duplicate_normalized_native_categories_still_require_a_choice():
    from vendoo_studio.services.native_category_match import match_row

    assert match_row([
        {'id': 'one', 'path': 'Men > Tops > T-Shirts'},
        {'id': 'two', 'path': 'MEN > TOPS > T-SHIRTS'},
    ], 'Men > Tops > T-Shirts') is None


def test_large_category_menu_does_not_compare_every_pair():
    comparisons = 0

    class Label(str):
        def startswith(self, *args):
            nonlocal comparisons
            comparisons += 1
            return super().startswith(*args)

    options = [Label(f'Clothing > Branch {index} > Leaf') for index in range(1000)]
    options.insert(0, Label('Clothing'))
    options.insert(1, Label('Clothing > Branch 0'))
    form = {'fields': [{'path': 'direct_category_ids.mercari', 'options': options}]}

    rows = category_rows(form, {})

    assert rows[0]['options'] == options[2:]
    assert comparisons < 100 * len(options), 'Category preparation compared every pair of options'
