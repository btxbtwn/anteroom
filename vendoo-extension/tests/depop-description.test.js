const test = require('node:test');
const assert = require('node:assert/strict');
const { depopDescription } = require('../content-scripts/depop.js');

test('Depop description is the description, not the title pasted in front', () => {
  const title = 'Columbia XL (18/20) Outdoors Hooded Windbreaker Blue Regular Fit';
  const description = `${title}.\n\nMeasurements: Pit to pit: 20"`;
  assert.equal(depopDescription({ title, description }), description);
  assert.equal(depopDescription({ title, description: '' }), '');
  assert.equal(depopDescription({ title }), '');
  assert.equal(depopDescription(null), '');
});
