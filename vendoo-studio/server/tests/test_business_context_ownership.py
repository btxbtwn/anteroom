from datetime import date
from types import SimpleNamespace

from vendoo_studio.services import business_context


def test_same_database_and_day_never_reuse_another_sellers_business_context():
    engine = SimpleNamespace(url='postgresql://restricted-runtime')
    alice = SimpleNamespace(user_id='alice', get_bind=lambda: engine)
    bob = SimpleNamespace(user_id='bob', get_bind=lambda: engine)
    today = date(2026, 10, 30)
    business_context.invalidate_business_context()
    try:
        assert business_context.cached(alice, today, lambda: {'private sales': 'Alice'}) == {'private sales': 'Alice'}
        assert business_context.cached(bob, today, lambda: {'private sales': 'Bob'}) == {'private sales': 'Bob'}
        assert business_context.cached(bob, today, lambda: {'unexpected rebuild': True}) == {'private sales': 'Bob'}
        assert business_context.cached(alice, today, lambda: {'updated sales': 'Alice'}) == {'updated sales': 'Alice'}
    finally:
        business_context.invalidate_business_context()
