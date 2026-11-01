// Runs the seller's relayed marketplace command with the same handlers the Mac's
// socket uses, and returns the Bridge's reply instead of sending it to a socket.
// The hosted API approved the action; the handlers still check its confirmation.
async function runCloudMarketplaceCommand(command, photos) {
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
