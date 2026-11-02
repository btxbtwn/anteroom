import asyncio
import threading
from contextvars import ContextVar

from vendoo_studio.services import listing_generate


def test_validation_repair_keeps_schema_work_off_the_request_loop(monkeypatch):
    seller = ContextVar('repair_seller', default=None)
    request_thread = threading.get_ident()
    work = []

    def record_work(stage):
        assert threading.get_ident() != request_thread, f'{stage} blocked the request loop'
        assert seller.get() == 'alice'
        work.append(stage)

    class NativeOptions(list):
        def __deepcopy__(self, memo):
            record_work('copy native options')
            return list(self)

    listing = {'title': '', 'native_options': NativeOptions(['Small', 'Large']), 'evidence': []}

    def validate(snapshot):
        record_work('validate')
        snapshot['evidence'].append('validation mutation')
        return [{'field': 'title', 'message': 'A title is required'}] if not snapshot['title'] else []

    async def reply(*args, **kwargs):
        return '[{"op":"replace","path":"/title","value":"Fixed title"}]'

    monkeypatch.setattr(listing_generate, '_validation_blockers', validate)
    monkeypatch.setattr(listing_generate, 'collect_provider_text', reply)

    async def run():
        token = seller.set('alice')
        try:
            return await listing_generate.fill_validation_gaps(object(), listing)
        finally:
            seller.reset(token)

    updated = asyncio.run(run())
    assert updated['title'] == 'Fixed title'
    assert listing['title'] == ''
    assert listing['evidence'] == updated['evidence'] == []
    assert updated['native_options'] == ['Small', 'Large']
    assert work.count('validate') == 2
    assert work.count('copy native options') == 2
