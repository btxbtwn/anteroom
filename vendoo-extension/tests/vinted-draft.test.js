const test = require('node:test');
const assert = require('node:assert/strict');
const { classifyVintedControl, vintedConditionCandidates, vintedListControl } = require('../content-scripts/vinted.js');

test('Upload is the list control, and Save draft is not', () => {
  assert.equal(vintedListControl('Upload'), true);
  assert.equal(vintedListControl('Publish'), true);
  assert.equal(vintedListControl('Upload item'), true);
  assert.equal(vintedListControl('Save draft'), false);
  assert.equal(vintedListControl('Save as draft'), false);
  assert.equal(vintedListControl('List this item'), false);
  assert.equal(vintedListControl('Save'), false);
  assert.equal(classifyVintedControl('Save draft'), 'save');
  assert.equal(classifyVintedControl(''), 'ignore');
});

test('Vinted condition labels try the live form wording', () => {
  assert.deepEqual(vintedConditionCandidates('Very good'), ['Very good']);
  assert.deepEqual(vintedConditionCandidates('New without tags'), ['New without tags', 'Like new']);
  assert.deepEqual(vintedConditionCandidates(''), []);
});
