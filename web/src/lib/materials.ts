// 2026-10-23 (exam tools): open-book materials on the student side.
//
// Before the exam: upload checks the page can do itself (count, size, type) so the student
// hears about a problem before a 20 MB upload. During the exam: the attempt's file list is
// fetched once (small), and a file's bytes only when the student opens it.
//
// 2026-10-23 (review): no prefetch of every file when the exam opens (a whole room at once
// was too much for the server and the database). An opened file stays in memory (LRU, at
// most BLOB_CACHE_BYTES) for re-opens during the attempt, and the browser may keep it too
// (the server allows private caching with an ETag). A file never opened needs the
// connection ("Materials need the connection"); the exam itself keeps working offline.

import { NetworkError, materialsApi } from "../api/client";
import type { MaterialFile } from "../api/types";

export const MAX_FILES = 5;
export const MAX_BYTES = 20 * 1024 * 1024;
export const MAX_LABEL = "20 MB";
/** Opened files kept in memory, across the attempt (oldest opened dropped first). */
export const BLOB_CACHE_BYTES = 60 * 1024 * 1024;
export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.txt,.md,application/pdf,image/png,image/jpeg,text/plain,text/markdown";
const EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md", ".markdown"];

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  const mb = n / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/** Why this file can't be added, or null. The server checks again (by the bytes). */
export function checkFile(file: { name: string; size: number }, count: number, used: number): string | null {
  const name = file.name.toLowerCase();
  if (!EXTENSIONS.some((e) => name.endsWith(e))) return `${file.name}: use a PDF, PNG, JPEG, TXT or MD file.`;
  if (file.size === 0) return `${file.name} is empty.`;
  if (count >= MAX_FILES) return `You can upload at most ${MAX_FILES} files. Remove one first.`;
  if (used + file.size > MAX_BYTES) return `${file.name} is too big: files can be at most ${MAX_LABEL} in all.`;
  return null;
}

export type MaterialsState =
  | { status: "loading" }
  | { status: "ready"; files: MaterialFile[] }
  /** offline: the list (or a file) could not be fetched and nothing is in memory */
  | { status: "offline" }
  | { status: "error"; message: string };

interface Entry {
  files: MaterialFile[] | null;
  loading: Promise<MaterialFile[]> | null;
}

const cache = new Map<string, Entry>();
/** `${aid}/${id}` -> bytes; Map order is the LRU order (last = most recently opened). */
const blobs = new Map<string, Blob>();
let blobBytes = 0;

function entry(aid: string): Entry {
  let e = cache.get(aid);
  if (!e) {
    e = { files: null, loading: null };
    cache.set(aid, e);
  }
  return e;
}

function keepBlob(key: string, b: Blob): void {
  const old = blobs.get(key);
  if (old) {
    blobs.delete(key);
    blobBytes -= old.size;
  }
  blobs.set(key, b);
  blobBytes += b.size;
  // drop the least recently opened until it fits (the one just opened always stays)
  for (const [k, v] of blobs) {
    if (blobBytes <= BLOB_CACHE_BYTES || k === key) break;
    blobs.delete(k);
    blobBytes -= v.size;
  }
}

/** The attempt's file list (from memory after the first time). Only the list: a file's
 *  bytes are fetched when it is opened (materialBlob). */
export function loadAttemptMaterials(aid: string): Promise<MaterialFile[]> {
  const e = entry(aid);
  if (e.files) return Promise.resolve(e.files);
  if (e.loading) return e.loading;
  const p = materialsApi.attemptFiles(aid).then(
    (r) => {
      e.files = r.files;
      e.loading = null;
      return r.files;
    },
    (err) => {
      e.loading = null;
      throw err;
    },
  );
  e.loading = p;
  return p;
}

const inflight = new Map<string, Promise<Blob>>();

/** One file's bytes: from memory, else from the server (then kept, see keepBlob). */
export function materialBlob(aid: string, id: string): Promise<Blob> {
  const key = `${aid}/${id}`;
  const have = blobs.get(key);
  if (have) {
    keepBlob(key, have); // now the most recently opened
    return Promise.resolve(have);
  }
  const running = inflight.get(key);
  if (running) return running;
  const p = materialsApi.attemptFile(aid, id).then(
    (b) => {
      keepBlob(key, b);
      inflight.delete(key);
      return b;
    },
    (err) => {
      inflight.delete(key);
      throw err;
    },
  );
  inflight.set(key, p);
  return p;
}

export function isOfflineError(e: unknown): boolean {
  return e instanceof NetworkError;
}

/** Forget an attempt's files (after the hand-in). */
export function forgetAttemptMaterials(aid: string): void {
  cache.delete(aid);
  for (const [k, v] of blobs) {
    if (k.startsWith(`${aid}/`)) {
      blobs.delete(k);
      blobBytes -= v.size;
    }
  }
}

/** Test helper: the bytes kept in memory now, and for which files (LRU order). */
export function _materialsMemoryForTests(): { bytes: number; keys: string[] } {
  return { bytes: blobBytes, keys: [...blobs.keys()] };
}

/** Test helper. */
export function _resetMaterialsForTests(): void {
  cache.clear();
  inflight.clear();
  blobs.clear();
  blobBytes = 0;
}
