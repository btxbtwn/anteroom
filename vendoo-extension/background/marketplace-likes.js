// Read-only recent-like feeds from the signed-in web clients. Only listing IDs
// and event times leave Chrome; buyer identities and notification text do not.
// These bounded feeds supplement the regular closet scan, not replace it.
async function readMarketplaceLikes(payload) {
  try {
    await ensureMarketplaceRequestContext();
    const deadline = Date.now() + 30000;
    let rows;
    if (payload.marketplace === 'poshmark') {
      const account = await poshmarkAccount();
      const { data } = await marketplaceFetch(`${POSHMARK}/vm-rest/users/${account.user_id}/newsfeed/all?pm_version=vue3.2026.43.00`,
        { marketplace: 'poshmark', step: 'like-events', credentials: 'include', deadline });
      if (!Array.isArray(data?.data)) throw marketplaceError('Poshmark’s recent-like feed changed.', { code: 'schema_drift' });
      rows = data.data.filter(story => story.story_type === 'PersonLikePost').flatMap(story => {
        if (!Array.isArray(story.content?.data)) throw marketplaceError('Poshmark’s recent-like feed changed.', { code: 'schema_drift' });
        return story.content.data.map(item => ({ listing_id: item.news_item?.target?.data?.post_id, at: item.news_item?.created_at }));
      });
    } else if (payload.marketplace === 'depop') {
      const { data } = await depopOfferCall('GET', '/presentation/api/v1/shops/me/products/likes/', deadline, 'like-events');
      if (!Array.isArray(data?.objects)) throw depopOfferDrift('recent-like feed');
      rows = data.objects.map(item => ({ listing_id: item.product?.slug, at: item.liked_at }));
    } else {
      throw marketplaceError('This marketplace has no verified recent-like feed.', { code: 'not_captured' });
    }
    const idPattern = MARKETPLACE_PROMOTIONS[payload.marketplace].id;
    const events = rows.map(row => {
      if (typeof row.listing_id !== 'string' || !idPattern.test(row.listing_id)
          || typeof row.at !== 'string' || !Number.isFinite(Date.parse(row.at))) {
        throw marketplaceError('The marketplace’s recent-like feed changed. Anteroom needs an update.', { code: 'schema_drift' });
      }
      return { listing_id: row.listing_id, at: new Date(row.at).toISOString() };
    });
    return { ok: true, events };
  } catch (error) { return marketplaceFailure(error, true); }
}
