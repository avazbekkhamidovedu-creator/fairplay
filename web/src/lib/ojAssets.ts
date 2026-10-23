// DKU OJ bridge: pictures of DKU OJ questions ("fp-asset:<name>").
//
// The asset URLs need the sign-in (and, for students, the attempt's app session), so an
// <img src> cannot point at them directly: the bytes are fetched through the API layer
// and shown from an object URL, kept in memory per attempt (or exam) until forgetAssets.
// Offline packages carry the pictures as data: URLs in question.asset_urls, like they
// carry question images (server offline.inline_images); those are used first.
import { ojApi } from "../api/client";

export type AssetFetcher = (name: string) => Promise<Blob>;

export interface AssetContext {
  /** cache scope: "a:<attempt id>" or "t:<exam id>" */
  scope: string;
  /** name -> URL already available (offline package data: URLs) */
  inline?: Record<string, string> | null;
  /** fetches the bytes; null = offline attempt not on the server yet */
  fetch: AssetFetcher | null;
}

const cache = new Map<string, Promise<string>>();
const made = new Map<string, string>(); // cache key -> object URL (to revoke)

const cacheKey = (scope: string, name: string) => `${scope}\u0000${name}`;

function objectUrl(blob: Blob): string {
  return typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : "";
}

/** URL to show for one picture. Rejects when it cannot be loaded (offline, missing). */
export function resolveAsset(ctx: AssetContext, name: string): Promise<string> {
  const inline = ctx.inline?.[name];
  if (inline) return Promise.resolve(inline);
  if (!ctx.fetch) return Promise.reject(new Error("Picture not available offline"));
  const key = cacheKey(ctx.scope, name);
  const have = cache.get(key);
  if (have) return have;
  const p = ctx.fetch(name).then(
    (b) => {
      const url = objectUrl(b);
      if (!url) throw new Error("Cannot show the picture");
      made.set(key, url);
      return url;
    },
    (e) => {
      cache.delete(key); // try again next time (the Wi-Fi may be back)
      throw e;
    },
  );
  cache.set(key, p);
  return p;
}

/** Load pictures now (while online), so they still show if the Wi-Fi drops. */
export function prefetchAssets(ctx: AssetContext, names: string[]): void {
  for (const n of names) void resolveAsset(ctx, n).catch(() => undefined);
}

/** Drop the pictures of one attempt / exam (after the hand-in). */
export function forgetAssets(scope: string): void {
  const prefix = `${scope}\u0000`;
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k);
  for (const [k, url] of [...made]) {
    if (!k.startsWith(prefix)) continue;
    made.delete(k);
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  }
}

export function attemptAssetContext(aid: string | null, inline?: Record<string, string> | null): AssetContext {
  return { scope: `a:${aid ?? "offline"}`, inline, fetch: aid ? (n) => ojApi.attemptAsset(aid, n) : null };
}

export function teacherAssetContext(examId: string, inline?: Record<string, string> | null): AssetContext {
  return { scope: `t:${examId}`, inline, fetch: (n) => ojApi.teacherAsset(examId, n) };
}

/** For tests. */
export function _resetAssetsForTests(): void {
  cache.clear();
  made.clear();
}
