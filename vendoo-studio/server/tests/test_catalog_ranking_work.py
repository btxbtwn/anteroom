from vendoo_studio.services import catalog_index as catalog


def test_concurrent_searches_reuse_category_word_normalization(monkeypatch):
    # Several cold-index searches score the same shared taxonomy. Repeating
    # regex tokenization for each draft occupied worker connections for seconds.
    findall = catalog.re.findall
    tokenizations = []

    def count(pattern, value, *args, **kwargs):
        if value.startswith('performance sentinel'):
            tokenizations.append(value)
        return findall(pattern, value, *args, **kwargs)

    monkeypatch.setattr(catalog.re, 'findall', count)
    # One large marketplace has over 18,000 leaves. A cache smaller than one
    # traversal churns on every new search and does not remove the repeated work.
    paths = [f'Performance sentinel {number} > Men > Shirts' for number in range(20000)]
    for tokens, expected in [(['men', 'shirt'], 2), (['mens', 'shirts'], 2), (['women', 'shirts'], 1)]:
        assert [catalog._whole_word_score(path, tokens) for path in paths] == [expected] * len(paths)
    assert len(tokenizations) == len(paths), 'Independent searches must not tokenize every category path again'


def test_shared_word_normalization_preserves_inflection_casefold_and_whole_words():
    for path, tokens, expected in [
        ('Clothing > Categories > Dresses', ['category', 'dress'], 2),
        ('Clothing > Straße', ['strasse'], 1),
        ('Clothing > MEN > SHIRTS', ['men', 'shirt'], 2),
        ('Toys > Teepees', ['tee'], 0),
        ('Clothing > Shirts', ['shirt', 'shirts'], 1),
    ]:
        assert catalog._whole_word_score(path, tokens) == expected
