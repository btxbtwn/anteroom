const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

test('a recorded GraphQL draft save keeps the operation and query, never the values', () => {
  const context = loadWorker();
  const raw = JSON.stringify({
    operationName: 'addDraftItemMutation',
    variables: { input: { common: { name: 'Secret vintage jacket', price: 4200, photoIds: ['p1', 'p2'] } } },
    extensions: { persistedQuery: { version: 1, sha256Hash: 'abc123' } },
    query: 'mutation addDraftItemMutation($input: AddDraftItemInput!) { addDraftItem(input: $input) { item { id } } }',
  });
  const body = vm.runInContext(`recordedBody(${JSON.stringify(raw)})`, context);
  const plain = JSON.parse(JSON.stringify(body));
  assert.equal(plain.kind, 'graphql');
  assert.deepEqual(plain.operations[0], {
    operation: 'addDraftItemMutation',
    hash: 'abc123',
    query: 'mutation addDraftItemMutation($input: AddDraftItemInput!) { addDraftItem(input: $input) { item { id } } }',
    variables: { input: { common: { name: 'string', price: 'number', photoIds: ['string'] } } },
  });
  assert.ok(!JSON.stringify(body).includes('Secret vintage jacket'));
  assert.ok(!JSON.stringify(body).includes('4200'));
});

test('a recorded JSON or binary body keeps only its shape or kind', () => {
  const context = loadWorker();
  const json = JSON.parse(JSON.stringify(vm.runInContext(`recordedBody(${JSON.stringify(JSON.stringify({ title: 'x', tags: ['a'] }))})`, context)));
  assert.deepEqual(json, { kind: 'json', shape: { title: 'string', tags: ['string'] } });
  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('recordedBody("\\u0000binary")', context))), { kind: 'non-json' });
});

test('recording stops on a successful draft operation only', () => {
  const context = loadWorker();
  const saved = (status, operation) => vm.runInContext(`draftSaved(${JSON.stringify({ status, body: { operations: [{ operation }] } })})`, context);
  assert.equal(saved(200, 'addDraftItemMutation'), true);
  assert.equal(saved(500, 'addDraftItemMutation'), false);
  assert.equal(saved(200, 'myProfileQuery'), false);
  assert.equal(saved(200, 'existingTemplateDraftItemsQuery'), false);
});

test('a recorded GraphQL file upload keeps its operation and part names, never the file', () => {
  const context = loadWorker();
  const boundary = '----WebKitFormBoundaryABC';
  const raw = [
    `--${boundary}`, 'Content-Disposition: form-data; name="operations"', '',
    JSON.stringify({ operationName: 'uploadDraftItemImage', variables: { input: { image: null, itemId: 'm123' } },
      extensions: { persistedQuery: { version: 1, sha256Hash: 'def456' } } }),
    `--${boundary}`, 'Content-Disposition: form-data; name="map"', '', '{"1":["variables.input.image"]}',
    `--${boundary}`, 'Content-Disposition: form-data; name="1"; filename="photo.jpg"', 'Content-Type: image/jpeg', '', 'JPEGBYTES',
    `--${boundary}--`, '',
  ].join('\r\n');
  const body = JSON.parse(JSON.stringify(vm.runInContext(
    `recordedBody(${JSON.stringify(raw)}, ${JSON.stringify(`multipart/form-data; boundary=${boundary}`)})`, context)));
  assert.deepEqual([...body.parts].sort(), ['1', 'map', 'operations']);
  assert.deepEqual(body.map, { 1: ['variables.input.image'] });
  assert.deepEqual(body.operations[0], { operation: 'uploadDraftItemImage', hash: 'def456', query: null,
    variables: { input: { image: 'null', itemId: 'string' } } });
  assert.ok(!JSON.stringify(body).includes('JPEGBYTES'));
  assert.ok(!JSON.stringify(body).includes('m123'));
});
