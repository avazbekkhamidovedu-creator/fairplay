// 2026-10-23 (exam tools): allowed websites of an exam. Plain host names; each host and
// its subdomains may be shown in the exam's websites pane (the apps enforce the same list
// inside sub-frames). Mirrors server schemas.normalize_website.

export const MAX_WEBSITES = 20;

const HOST_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/;

export type WebsiteCheck = { ok: true; host: string } | { ok: false; error: string };

/** "https://Docs.Python.org/" -> "docs.python.org"; a path, port, "*." or an IP is refused. */
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
  const host = (slash >= 0 ? s.slice(0, slash) : s).toLowerCase().replace(/\.$/, "");
  const rest = slash >= 0 ? s.slice(slash + 1).replace(/\/+$/, "") : "";
  if (rest || /[?#:@*]/.test(host)) return { ok: false, error: "Use only the site name, like docs.python.org." };
  if (host.length > 253 || !HOST_RE.test(host) || /^[0-9.]+$/.test(host))
    return { ok: false, error: `"${raw}" is not a website name like docs.python.org.` };
  return { ok: true, host };
}

/** Address the pane opens for an allowed host. */
export function siteUrl(host: string): string {
  return `https://${host}/`;
}

/** Is this host one of the allowed ones (or a subdomain of one)? */
export function hostAllowed(host: string, allowed: string[]): boolean {
  const h = host.toLowerCase();
  return allowed.some((a) => h === a || h.endsWith(`.${a}`));
}
