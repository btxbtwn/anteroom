const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const URL_ = 'https://webapi.depop.com/presentation/api/v1/drafts/1/';
const CODES = ['signed_out', 'challenge', 'rate_limited', 'missing_fields', 'rejected', 'schema_drift', 'network', 'uncertain'];

function worker(answers) {
  const c = loadWorker();
  const calls = [];
  const sleeps = [];
  c.marketplaceSleep = async ms => { sleeps.push(ms); };
  c.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const answer = answers[Math.min(calls.length - 1, answers.length - 1)];
    if (answer instanceof Error) throw answer;
    return answer;
  };
  const run = options => c.marketplaceFetch(URL_, { marketplace: 'depop', step: 'test', deadline: Date.now() + 60000, ...options });
  return { c, calls, sleeps, run };
}

const failure = async promise => { try { await promise; } catch (error) { return error; } assert.fail('expected a failure'); };

test('a transient read is retried with a pause and then succeeds', async () => {
  const { calls, sleeps, run } = worker([new TypeError('Failed to fetch'), reply(502, 'Bad gateway'), reply(200, { id: 1 })]);
  const { data } = await run();
  assert.equal(data.id, 1);
  assert.equal(calls.length, 3);
  assert.equal(sleeps.length, 2);
  assert.ok(sleeps.every(ms => ms > 0 && ms < 5000));
});

test('429 with Retry-After waits that long before retrying a read', async () => {
  const { calls, sleeps, run } = worker([reply(429, { message: 'Slow down' }, { 'retry-after': '3' }), reply(200, {})]);
  await run();
  assert.equal(calls.length, 2);
  assert.deepEqual(sleeps, [3000]);
});

test('a long Retry-After is reported, not waited out', async () => {
  const { calls, run } = worker([reply(429, { message: 'Slow down' }, { 'retry-after': '120' })]);
  const error = await failure(run());
  assert.equal(error.code, 'rate_limited');
  assert.equal(error.retry_after_s, 120);
  assert.equal(error.rejected, true);
  assert.equal(calls.length, 1);
});

test('creates and publishes are sent once, whatever happens', async () => {
  for (const answer of [new TypeError('Failed to fetch'), reply(503, 'Unavailable'), reply(429, {}, { 'retry-after': '1' }), reply(408, '')]) {
    const { calls, run } = worker([answer, reply(200, {})]);
    const error = await failure(run({ method: 'POST', body: '{}' }));
    assert.equal(calls.length, 1);
    if (answer instanceof Error || answer.status >= 500 || answer.status === 408) {
      assert.equal(error.code, 'uncertain');
      assert.equal(error.rejected, false);
    }
  }
});

test('bot checks are challenges and sign-in pages are signed out', async () => {
  for (const [answer, code] of [
    [reply(403, '<html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/h/b"></script></html>', { 'content-type': 'text/html' }), 'challenge'],
    [reply(503, '<div id="challenge-error-text">Enable JavaScript</div>', { 'content-type': 'text/html' }), 'challenge'],
    [reply(200, '<html><div id="px-captcha"></div></html>', { 'content-type': 'text/html' }), 'challenge'],
    [reply(200, '<html><form action="/login"><input type="password"></form></html>', { 'content-type': 'text/html' }), 'signed_out'],
    [reply(401, { message: 'Token expired' }), 'signed_out'],
    [reply(200, '<html>Something else</html>', { 'content-type': 'text/html' }), 'schema_drift'],
  ]) {
    const { calls, run } = worker([answer]);
    const error = await failure(run({ method: 'POST', body: '{}' }));
    assert.equal(error.code, code, String(answer.status));
    assert.equal(calls.length, 1);
  }
});

test('a normal page that loads bot-check scripts is not a challenge', async () => {
  const { run } = worker([reply(200, '<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>ok</html>', { 'content-type': 'text/html' })]);
  const { data } = await run({ expect: 'text' });
  assert.match(data, /ok/);
});

test('a 401 in the middle of an operation is a definite refusal with the marketplace\'s words', async () => {
  const { run } = worker([reply(401, { error: { message: 'Session expired for seller@example.com', token: 'x' } })]);
  const error = await failure(run({ method: 'PUT', body: '{}' }));
  assert.equal(error.code, 'signed_out');
  assert.equal(error.rejected, true);
  assert.equal(error.marketplace_message, 'Session expired for [email]');
});

test('marketplace words are scrubbed of tokens and capped', () => {
  const c = loadWorker();
  const text = c.marketplaceScrub(`Bad token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc session_id=abc123; Bearer secret-value ${'word '.repeat(200)}`);
  assert.ok(!/eyJ|abc123|secret-value/.test(text));
  assert.ok(text.length > 500);
  assert.ok(text.length <= 4000);
});

test('every failure maps to a contract error code, and unsafe is always uncertain', () => {
  const c = loadWorker();
  for (const code of CODES) {
    const safe = c.marketplaceFailure(c.marketplaceError('x', { code }), true);
    assert.ok(CODES.includes(safe.error_code));
    assert.equal(safe.safe_to_retry, true);
    assert.equal(c.marketplaceFailure(c.marketplaceError('x', { code }), false).error_code, 'uncertain');
  }
  assert.equal(c.marketplaceFailure(new Error('plain'), true).error_code, 'rejected');
  const missing = c.marketplaceFailure(c.marketplaceMissing('poshmark', [{ field: 'size', needs: 'a size' }]), true);
  assert.equal(missing.error_code, 'missing_fields');
  assert.deepEqual(JSON.parse(JSON.stringify(missing.fields)), [{ field: 'size', message: 'Poshmark needs a size' }]);
  assert.equal(missing.error, 'Poshmark needs a size.');
});

test('a request is never sent after the deadline or a cancel, and each attempt ends by the deadline', async () => {
  const { c, calls, run } = worker([reply(200, {})]);
  const late = await failure(run({ deadline: Date.now() - 1 }));
  assert.equal(late.code, 'network');
  assert.equal(late.rejected, true);
  assert.equal(calls.length, 0);
  let timeout;
  c.AbortSignal = { timeout: ms => { timeout = ms; return AbortSignal.timeout(ms); }, any: AbortSignal.any };
  await run({ deadline: Date.now() + 2000, timeoutMs: 60000 });
  assert.ok(timeout <= 2000);
});

test('the marketplace says what it accepted in JSON only when it labels it JSON', async () => {
  const { run } = worker([reply(200, '{"id":1}', { 'content-type': 'text/plain' })]);
  const error = await failure(run());
  assert.equal(error.code, 'schema_drift');
});
