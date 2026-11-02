// Named commands other than marketplace actions: read-only choices for the seller's
// editor and Connections, from their own marketplace sessions, and opening a sign-in page.
const CLOUD_READS = {
  'marketplace.vinted_categories': async () => ({ options: await vintedCategoryOptions() }),
  'marketplace.vinted_brands': async ({ query, category }) => ({ options: await vintedBrandOptions({ query, category }) }),
  'marketplace.sellwild_account': async () => sellwildAccountChoices(Date.now() + 25000),
  // Settings → Connections: sign-in status only, never cookie or token values.
  'marketplace.sessions': async () => ({ sessions: await readExtensionSessions() }),
  // The seller asked to sign in: open that marketplace's sign-in page in this Chrome, as the Mac does.
  'marketplace.connect': ({ marketplace }) => new Promise(resolve => handleMarketplaceConnect({ payload: { marketplace } }, resolve)),
  // Check on Facebook: one linked listing's own page, read as the Mac reads it. Nothing on Facebook changes.
  'marketplace.listing_state': ({ marketplace, listing_id }) =>
    new Promise(resolve => handleMarketplaceListingState({ payload: { marketplace, listing_id } }, resolve)),
};
const CLOUD_READ_ERRORS = new Set(['signed_out', 'challenge', 'rate_limited', 'schema_drift', 'network']);

// Only fixed error codes go back: the marketplace's own words and headers stay in Chrome.
async function runCloudRead(command) {
  try {
    return { ok: true, ...await CLOUD_READS[command.type](command.payload) };
  } catch (error) {
    return { ok: false, error_code: CLOUD_READ_ERRORS.has(error?.code) ? error.code : 'network' };
  }
}

// Runs the seller's relayed marketplace command with the same handlers the Mac's
// socket uses, and returns the Bridge's reply instead of sending it to a socket.
// The hosted API approved the action; the handlers still check its confirmation.
async function runCloudMarketplaceCommand(command, photos) {
  if (Object.hasOwn(CLOUD_READS, command.type)) return runCloudRead(command);
  const jobId = String(command.job_id);
  if (command.type === 'job.cancel') {
    await cancelNativeMarketplaceOperation(String(command.payload.job_id));
    return { ok: true };
  }
  if (photos.length) {
    relayedPhotos.set(jobId, new Map(photos.map(photo => [photo.id, { base64: photo.base64, mime_type: photo.mime_type }])));
  }
  let result = null;
  const reply = value => { result = value; };
  try {
    if (command.type === 'marketplace.delete') await handleMarketplaceDelete(command, reply);
    else await handleNativeMarketplaceMessage(command, reply);
  } finally {
    relayedPhotos.delete(jobId);
  }
  return result || marketplaceFailure(marketplaceError('The Bridge did not answer this action.'), false);
}
