// Uses the same native builder as the Mac. Catalog and account reads only.
async function readCloudPoshmarkForm(listing) {
  const operation = claimNativeOperation('*', `cloud-form-${crypto.randomUUID()}`);
  if (!operation) return { ok: false, error_code: 'busy' };
  try {
    const form = CloudMarketplaceProtocol.form(await readPoshmarkEditorForm(listing, Date.now() + 25000));
    return form ? { ok: true, form } : { ok: false, error_code: 'schema_drift' };
  } catch (error) {
    const code = ['signed_out', 'challenge', 'rate_limited', 'schema_drift'].includes(error?.code) ? error.code : 'network';
    return { ok: false, error_code: code };
  } finally { releaseNativeOperation(operation); }
}
