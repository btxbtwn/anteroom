'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const {
  executePoshmarkOperation,
  isAllowedHost,
  isPoshmarkCookieHost,
  assertAllowedUrl,
  buildCookieJar,
  createPoshmarkFetch,
} = require('./run-poshmark.cjs');

function makeJwt(claims) {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
  return `${header}.${payload}.sig`;
}

function makePoshmarkCookies(userId, extra = []) {
  const jwt = makeJwt({
    user_id: userId,
    username: `seller_${userId.slice(-4)}`,
    exp: Math.floor(Date.now() / 1000) + 3600 * 24,
  });
  return [
    {
      name: 'jwt',
      value: jwt,
      domain: '.poshmark.com',
      path: '/',
      secure: true,
      httpOnly: true,
      expirationDate: Math.floor(Date.now() / 1000) + 3600 * 24,
    },
    {
      name: '_web_session',
      value: `sess_${userId}`,
      domain: '.poshmark.com',
      path: '/',
      secure: true,
      httpOnly: true,
    },
    ...extra,
  ];
}

function mockResponse({ status = 200, body = {}, headers = {} }) {
  const h = new Headers({ 'content-type': 'application/json', ...headers });
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    url: '',
    redirected: false,
    headers: h,
    text: async () => text,
    json: async () => JSON.parse(text),
    arrayBuffer: async () => Buffer.from(text),
    blob: async () => new Blob([text]),
    body: null,
  };
}

test('host allowlist and cookie hygiene', async () => {
  assert.equal(isPoshmarkCookieHost('poshmark.com'), true);
  assert.equal(isPoshmarkCookieHost('www.poshmark.com'), true);
  assert.equal(isPoshmarkCookieHost('evil-poshmark.com'), false);
  assert.equal(isAllowedHost('poshmark.com'), true);
  assert.equal(isAllowedHost('d2h1pu99sxkfvn.cloudfront.net'), true);
  assert.equal(isAllowedHost('poshmark-uploads.s3.amazonaws.com'), true);
  assert.equal(isAllowedHost('127.0.0.1'), false);
  assert.equal(isAllowedHost('evil.example.com'), false);

  assert.throws(() => assertAllowedUrl('http://127.0.0.1:4318/api/jobs/1/photos/2'));
  assert.throws(() => assertAllowedUrl('https://evil.example.com/steal'));
  assert.throws(() => assertAllowedUrl('https://poshmark.com:8443/vm-rest/posts'));

  const jar = buildCookieJar(makePoshmarkCookies('aaaaaaaaaaaaaaaaaaaaaaaa'));
  const seen = [];
  const fetchFn = createPoshmarkFetch({
    jar,
    userAgent: 'TestChrome/131',
    httpFetch: async (url, init) => {
      seen.push({ url, headers: init.headers });
      return mockResponse({
        body: { ok: true },
        headers: url.startsWith('https://poshmark.com')
          ? { 'set-cookie': '_web_session=rotated_sess; Domain=.poshmark.com; Path=/; Secure; HttpOnly' }
          : { 'set-cookie': 's3_trap=1; Path=/' },
      });
    },
  });

  // Poshmark request gets Cookie, Origin, Referer, User-Agent
  await fetchFn('https://poshmark.com/vm-rest/posts/123', { method: 'GET' });
  assert.equal(seen[0].headers['User-Agent'], 'TestChrome/131');
  assert.equal(seen[0].headers.Origin, 'https://poshmark.com');
  assert.equal(seen[0].headers.Referer, 'https://poshmark.com/create-listing');
  assert.match(seen[0].headers.Cookie, /jwt=/);
  assert.match(seen[0].headers.Cookie, /_web_session=sess_aaaa/);

  // S3 upload NEVER gets Cookie, Origin, or Referer
  await fetchFn('https://poshmark-uploads.s3.amazonaws.com/photo.jpg', { method: 'PUT', body: 'img' });
  assert.equal(seen[1].headers.Cookie, undefined);
  assert.equal(seen[1].headers.Origin, undefined);
  assert.equal(seen[1].headers.Referer, undefined);

  // Subsequent Poshmark call uses rotated cookie from Set-Cookie
  await fetchFn('https://poshmark.com/vm-rest/posts/456', { method: 'GET' });
  assert.match(seen[2].headers.Cookie, /_web_session=rotated_sess/);
  assert.doesNotMatch(seen[2].headers.Cookie, /s3_trap/);
});

test('confirmation gates refuse unconfirmed mutations with zero network calls', async () => {
  let called = 0;
  const httpFetch = async () => {
    called += 1;
    return mockResponse({ body: {} });
  };
  const cookies = makePoshmarkCookies('bbbbbbbbbbbbbbbbbbbbbbbb');

  for (const command of [
    { type: 'marketplace.execute', job_id: 'j1', payload: { marketplace: 'poshmark', action: 'list', operation_id: 'op-1' } },
    { type: 'marketplace.execute', job_id: 'j2', payload: { marketplace: 'poshmark', action: 'update', listing_id: 'cccccccccccccccccccccccc', operation_id: 'op-2' } },
    { type: 'marketplace.delete', job_id: 'j3', payload: { marketplace: 'poshmark', listing_id: 'cccccccccccccccccccccccc' } },
    { type: 'marketplace.promote', job_id: 'j4', payload: { marketplace: 'poshmark', action: 'share', listing_id: 'cccccccccccccccccccccccc', action_id: 'act-123456' } },
  ]) {
    const out = await executePoshmarkOperation({ command, cookies, httpFetch });
    assert.equal(out.result.ok, false);
    assert.equal(out.result.safe_to_retry, true);
    assert.equal(out.result.error_code, 'rejected');
  }
  assert.equal(called, 0);
});

test('seller isolation and Set-Cookie persistence across operations', async () => {
  const sellerA = '111111111111111111111111';
  const sellerB = '222222222222222222222222';
  const cookiesSeen = [];

  const httpFetch = async (url, init) => {
    cookiesSeen.push({ url, cookie: init.headers.Cookie || '' });
    if (url.includes('/create-listing')) {
      const uid = init.headers.Cookie.includes(sellerA) ? sellerA : sellerB;
      return mockResponse({
        headers: {
          'content-type': 'text/html',
          'set-cookie': `_web_session=refreshed_${uid}; Domain=.poshmark.com; Path=/; Secure; HttpOnly`,
        },
        body: `<script>window.__INITIAL_STATE__ = ${JSON.stringify({
          csrftoken: `csrf_${uid}`,
          userCookies: { userInfo: { uid } },
        })};</script>`,
      });
    }
    return mockResponse({ body: { data: {} } });
  };

  const [resA, resB] = await Promise.all([
    executePoshmarkOperation({
      command: { type: 'marketplace.verify_session', payload: { marketplace: 'poshmark' } },
      cookies: makePoshmarkCookies(sellerA),
      httpFetch,
    }),
    executePoshmarkOperation({
      command: { type: 'marketplace.verify_session', payload: { marketplace: 'poshmark' } },
      cookies: makePoshmarkCookies(sellerB),
      httpFetch,
    }),
  ]);

  assert.equal(resA.result.ok, true);
  assert.equal(resA.result.session.state, 'signed_in');
  assert.equal(resA.cookies_changed, true);
  assert.equal(resA.cookies.find(c => c.name === '_web_session').value, `refreshed_${sellerA}`);

  assert.equal(resB.result.ok, true);
  assert.equal(resB.result.session.state, 'signed_in');
  assert.equal(resB.cookies_changed, true);
  assert.equal(resB.cookies.find(c => c.name === '_web_session').value, `refreshed_${sellerB}`);
});

test('write-ahead journal failure blocks Poshmark mutation cleanly', async () => {
  let networkCalls = 0;
  const out = await executePoshmarkOperation({
    command: {
      type: 'marketplace.execute',
      job_id: 'job-wa-1',
      payload: {
        marketplace: 'poshmark',
        action: 'save_draft',
        operation_id: 'abcdef12-3456-7890-abcd-ef1234567890',
        listing: { title: 'Test Jacket', price: 25 },
      },
    },
    cookies: makePoshmarkCookies('333333333333333333333333'),
    httpFetch: async () => {
      networkCalls += 1;
      return mockResponse({ body: {} });
    },
    onJournalSet: async () => {
      throw new Error('Postgres journal write failed');
    },
  });

  assert.equal(out.result.ok, false);
  assert.equal(out.result.safe_to_retry, true);
  assert.equal(networkCalls, 0);
});

test('CLI subprocess runs marketplace.sessions over stdin/stdout NDJSON', async () => {
  const runnerPath = path.join(__dirname, 'run-poshmark.cjs');
  const child = spawn(process.execPath, [runnerPath], { stdio: ['pipe', 'pipe', 'pipe'] });

  const stdoutLines = [];
  let stderr = '';
  child.stdout.on('data', chunk => {
    for (const line of chunk.toString('utf8').split('\n')) {
      if (line.trim()) stdoutLines.push(JSON.parse(line));
    }
  });
  child.stderr.on('data', chunk => {
    stderr += chunk.toString('utf8');
  });

  const payload = {
    type: 'run',
    command: { type: 'marketplace.sessions', payload: { marketplace: 'poshmark' } },
    cookies: makePoshmarkCookies('444444444444444444444444'),
    journals: {},
  };
  child.stdin.write(`${JSON.stringify(payload)}\n`);

  const exitCode = await new Promise(resolve => child.on('close', resolve));
  assert.equal(exitCode, 0, `stderr was: ${stderr}`);
  assert.equal(stdoutLines.length, 1);
  assert.equal(stdoutLines[0].type, 'result');
  assert.equal(stdoutLines[0].result.ok, true);
  assert.equal(stdoutLines[0].result.sessions.poshmark.state, 'signed_in');
  assert.equal(stdoutLines[0].result.sessions.poshmark.label, 'seller_4444');
});
