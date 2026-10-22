// 2026-10-23 (exam tools): open-book materials on the student side.
//
// Before the exam: upload checks the page can do itself (count, size, type) so the student
// hears about a problem before a 50 MB upload. During the exam: the attempt's files are
// fetched once when the exam opens and kept in memory, so they stay readable if the Wi-Fi
// drops later. After a reload without the connection they are not there ("Materials need
// the connection"); the exam itself keeps working offline as before.

import { NetworkError, materialsApi } from "../api/client";
import type { MaterialFile } from "../api/types";

export const MAX_FILES = 5;
export const MAX_BYTES = 50 * 1024 * 1024;
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
  if (used + file.size > MAX_BYTES) return `${file.name} is too big: files can be at most 50 MB in all.`;
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
  blobs: Map<string, Blob>;
  loading: Promise<MaterialFile[]> | null;
}

const cache = new Map<string, Entry>();

function entry(aid: string): Entry {
  let e = cache.get(aid);
  if (!e) {
    e = { files: null, blobs: new Map(), loading: null };
    cache.set(aid, e);
  }
  return e;
}

/** The attempt's files (from memory after the first time). Also fetches every file's bytes
 *  in the background so they can be read without the connection later. */
export function loadAttemptMaterials(aid: string): Promise<MaterialFile[]> {
  const e = entry(aid);
  if (e.files) return Promise.resolve(e.files);
  if (e.loading) return e.loading;
  const p = materialsApi.attemptFiles(aid).then(
    (r) => {
      e.files = r.files;
      e.loading = null;
      // prefetch one after another (no burst of 5 big downloads)
      void r.files.reduce<Promise<unknown>>(
        (prev, f) => prev.then(() => materialBlob(aid, f.id).catch(() => undefined)),
        Promise.resolve(),
      );
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

/** One file's bytes: from memory, else from the server (then kept). */
export function materialBlob(aid: string, id: string): Promise<Blob> {
  const e = entry(aid);
  const have = e.blobs.get(id);
  if (have) return Promise.resolve(have);
  const key = `${aid}/${id}`;
  const running = inflight.get(key);
  if (running) return running;
  const p = materialsApi.attemptFile(aid, id).then(
    (b) => {
      e.blobs.set(id, b);
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
}

/** Test helper. */
export function _resetMaterialsForTests(): void {
  cache.clear();
  inflight.clear();
}
