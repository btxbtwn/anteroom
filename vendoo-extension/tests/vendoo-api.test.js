const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {loadWorker, reply} = require('./worker-context');
const protocol = require('../cloud-marketplace-protocol');

test('Vendoo off refuses before opening a tab or reading a sign-in', async () => {
  const worker = loadWorker();
  let reads = 0, result;
  worker.freshVendooSession = async () => { reads++; throw new Error('must not run'); };
  await worker.handleVendooApiMessage({job_id: 'abc123', payload: {ops: [{op: 'session'}]}}, value => { result = value; });
  assert.equal(result.ok, false);
  assert.equal(reads, 0);
});

test('Vendoo sessions remain in memory and no credentials enter the reply', async () => {
  const worker = loadWorker();
  let writes = 0;
  worker.chrome.storage.local.set = async () => { writes++; };
  const session = {uid: 'seller', access_token: 'private-token', refresh_token: 'private-refresh', expiration_time: Date.now() + 3600000};
  worker.readVendooSession = async () => session;
  const result = await worker.runVendooApiOps([{op: 'session'}]);
  assert.equal(result.results[0].uid, 'seller');
  assert.equal(JSON.stringify(result).includes('private-'), false);
  assert.equal(writes, 0);
  worker.readVendooSession = async () => ({...session, uid: 'different-seller'});
  const changed = await worker.runVendooApiOps([{op: 'session', expected_uid: 'seller'}]);
  assert.equal(changed.ok, false);
});

test('parallel photo uploads finish before the dependent private draft write', async () => {
  const worker = loadWorker();
  worker.freshVendooSession = async () => ({uid: 'seller'});
  const complete = [];
  worker.uploadVendooPhoto = async (_session, photo) => { complete.push(photo.id); return {id: photo.id, version: 3}; };
  worker.createVendooItem = async () => { assert.deepEqual(complete.sort(), ['1', '2', '3']); return {}; };
  const result = await worker.runVendooApiOps([
    ...['1', '2', '3'].map(id => ({op: 'upload_photo', photo: {id}})), {op: 'create_item', item: {userID: 'seller'}},
  ]);
  assert.equal(result.ok, true);
  assert.deepEqual(Array.from(result.results.slice(0, 3), row => row.photo_id), ['1', '2', '3']);
});

test('an account mismatch stops before uploads and writes', async () => {
  const worker = loadWorker();
  worker.freshVendooSession = async () => ({uid: 'different-seller'});
  worker.uploadVendooPhoto = async () => { throw new Error('must not upload'); };
  const result = await worker.runVendooApiOps([{op: 'session', expected_uid: 'original-seller'}, {op: 'upload_photo', photo: {id: '1'}}]);
  assert.equal(result.ok, false);
  assert.equal(result.results.length, 1);
});

test('a failed Vendoo create is never retried automatically', async () => {
  const worker = loadWorker();
  let calls = 0;
  worker.fetch = async () => { calls++; return reply(503, {message: 'Unavailable'}); };
  await assert.rejects(worker.createVendooItem({uid: 'seller', access_token: 'private'}, {userID: 'seller'}, null));
  assert.equal(calls, 1);
});

test('private Vendoo draft transport refuses publishing and deletion even with confirmation', async () => {
  const worker = loadWorker();
  worker.freshVendooSession = async () => ({uid: 'seller'});
  let writes = 0;
  worker.fetch = async () => { writes++; throw new Error('must not write'); };
  for (const op of ['list_item', 'delist_item', 'delete_item']) {
    for (const options of [{}, {confirm: true}, {confirm: true, marketplaces: []}, {confirm: true, marketplaces: ['all']}]) {
      assert.equal((await worker.runVendooApiOps([{op, ...options}])).ok, false);
    }
    assert.equal((await worker.runVendooApiOps([{op, confirm: true, marketplaces: ['poshmark']}])).ok, false);
  }
  assert.equal(writes, 0);
});

test('the cloud Vendoo draft capability cannot relay publishing, arbitrary photos, or state changes', () => {
  const command = ops => ({type: 'job.vendoo_api', job_id: 'abc123', message_id: 'a'.repeat(32),
    payload: {listing_mode: 'vendoo', request_id: 'a'.repeat(32), ops}});
  assert.ok(protocol.command(command([{op: 'session'}])));
  for (const op of [{op: 'list_item', confirm: true, marketplaces: ['poshmark']},
    {op: 'delete_item', item_id: 'a'.repeat(20)},
    {op: 'upload_photo', photo: {id: 'abc123', mime_type: 'image/jpeg', url: 'https://example.com/private'}},
    {op: 'update_item', item_id: 'a'.repeat(20), expected_update_time: 'now', updates: {'listings.poshmark.status': {listed: true}}}]) {
    assert.equal(protocol.command(command([op])), null);
  }
  const worker = loadWorker();
  assert.equal(vm.runInContext('typeof handleVendooApiMessage', worker), 'function');
});

test('Vendoo photo upload PUT validates allowlisted HTTPS storage hosts and omits cookies', async () => {
  const session = {uid: 'seller', access_token: 'private-token'};
  const bytes = new Blob([new Uint8Array([1, 2, 3])], {type: 'image/jpeg'});
  for (const allowedUrl of [
    'https://storage.googleapis.com/vendoo-prod-7948f.appspot.com/users/seller/photo.jpg?X-Goog-Signature=abc',
    'https://firebasestorage.googleapis.com/v0/b/vendoo-prod-7948f.appspot.com/o/photo.jpg?token=abc',
    'https://vendoo-uploads.s3.amazonaws.com/users/seller/photo.jpg?X-Amz-Signature=abc',
    'https://vendoo-uploads.s3-accelerate.amazonaws.com/users/seller/photo.jpg?X-Amz-Signature=abc',
  ]) {
    const worker = loadWorker();
    const calls = [];
    worker.fetch = async (url, init) => {
      calls.push({url: String(url), init});
      if (calls.length === 1) return reply(200, {url: allowedUrl, imagePath: 'users/seller/photo.jpg'});
      return reply(200, null);
    };
    const result = await worker.uploadVendooPhotoBytes(session, {mime_type: 'image/jpeg', max_dimension: 1200}, bytes, 'jpg');
    assert.equal(result.id, 'users/seller/photo.jpg');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, allowedUrl);
    assert.equal(calls[1].init.method, 'PUT');
    assert.equal(calls[1].init.credentials, 'omit');
    assert.equal(calls[1].init.headers.Authorization, undefined);
  }

  for (const rejectedUrl of [
    'http://storage.googleapis.com/vendoo-prod-7948f.appspot.com/photo.jpg',
    'https://evil.example/upload',
    'https://storage.googleapis.com.evil.example/photo.jpg',
    'https://127.0.0.1:4318/api/settings',
    'https://poshmark.com/vm-rest/posts',
    'https://storage.googleapis.com:8443/photo.jpg',
    'https://user:pass@storage.googleapis.com/photo.jpg',
    'https://s3.amazonaws.com/bucket/photo.jpg',
  ]) {
    const worker = loadWorker();
    const calls = [];
    worker.fetch = async (url, init) => {
      calls.push({url: String(url), init});
      return reply(200, {url: rejectedUrl, imagePath: 'users/seller/photo.jpg'});
    };
    await assert.rejects(
      worker.uploadVendooPhotoBytes(session, {mime_type: 'image/jpeg'}, bytes, 'jpg'),
      /recognised photo upload address/,
    );
    assert.equal(calls.length, 1, `must not PUT to rejected URL ${rejectedUrl}`);
  }
});
