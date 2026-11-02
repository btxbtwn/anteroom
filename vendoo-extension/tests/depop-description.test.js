const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

test('Depop description is the description, not the title pasted in front', () => {
  const worker = loadWorker();
  const title = 'Columbia XL (18/20) Outdoors Hooded Windbreaker Blue Regular Fit';
  const description = `${title}.\n\nMeasurements: Pit to pit: 20"`;
  assert.equal(call(worker, 'depopDescription', { title, description }), description);
  assert.equal(call(worker, 'depopDescription', { title, description: '' }), '');
  assert.equal(call(worker, 'depopDescription', { title }), '');
});
