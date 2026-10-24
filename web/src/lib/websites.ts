// 2026-10-23 (exam tools): allowed websites of an exam, shown in the exam's websites pane
// (the apps enforce the same list inside sub-frames). Mirrors server schemas.normalize_website.
// 2026-10-23 (review): an entry is matched exactly, like the apps do: "docs.python.org" is
// that host (and the same host with "www." added or removed); "*.python.org" is python.org
// and all its subdomains. No wildcard on a public suffix or shared hosting ("*.com",
// "*.github.io"): anyone can get a subdomain there. SHARED_SUFFIXES is the apps' list.

export const MAX_WEBSITES = 20;

export const WILDCARD_PREFIX = "*.";

export const SHARED_SUFFIXES: ReadonlySet<string> = new Set([
  "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io", "app", "dev", "page",
  "site", "online", "xyz", "me", "co", "ai", "cn", "uk", "us", "eu", "de", "ru", "uz", "jp",
  "co.uk", "org.uk", "ac.uk", "gov.uk", "com.cn", "net.cn", "org.cn", "edu.cn", "gov.cn", "ac.cn",
  "github.io", "githubusercontent.com", "github.com", "gitlab.io", "gitlab.com", "bitbucket.io",
  "vercel.app", "now.sh", "netlify.app", "netlify.com", "pages.dev", "workers.dev",
  "web.app", "firebaseapp.com", "herokuapp.com", "blogspot.com", "wordpress.com",
  "glitch.me", "replit.app", "replit.dev", "repl.co", "onrender.com", "fly.dev",
  "azurewebsites.net", "azurestaticapps.net", "cloudfront.net", "amazonaws.com",
  "appspot.com", "wixsite.com", "weebly.com", "notion.site", "carrd.co", "surge.sh",
  "neocities.org", "codepen.io", "jsfiddle.net", "stackblitz.io", "codesandbox.io",
  "ngrok.io", "ngrok-free.app", "trycloudflare.com", "google.com", "googleusercontent.com",
  "sites.google.com", "docs.google.com", "translate.goog", "s3.amazonaws.com",
]);

/** Second-level labels under a two-letter country code that are public suffixes too. */
const COUNTRY_SECOND_LEVELS = new Set(["com", "net", "org", "edu", "gov", "mil", "ac", "co", "go", "or", "ne", "gob", "nic", "sch", "gv"]);

/** A wildcard base nobody may use: on the list, a single label, or "com.au" / "edu.uz". */
export function isSharedSuffix(host: string): boolean {
  if (SHARED_SUFFIXES.has(host)) return true;
  const labels = host.split(".");
  if (labels.length < 2) return true;
  return labels.length === 2 && labels[1]!.length === 2 && COUNTRY_SECOND_LEVELS.has(labels[0]!);
}

const HOST_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/;

export type WebsiteCheck = { ok: true; host: string } | { ok: false; error: string };

/** "https://Docs.Python.org/" -> "docs.python.org", "*.Python.org" -> "*.python.org"; a path,
 *  a port, an IP, or a "*" anywhere else is refused. `host` is the entry as stored. */
export function normalizeWebsite(input: string): WebsiteCheck {
  const raw = input.trim();
  if (!raw) return { ok: false, error: "Type a website, like docs.python.org." };
  let s = raw;
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\//.exec(s);
  if (scheme) {
    if (!/^https?$/i.test(scheme[1] ?? "")) return { ok: false, error: `"${raw}" is not a website.` };
    s = s.slice(scheme[0].length);
  }
  const slash = s.indexOf("/");
  let host = (slash >= 0 ? s.slice(0, slash) : s).toLowerCase().replace(/\.$/, "");
  const rest = slash >= 0 ? s.slice(slash + 1).replace(/\/+$/, "") : "";
  const wildcard = host.startsWith(WILDCARD_PREFIX);
  if (wildcard) host = host.slice(WILDCARD_PREFIX.length);
  if (rest || /[?#:@*]/.test(host))
    return { ok: false, error: "Use only the site name, like docs.python.org or *.python.org." };
  if (host.length > 253 || !HOST_RE.test(host) || /^[0-9.]+$/.test(host))
    return { ok: false, error: `"${raw}" is not a website name like docs.python.org.` };
  if (wildcard && isSharedSuffix(host))
    return { ok: false, error: `Anyone can make a site under ${host}. Name the site itself, like docs.python.org.` };
  return { ok: true, host: (wildcard ? WILDCARD_PREFIX : "") + host };
}

/** The host an entry opens on ("*.python.org" -> "python.org"). */
export function siteHost(entry: string): string {
  return entry.startsWith(WILDCARD_PREFIX) ? entry.slice(WILDCARD_PREFIX.length) : entry;
}

/** Address the pane opens for an allowed entry. */
export function siteUrl(entry: string): string {
  return `https://${siteHost(entry)}/`;
}

/** What the student sees for an entry ("*.python.org" -> "python.org (all pages)"). */
export function siteLabel(entry: string): string {
  return entry.startsWith(WILDCARD_PREFIX) ? `${siteHost(entry)} (all pages)` : entry;
}

/** Does `host` match one entry? Same rule as the apps' AllowedSites.entryMatches. */
export function entryMatches(host: string, entry: string): boolean {
  const h = host.toLowerCase().replace(/\.$/, "");
  if (entry.startsWith(WILDCARD_PREFIX)) {
    const base = siteHost(entry);
    return h === base || h.endsWith(`.${base}`);
  }
  if (h === entry || h === `www.${entry}`) return true;
  return entry.startsWith("www.") && entry.slice(4).includes(".") && h === entry.slice(4);
}

/** Is this host allowed by one of the entries? */
export function hostAllowed(host: string, allowed: string[]): boolean {
  return allowed.some((a) => entryMatches(host, a));
}

// 2026-10-24: allowed websites may come with full links (start pages) and companions.

/** What the page hands the app as allowed_sites: the exam's websites plus the sites a
 *  service needs to work (the server's `allowed_sites`; older servers: the websites). */
export function lockSites(tools: { websites?: string[]; allowed_sites?: string[] } | null | undefined): string[] {
  if (!tools) return [];
  return [...(tools.allowed_sites ?? tools.websites ?? [])];
}

export interface SiteEntry {
  key: string;
  host: string;
  url: string;
  label: string;
  /** the link's own text below the title ("duke.app.box.com/folder/0") */
  detail?: string;
}

/** "https://duke.app.box.com/folder/0" -> "duke.app.box.com/folder/0". */
export function shortLink(url: string): string {
  return url.replace(/^https?:\/\//i, "").replace(/\/$/, "");
}

/** The rows of the websites list: each start page (link) of a site, else the site itself. */
export function siteEntries(
  sites: string[],
  links: { host: string; url: string; title?: string | null }[] | null | undefined,
): SiteEntry[] {
  const out: SiteEntry[] = [];
  for (const host of sites) {
    const mine = (links ?? []).filter((l) => l.host === host && /^https:\/\//i.test(l.url));
    if (mine.length === 0) {
      out.push({ key: host, host, url: siteUrl(host), label: siteLabel(host) });
      continue;
    }
    for (const l of mine)
      out.push({ key: l.url, host, url: l.url, label: l.title || shortLink(l.url), detail: l.title ? shortLink(l.url) : undefined });
  }
  return out;
}

/** Teacher input: a site name or a full link (optionally after a title: "Course folder
 *  https://duke.app.box.com/folder/0"). Mirrors server schemas.parse_website_entry. */
export type WebsiteEntryCheck =
  | { ok: true; host: string; link: { url: string; title: string | null } | null }
  | { ok: false; error: string };

export function parseWebsiteEntry(input: string): WebsiteEntryCheck {
  const raw = input.trim();
  if (!raw) return { ok: false, error: "Type a website or a link, like docs.python.org or https://duke.app.box.com/folder/0." };
  let title: string | null = null;
  let s = raw;
  const m = /^(.*\S)\s+(https?:\/\/\S+)$/i.exec(raw);
  if (m) {
    title = m[1]!.replace(/\s+/g, " ").trim().slice(0, 80) || null;
    s = m[2]!;
  }
  if (!/^https?:\/\//i.test(s)) {
    if (/\s/.test(s)) return { ok: false, error: `"${raw}" is not a website or a link.` };
    const r = normalizeWebsite(s);
    return r.ok ? { ok: true, host: r.host, link: null } : r;
  }
  if (s.length > 2000) return { ok: false, error: "That link is too long." };
  const rest = s.replace(/^https?:\/\//i, "");
  const cut = rest.search(/[/?#]/);
  const authority = cut >= 0 ? rest.slice(0, cut) : rest;
  let tail = cut >= 0 ? rest.slice(cut) : "";
  if (authority.startsWith(WILDCARD_PREFIX)) return { ok: false, error: "A link can't have a *. wildcard." };
  if (/[@:]/.test(authority)) return { ok: false, error: "Use a link without a port or a user name." };
  const r = normalizeWebsite(authority);
  if (!r.ok) return r;
  if ((tail === "" || tail === "/") && title === null) return { ok: true, host: r.host, link: null };
  if (tail && !tail.startsWith("/")) tail = `/${tail}`;
  if (!/^[A-Za-z0-9._~!$&'()*+,;=:@%/?#[\]-]*$/.test(tail)) return { ok: false, error: `"${raw}" has characters a link can't have.` };
  return { ok: true, host: r.host, link: { url: `https://${r.host}${tail || "/"}`, title } };
}
