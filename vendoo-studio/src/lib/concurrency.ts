/** How many per-listing reads a bulk dialog keeps in flight. */
export const BULK_READ_CONCURRENCY = 4;

/**
 * Maps `items` through `work` with at most `limit` calls running at once, so a
 * bulk action over many listings never floods the API. Results keep the input
 * order. Like `Promise.all`, the first rejection rejects the whole run, and no
 * further items start after it.
 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  work: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = [];
  let next = 0;
  let failed = false;
  const worker = async () => {
    while (!failed && next < items.length) {
      const index = next++;
      try {
        results[index] = await work(items[index]!, index);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(items.length, Math.max(1, Math.floor(limit))) }, worker));
  return results;
}
