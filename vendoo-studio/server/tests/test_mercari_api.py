from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from vendoo_studio.services import mercari_api


class MercariApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_quotes_use_an_ephemeral_guest_and_only_read_shipping_choices(self):
        paths = []

        def respond(request):
            paths.append((request.method, request.url.path))
            if request.url.path == '/v1/token':
                self.assertNotIn('authorization', request.headers)
                return httpx.Response(200, content=mercari_api.MESSAGES['Guest'](
                    token={'accessToken': 'guest-for-test'}).SerializeToString())
            self.assertEqual(request.headers['authorization'], 'Bearer guest-for-test')
            data = mercari_api.MESSAGES['Request'].FromString(request.content)
            self.assertEqual(data.categoryId, 275)
            self.assertEqual(data.selectedWeight.unit, 1)
            self.assertEqual(data.selectedWeight.weight, 16)
            self.assertEqual(data.selectedPackageDimension.length, 14)
            return httpx.Response(200, content=mercari_api.MESSAGES['Response'](shippingClasses=[{
                'id': 2552, 'fee': 799, 'weightRange': {'min': 13, 'max': 16}}]).SerializeToString())

        client = httpx.AsyncClient
        with patch.object(mercari_api.httpx, 'AsyncClient',
                          side_effect=lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)):
            result = await mercari_api.shipping_quotes(275, 16, (14, 10, 5))
        self.assertEqual(paths, [('GET', '/v1/token'), ('POST', '/v1/shipping/available_shipping_classes')])
        self.assertEqual(result[0]['fee'], 799)
        self.assertNotIn('guest-for-test', str(result))

    async def test_missing_guest_token_never_sends_a_quote(self):
        paths = []

        def respond(request):
            paths.append(request.url.path)
            return httpx.Response(200, content=b'')

        client = httpx.AsyncClient
        with patch.object(mercari_api.httpx, 'AsyncClient',
                          side_effect=lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)):
            with self.assertRaises(ValueError):
                await mercari_api.shipping_quotes(275, 16, (14, 10, 5))
        self.assertEqual(paths, ['/v1/token'])
