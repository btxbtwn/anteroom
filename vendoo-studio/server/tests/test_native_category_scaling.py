from vendoo_studio.services.native_field_fill import category_rows


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
