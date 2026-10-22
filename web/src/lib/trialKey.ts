// Sample exam: its offline exit key, kept for this tab only (lib/offlineExit.ts). Shared by the
// exam page (fetches it while online) and the done page (2026-10-22: kept until the app has
// unlocked, so a hand-in the app cannot confirm still has a way out).

const storageKey = (aid: string) => `fairplay:trialkey:${aid}`;

export function readTrialKey(aid: string): string | null {
  try {
    return sessionStorage.getItem(storageKey(aid));
  } catch {
    return null;
  }
}

export function writeTrialKey(aid: string, key: string | null): void {
  try {
    if (key) sessionStorage.setItem(storageKey(aid), key);
    else sessionStorage.removeItem(storageKey(aid));
  } catch {
    /* storage unavailable: kept in memory */
  }
}
