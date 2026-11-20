// Adapted from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: apps/web/src/modelOrdering.ts. The same ordering
// (favorites first when grouped, then provider order, then catalog order),
// written with a plain comparator instead of effect/Order.
import type { ListingProviderId as ProviderInstanceId } from "../../api/types";

export interface ProviderModelItem {
  readonly slug: string;
  readonly instanceId: ProviderInstanceId;
}

export function providerModelKey(instanceId: ProviderInstanceId, slug: string): string {
  return `${instanceId}:${slug}`;
}

export function sortProviderModelItems<T extends ProviderModelItem>(
  items: ReadonlyArray<T>,
  options?: {
    readonly favoriteModelKeys?: ReadonlySet<string>;
    readonly groupFavorites?: boolean;
    readonly instanceOrder?: ReadonlyArray<ProviderInstanceId>;
  },
): T[] {
  const favoriteModelKeys = options?.favoriteModelKeys ?? new Set<string>();
  const instanceOrder = new Map((options?.instanceOrder ?? []).map((instanceId, index) => [instanceId, index] as const));
  const originalOrder = new Map(items.map((item, index) => [providerModelKey(item.instanceId, item.slug), index] as const));
  const rank = (value: number | undefined) => value ?? Number.POSITIVE_INFINITY;
  const favorite = (item: T) => favoriteModelKeys.has(providerModelKey(item.instanceId, item.slug));
  return [...items].sort((left, right) => {
    if (options?.groupFavorites === true) {
      const delta = Number(favorite(right)) - Number(favorite(left));
      if (delta !== 0) return delta;
    }
    const leftInstance = rank(instanceOrder.get(left.instanceId));
    const rightInstance = rank(instanceOrder.get(right.instanceId));
    if (leftInstance !== rightInstance) return leftInstance < rightInstance ? -1 : 1;
    const leftOrder = rank(originalOrder.get(providerModelKey(left.instanceId, left.slug)));
    const rightOrder = rank(originalOrder.get(providerModelKey(right.instanceId, right.slug)));
    return leftOrder === rightOrder ? 0 : leftOrder < rightOrder ? -1 : 1;
  });
}
