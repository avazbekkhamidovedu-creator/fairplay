// 2026-10-23 (exam tools): "Tools for students" in the exam editor. Simple tick boxes:
// a calculator (scientific or graphing), open book (students upload their files before
// the exam), allowed websites (a list with Add / Remove). Outside apps are gone: every app
// is closed when the computer locks, these tools are built into FairPlay.
import { useEffect, useState } from "react";
import { toolsApi, type SiteCheck } from "../../api/client";
import type { CalculatorMode } from "../../api/types";
import { MAX_WEBSITES, normalizeWebsite } from "../../lib/websites";
import "../../styles/tools.css";

export interface ExamToolsValue {
  calculator: CalculatorMode;
  openBook: boolean;
  websites: string[];
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

export function ExamToolsSettings({ value, onChange }: { value: ExamToolsValue; onChange: (v: ExamToolsValue) => void }) {
  const [site, setSite] = useState("");
  const [siteError, setSiteError] = useState<string | null>(null);
  // the website list stays visible while ticked, even when it is still empty
  const [sitesOn, setSitesOn] = useState(value.websites.length > 0);
  const calcOn = value.calculator !== "off";
  const checks = useSiteChecks(value.websites);

  const addSite = () => {
    const r = normalizeWebsite(site);
    if (!r.ok) {
      setSiteError(r.error);
      return;
    }
    if (value.websites.includes(r.host)) {
      setSiteError(`${r.host} is already on the list.`);
      return;
    }
    if (value.websites.length >= MAX_WEBSITES) {
      setSiteError(`At most ${MAX_WEBSITES} websites.`);
      return;
    }
    onChange({ ...value, websites: [...value.websites, r.host] });
    setSite("");
    setSiteError(null);
  };

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
            {value.websites.length > 0 && (
              <ul className="t-sites" aria-label="Allowed websites">
                {value.websites.map((h) => (
                  <li key={h}>
                    <span className="mono">{h}</span>
                    {checks[h]?.frameable === false && (
                      <span className="t-site-warn" role="note" data-testid="site-warn">
                        Can&apos;t be shown inside the exam. {checks[h]!.reason}
                      </span>
                    )}
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      aria-label={`Remove ${h}`}
                      onClick={() => onChange({ ...value, websites: value.websites.filter((x) => x !== h) })}
                    >
                      Remove
                    </button>
                  </li>
                ))}
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
                placeholder="docs.python.org"
                maxLength={260}
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
                docs.python.org — exactly this site; *.python.org — all of python.org. Up to {MAX_WEBSITES}.
              </span>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
