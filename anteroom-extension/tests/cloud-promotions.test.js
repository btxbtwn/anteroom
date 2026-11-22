const test = require('node:test');
const assert = require('node:assert/strict');
const protocol = require('../cloud-marketplace-protocol');
const { loadWorker } = require('./worker-context');
const request = 'a'.repeat(32);
const post = 'b'.repeat(24);
function command(type, payload) {
  return { type, job_id: 'job-1', message_id: request, payload: { job_id: 'job-1', request_id: request, ...payload } };
}
const send = { marketplace: 'poshmark', action: 'share', listing_id: post, action_id: 'action-123',
  confirm: true, price_cents: 4500, offer_price_cents: null, floor_cents: null, shipping_discount_id: null };

test('hosted promotions have bounded commands and require named confirmed writes', () => {
  assert.ok(protocol.command(command('marketplace.promote', send)));
  assert.ok(protocol.command(command('marketplace.promotion_check', { marketplace: 'poshmark', action: 'share', listing_id: post })));
  assert.ok(protocol.command(command('marketplace.promotion_result', { action_id: 'action-123' })));
  assert.ok(protocol.command(command('marketplace.like_events', { marketplace: 'depop' })));
  for (const patch of [{ confirm: false }, { confirm: undefined }, { marketplace: 'ebay' }, { action: 'list' },
    { action: 'delist' }, { listing_id: 'https://evil.example' }, { price_cents: -1 }, { action_id: '../action' },
    { cookie: 'secret' }, { gap_ms: 1 }, { offer_price_cents: '45' }]) {
    assert.equal(protocol.command(command('marketplace.promote', { ...send, ...patch })), null, JSON.stringify(patch));
  }
  assert.equal(protocol.command(command('marketplace.like_events', { marketplace: 'mercari' })), null);
  assert.equal(protocol.command(command('marketplace.promotion_check', { marketplace: 'mercari', action: 'share', listing_id: 'm1234567' })), null);
});

test('cloud sends use the desktop journal and never resend an action', async () => {
  const c = loadWorker();
  const stored = {};
  c.chrome.storage.local.get = async key => ({ [key]: stored[key] });
  c.chrome.storage.local.set = async value => Object.assign(stored, value);
  let writes = 0;
  c.runMarketplacePromotion = async () => { writes++; return { ok: true, state: 'done' }; };
  const cmd = command('marketplace.promote', send);
  assert.equal((await c.runCloudMarketplaceCommand(cmd, [])).state, 'done');
  assert.equal((await c.runCloudMarketplaceCommand(cmd, [])).state, 'done');
  assert.equal(writes, 1);
  const result = await c.runCloudMarketplaceCommand(command('marketplace.promotion_result', { action_id: send.action_id }), []);
  assert.equal(result.finished, true);
  assert.equal(writes, 1);
});

test('hosted follow commands require confirmed account writes with a bounded rolling budget', () => {
  const follow_config = { reciprocal_24h: true, unsubscribe_live_shows: false,
    remaining_follow_cap: 15, new_active_sellers_cap: 10, unfollow_cap: 20 };
  const follow = { marketplace: 'poshmark', action: 'follow', listing_id: '__account__',
    action_id: 'action-124', confirm: true, follow_config };
  assert.ok(protocol.command(command('marketplace.promote', follow)));
  assert.equal(protocol.command(command('marketplace.promote', { ...follow, confirm: false })), null);
  for (const patch of [{ remaining_follow_cap: undefined }, { new_active_sellers_cap: 16 }, { unfollow_cap: 1001 }]) {
    assert.equal(protocol.command(command('marketplace.promote', {
      ...follow, follow_config: { ...follow_config, ...patch },
    })), null);
  }
});
