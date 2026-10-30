// 2026-10-23 (exam tools): "Tools for students" in the exam editor. Simple tick boxes:
// a calculator (scientific or graphing), open book (students upload their files before
// the exam), allowed websites (a list with Add / Remove). Outside apps are gone: every app
// is closed when the computer locks, these tools are built into FairPlay.
import { useEffect, useState } from "react";
import { toolsApi, type SiteCheck } from "../../api/client";
import type { CalculatorMode } from "../../api/types";
import { MAX_WEBSITES, parseWebsiteEntry, shortLink } from "../../lib/websites";
import "../../styles/tools.css";

export interface ExamToolsValue {
  calculator: CalculatorMode;
  openBook: boolean;
  /** 2026-10-24: site names or links ("Title https://…"), as the server takes them */
  websites: string[];
  /** 2026-10-24: open book: uploads open this many days before the date (0 = off) */
  uploadOpensDays?: number;
}

/** 2026-10-24: the choices of "Students can upload their files … before the exam". */
export const UPLOAD_DAYS = [0, 1, 2, 3, 4, 5, 6, 7] as const;

/** One editor line -> its site (allow-list entry) and link (null for a plain site). */
function entryOf(line: string): { host: string; url: string | null; title: string | null } {
  const r = parseWebsiteEntry(line);
  return r.ok ? { host: r.host, url: r.link?.url ?? null, title: r.link?.title ?? null } : { host: line, url: null, title: null };
}

// 2026-10-23 (review): many sites refuse to be shown inside another page (the student would
// see a blank panel). The server checks each listed site once; answers are kept for the
// page's life.
const siteChecks = new Map<string, Promise<SiteCheck | null>>();

export function checkSite(host: string): Promise<SiteCheck | null> {
  let p = siteChecks.get(host);
  if (!p) {
    p = toolsApi.siteCheck(host).catch(() => {
      siteChecks.delete(host);       // offline / rate limited: ask again next time
      return null;
    });
    siteChecks.set(host, p);
  }
  return p;
}

/** Test helper. */
export function _resetSiteChecksForTests(): void {
  siteChecks.clear();
}

function useSiteChecks(hosts: string[]): Record<string, SiteCheck> {
  const [checks, setChecks] = useState<Record<string, SiteCheck>>({});
  const key = hosts.join("\n");
  useEffect(() => {
    let alive = true;
    for (const h of hosts)
      void checkSite(h).then((c) => {
        if (alive && c) setChecks((m) => (m[h] === c ? m : { ...m, [h]: c }));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return checks;
}

export function ExamToolsSettings({
  value,
  onChange,
  hasDate = true,
  boxLinkSet,
}: {
  value: ExamToolsValue;
  onChange: (v: ExamToolsValue) => void;
  /** 2026-10-24: the exam has a date (the early upload window counts back from it) */
  hasDate?: boolean;
  /** 2026-10-30: read-only; whether the exam has a Duke Box folder (never the link itself). Missing = not shown. */
  boxLinkSet?: boolean;
}) {
  const [site, setSite] = useState("");
  const [siteError, setSiteError] = useState<string | null>(null);
  // the website list stays visible while ticked, even when it is still empty
  const [sitesOn, setSitesOn] = useState(value.websites.length > 0);
  const calcOn = value.calculator !== "off";
  const entries = value.websites.map((line) => ({ line, ...entryOf(line) }));
  const hosts = [...new Set(entries.map((e) => e.host))];
  const checks = useSiteChecks(hosts);
  const uploadDays = value.uploadOpensDays ?? 0;

  const addSite = () => {
    const r = parseWebsiteEntry(site);
    if (!r.ok) {
      setSiteError(r.error);
      return;
    }
    const line = r.link ? (r.link.title ? `${r.link.title} ${r.link.url}` : r.link.url) : r.host;
    const same = entries.find((e) => (r.link ? e.url === r.link.url : e.host === r.host && !e.url));
    if (same) {
      setSiteError(`${r.link ? shortLink(r.link.url) : r.host} is already on the list.`);
      return;
    }
    if (!hosts.includes(r.host) && hosts.length >= MAX_WEBSITES) {
      setSiteError(`At most ${MAX_WEBSITES} websites.`);
      return;
    }
    onChange({ ...value, websites: [...value.websites, line] });
    setSite("");
    setSiteError(null);
  };
  // companions shown once per host (the first line of that host)
  const shownCompanions = new Set<string>();

  return (
    <section className="t-card t-tools" aria-labelledby="tools-h">
      <div>
        <h2 id="tools-h" className="t-tools-title">
          Tools for students
        </h2>
        <p className="t-small t-muted" style={{ margin: "2px 0 0" }}>
          Other apps are closed while the computer is locked. Students get only what you tick here, inside FairPlay.
        </p>
      </div>

      <div className="t-tool">
        <label className="t-check">
          <input
            type="checkbox"
            checked={calcOn}
            onChange={(e) => onChange({ ...value, calculator: e.target.checked ? "scientific" : "off" })}
          />
          <span>
            <b>Calculator</b>
            <span className="t-small t-muted"> · a calculator window students can move around</span>
          </span>
        </label>
        {calcOn && (
          <div className="t-tool-sub" role="radiogroup" aria-label="Calculator type">
            {(
              [
                ["scientific", "Scientific", "sin, cos, log, powers, roots, brackets"],
                ["graphing", "Graphing", "scientific, plus graphs of up to 3 functions"],
              ] as const
            ).map(([mode, title, hint]) => (
              <label key={mode} className="t-check">
                <input
                  type="radio"
                  name="exam-calculator"
                  checked={value.calculator === mode}
                  onChange={() => onChange({ ...value, calculator: mode })}
                />
                <span>
                  {title}
                  <span className="t-small t-muted"> · {hint}</span>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="t-tool">
        <label className="t-check">
          <input type="checkbox" checked={value.openBook} onChange={(e) => onChange({ ...value, openBook: e.target.checked })} />
          <span>
            <b>Open book</b>
            <span className="t-small t-muted">
              {" "}
              · students upload up to 5 files (PDF, images, text; 20 MB in all) before they start, and read them in the exam. You
              can see their files in Results.
            </span>
          </span>
        </label>
        {value.openBook && (
          <div className="t-tool-sub">
            <label className="t-inline-field">
              <span>Students can upload their files</span>
              <select
                className="input t-select-sm"
                value={uploadDays}
                onChange={(e) => onChange({ ...value, uploadOpensDays: Number(e.target.value) })}
                aria-describedby="upload-days-hint"
                data-testid="upload-days"
              >
                {UPLOAD_DAYS.map((n) => (
                  <option key={n} value={n}>
                    {n === 0 ? "only on exam day" : n === 1 ? "1 day before the exam" : `${n} days before the exam`}
                  </option>
                ))}
              </select>
            </label>
            <span id="upload-days-hint" className="t-small t-muted">
              {uploadDays === 0
                ? "On exam day students add their files right before they start."
                : hasDate
                  ? `The exam shows on students' pages ${uploadDays === 1 ? "1 day" : `${uploadDays} days`} before its date with only an upload area (no questions). Uploads close at the exam's start time, or when you open it.`
                  : "Set the exam's date and time above: uploads open that many days before it."}
            </span>
          </div>
        )}
      </div>

      <div className="t-tool">
        <label className="t-check">
          <input
            type="checkbox"
            checked={sitesOn || value.websites.length > 0}
            onChange={(e) => {
              setSitesOn(e.target.checked);
              if (!e.target.checked) onChange({ ...value, websites: [] });
            }}
          />
          <span>
            <b>Allowed websites</b>
            <span className="t-small t-muted"> · opened in a side panel; links to other sites are blocked</span>
          </span>
        </label>
        {(sitesOn || value.websites.length > 0) && (
          <div className="t-tool-sub">
            {entries.length > 0 && (
              <ul className="t-sites" aria-label="Allowed websites">
                {entries.map(({ line, host: h, url, title }) => {
                  const comp = checks[h]?.companions;
                  const showComp = !!comp && comp.sites.length > 0 && !shownCompanions.has(h);
                  if (showComp) shownCompanions.add(h);
                  return (
                    <li key={line}>
                      <span className="t-site-main">
                        {url ? (
                          <>
                            <span className="t-site-title">{title ?? shortLink(url)}</span>
                            <span className="mono t-small t-muted">
                              {title ? `${shortLink(url)} · ` : ""}starts here; all of {h} is allowed
                            </span>
                          </>
                        ) : (
                          <span className="mono">{h}</span>
                        )}
                      </span>
                      {checks[h]?.frameable === false && (
                        <span className="t-site-warn" role="note" data-testid="site-warn">
                          Can&apos;t be shown inside the exam. {checks[h]!.reason}
                        </span>
                      )}
                      {showComp && (
                        <span className="t-site-companions" role="note" data-testid="site-companions">
                          Also opened so {comp!.name} works: <span className="mono">{comp!.sites.join(", ")}</span>
                        </span>
                      )}
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        aria-label={`Remove ${url ? (title ?? shortLink(url)) : h}`}
                        onClick={() => onChange({ ...value, websites: value.websites.filter((x) => x !== line) })}
                      >
                        Remove
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="t-sites-add">
              <label htmlFor="exam-site" className="sr-only">
                Website to allow
              </label>
              <input
                id="exam-site"
                className="input mono"
                value={site}
                placeholder="docs.python.org or https://duke.app.box.com/folder/0"
                maxLength={2100}
                onChange={(e) => {
                  setSite(e.target.value);
                  setSiteError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addSite();
                  }
                }}
                aria-invalid={!!siteError}
                aria-describedby={siteError ? "exam-site-err" : "exam-site-hint"}
                disabled={value.websites.length >= MAX_WEBSITES}
              />
              <button type="button" className="btn btn-secondary" onClick={addSite} disabled={value.websites.length >= MAX_WEBSITES}>
                Add
              </button>
            </div>
            {siteError ? (
              <span id="exam-site-err" className="field-error">
                {siteError}
              </span>
            ) : (
              <span id="exam-site-hint" className="t-small t-muted">
                {/* 2026-10-23 (review): entries match exactly; "*." for a whole site */}
                docs.python.org — exactly this site; *.python.org — all of python.org; a full link (like a Duke Box folder) opens
                first and allows every page of its site. A title may go before a link. Up to {MAX_WEBSITES} sites.
              </span>
            )}
          </div>
        )}
      </div>

      {boxLinkSet !== undefined && (
        <p className="t-small t-muted" data-testid="box-folder-status" style={{ margin: 0 }}>
          Duke Box folder: {boxLinkSet ? "set" : "not set"}
        </p>
      )}
    </section>
  );
}
