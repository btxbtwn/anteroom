from fastapi.testclient import TestClient


def test_local_sessions_are_signed_and_sellers_cannot_read_each_others_photos():
    from vendoo_studio.hosted_sandbox import LocalSupabase
    local = LocalSupabase()
    with TestClient(local.app) as client:
        alice = client.post('/auth/v1/token?grant_type=password', json={'email': 'alice@sandbox.test', 'password': 'sandbox'})
        assert alice.status_code == 200
        body = alice.json()
        identity = local.verifier.verify(body['access_token'])
        assert identity.user_id == body['user']['id']
        bob = client.post('/auth/v1/token?grant_type=password', json={'email': 'bob@sandbox.test', 'password': 'sandbox'}).json()
        key = identity.user_id + '/draft/photo.jpg'
        headers = {'Authorization': 'Bearer ' + body['access_token']}
        assert client.post('/storage/v1/object/anteroom-photos/' + key, content=b'photo', headers=headers).status_code == 200
        assert client.get('/storage/v1/object/anteroom-photos/' + key, headers=headers).content == b'photo'
        assert client.get('/storage/v1/object/anteroom-photos/' + key, headers={'Authorization': 'Bearer ' + bob['access_token']}).status_code == 404
        assert client.get('/storage/v1/object/anteroom-photos/' + key).status_code == 401
        assert client.get('/storage/v1/object/anteroom-photos/' + key, headers={'apikey': local.server_key}).content == b'photo'
        assert client.get('/storage/v1/object/anteroom-photos/' + key, headers={'apikey': 'wrong'}).status_code == 401
        assert client.post('/auth/v1/token?grant_type=password', json={'email': 'real@example.com', 'password': 'sandbox'}).status_code == 400
        renewed = client.post('/auth/v1/token?grant_type=refresh_token', json={'refresh_token': body['refresh_token']})
        assert renewed.status_code == 200
        assert renewed.json()['user']['id'] == identity.user_id


def test_local_accounts_cover_sign_up_and_password_reset():
    from vendoo_studio.hosted_sandbox import LocalSupabase
    local = LocalSupabase()
    with TestClient(local.app) as client:
        created = client.post('/auth/v1/signup', json={'email': 'new@example.com', 'password': 'long enough'})
        assert created.status_code == 200 and 'access_token' not in created.json()
        taken = client.post('/auth/v1/signup', json={'email': 'alice@sandbox.test', 'password': 'long enough'})
        assert taken.json()['error_code'] == 'user_already_exists'
        wrong = client.post('/auth/v1/token?grant_type=password', json={'email': 'alice@sandbox.test', 'password': 'nope'})
        assert wrong.json()['error_code'] == 'invalid_credentials'
        assert client.post('/auth/v1/recover', json={'email': 'alice@sandbox.test'}).status_code == 200
        recovered = client.post('/auth/v1/token?grant_type=pkce', json={'auth_code': 'recovery-alice', 'code_verifier': 'x'})
        assert recovered.json()['user']['email'] == 'alice@sandbox.test'
        assert client.post('/auth/v1/token?grant_type=pkce', json={'auth_code': 'recovery-alice', 'code_verifier': 'x'}).status_code == 400
        headers = {'Authorization': 'Bearer ' + recovered.json()['access_token']}
        assert client.put('/auth/v1/user', json={'password': 'short'}, headers=headers).json()['error_code'] == 'weak_password'
        assert client.put('/auth/v1/user', json={'password': 'a new password'}, headers=headers).status_code == 200


def test_load_tests_can_sign_in_more_sellers(monkeypatch):
    import importlib
    from vendoo_studio import hosted_sandbox
    monkeypatch.setenv('ANTEROOM_SANDBOX_SELLERS', '4')
    try:
        reloaded = importlib.reload(hosted_sandbox)
        assert list(reloaded.ACCOUNTS) == ['alice@sandbox.test', 'bob@sandbox.test',
                                           'seller3@sandbox.test', 'seller4@sandbox.test']
        assert len(set(reloaded.ACCOUNTS.values())) == 4
    finally:
        monkeypatch.delenv('ANTEROOM_SANDBOX_SELLERS')
        importlib.reload(hosted_sandbox)
    assert list(hosted_sandbox.ACCOUNTS) == ['alice@sandbox.test', 'bob@sandbox.test']


def test_database_delay_proxy_adds_the_round_trip_each_way(tmp_path):
    import asyncio
    import socket
    import threading
    import time
    from vendoo_studio.hosted_sandbox import DelayedDatabase

    path = str(tmp_path / 'echo.sock')

    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(asyncio.start_unix_server(echo, path))
    threading.Thread(target=loop.run_forever, daemon=True).start()
    proxy = DelayedDatabase(path, 0.05)
    try:
        with socket.create_connection(('127.0.0.1', proxy.port), timeout=5) as client:
            for message in (b'one', b'two'):
                started = time.perf_counter()
                client.sendall(message)
                assert client.recv(1024) == message
                assert 0.1 <= time.perf_counter() - started < 1
    finally:
        proxy.stop()
        loop.call_soon_threadsafe(loop.stop)
