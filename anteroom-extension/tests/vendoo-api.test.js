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
