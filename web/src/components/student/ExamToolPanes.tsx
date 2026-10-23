// 2026-10-23 (exam tools): what the side pane shows. Materials: the student's own uploaded
// files (the list fetched once; a file's bytes when it is opened, then kept in memory for
// re-opens, see lib/materials). Websites: the exam's allowed
// sites in a sandboxed frame with our own Back / Forward / Reload; the address is shown,
// never editable. The apps only let those sites load in sub-frames (2026-10-23 review: each
// entry exactly; "*.python.org" is shown as "python.org (all pages)" and opens python.org).
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { MaterialFile } from "../../api/types";
import { formatBytes, isOfflineError, loadAttemptMaterials, materialBlob } from "../../lib/materials";
import { siteLabel, siteUrl } from "../../lib/websites";
import { MaterialViewer } from "./MaterialViewer";
import { ArrowLeftIcon, ArrowRightIcon, FileIcon, GlobeIcon, ReloadIcon } from "./icons";
import "../../styles/tools.css";

const KIND_LABEL: Record<MaterialFile["kind"], string> = { pdf: "PDF", png: "PNG", jpg: "JPG", txt: "TXT", md: "MD" };
export const OFFLINE_MATERIALS = "Materials need the connection. Your answers keep saving on this computer.";

export function MaterialsPaneContent({ attemptId }: { attemptId: string | null }) {
  const [files, setFiles] = useState<MaterialFile[] | null>(null);
  const [problem, setProblem] = useState<string | null>(attemptId ? null : OFFLINE_MATERIALS);
  const [open, setOpen] = useState<MaterialFile | null>(null);

  useEffect(() => {
    if (!attemptId) {
      setProblem(OFFLINE_MATERIALS);
      return;
    }
    let alive = true;
    setProblem(null);
    loadAttemptMaterials(attemptId).then(
      (f) => alive && setFiles(f),
      (e) =>
        alive &&
        setProblem(isOfflineError(e) ? OFFLINE_MATERIALS : e instanceof ApiError ? e.detail : "Materials can't be shown right now."),
    );
    return () => {
      alive = false;
    };
  }, [attemptId]);

  if (open && attemptId)
    return (
      <div className="stu-pane-stack">
        <div className="stu-pane-sub">
          <button type="button" className="stu-btn sm" onClick={() => setOpen(null)}>
            <ArrowLeftIcon size={14} />
            All files
          </button>
          <span className="stu-pane-subtitle" title={open.name}>
            {open.name}
          </span>
        </div>
        <MaterialViewer file={open} load={() => materialBlob(attemptId, open.id)} />
      </div>
    );
  if (problem)
    return (
      <p className="stu-alert warn" role="status" data-testid="materials-offline">
        {problem}
      </p>
    );
  if (!files)
    return (
      <p className="stu-muted" role="status">
        Loading your materials…
      </p>
    );
  if (files.length === 0)
    return <p className="stu-muted">You did not upload any materials for this exam.</p>;
  return (
    <ul className="stu-filelist" aria-label="Your materials">
      {files.map((f) => (
        <li key={f.id}>
          <button type="button" className="stu-filerow" onClick={() => setOpen(f)}>
            <FileIcon size={22} label={KIND_LABEL[f.kind]} />
            <span className="stu-filerow-name">{f.name}</span>
            <span className="stu-filerow-size">{formatBytes(f.size)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A site that has not loaded in the frame after this long gets the friendly message. */
export const FRAME_TIMEOUT_MS = 10_000;
export const BLANK_HINT = "Blank page? This site doesn't allow being shown inside the exam.";

export function WebsitesPaneContent({ sites }: { sites: string[] }) {
  // Our own history of what we opened (the frame's own navigation is on another origin).
  const [history, setHistory] = useState<string[]>([]);
  const [index, setIndex] = useState(-1);
  const [reload, setReload] = useState(0);
  // "All sites" shows the list again without losing the history
  const [listOpen, setListOpen] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [slow, setSlow] = useState(false);
  const current = index >= 0 ? history[index] : undefined;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!current) return;
    setLoaded(false);
    setSlow(false);
    timer.current = setTimeout(() => setSlow(true), FRAME_TIMEOUT_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [current, index, reload]);

  const openSite = (host: string) => {
    const url = siteUrl(host);
    setHistory((h) => [...h.slice(0, index + 1), url]);
    setIndex((i) => i + 1);
    setListOpen(false);
  };

  if (!current || listOpen)
    return (
      <div className="stu-pane-stack">
        <p className="stu-muted stu-small" style={{ margin: 0 }}>
          Your teacher allows these websites. Links to other sites are blocked.
        </p>
        <ul className="stu-filelist" aria-label="Allowed websites">
          {sites.map((host) => (
            <li key={host}>
              <button type="button" className="stu-filerow" onClick={() => openSite(host)}>
                <GlobeIcon size={20} />
                <span className="stu-filerow-name">{siteLabel(host)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    );

  let host = "";
  try {
    host = new URL(current).host;
  } catch {
    host = current;
  }
  return (
    <div className="stu-pane-stack stu-web">
      <div className="stu-web-bar">
        <button type="button" className="stu-icon-btn" aria-label="Back" disabled={index <= 0} onClick={() => setIndex((i) => Math.max(0, i - 1))}>
          <ArrowLeftIcon size={16} />
        </button>
        <button
          type="button"
          className="stu-icon-btn"
          aria-label="Forward"
          disabled={index >= history.length - 1}
          onClick={() => setIndex((i) => Math.min(history.length - 1, i + 1))}
        >
          <ArrowRightIcon size={16} />
        </button>
        <button type="button" className="stu-icon-btn" aria-label="Reload" onClick={() => setReload((r) => r + 1)}>
          <ReloadIcon />
        </button>
        <span className="stu-web-host" data-testid="website-host" title={current}>
          {host}
        </span>
        <button type="button" className="stu-btn sm" onClick={() => setListOpen(true)}>
          All sites
        </button>
      </div>
      <div className="stu-web-frame">
        <iframe
          key={`${index}-${reload}`}
          src={current}
          title={`Website: ${host}`}
          sandbox="allow-scripts allow-same-origin allow-forms"
          referrerPolicy="no-referrer"
          onLoad={() => {
            setLoaded(true);
            setSlow(false);
            if (timer.current) clearTimeout(timer.current);
          }}
        />
        {slow && !loaded && (
          <div className="stu-web-blocked" role="status" data-testid="website-blocked">
            <GlobeIcon size={28} />
            <p className="stu-pop-title">This site can&apos;t be shown inside the exam</p>
            <p className="stu-muted">It may not allow being shown in other pages, or the connection is slow.</p>
            <button type="button" className="stu-btn sm" onClick={() => setReload((r) => r + 1)}>
              Try again
            </button>
          </div>
        )}
      </div>
      {/* 2026-10-23 review: a site that refuses to be framed still fires "load" (for the
          browser's own error page), so the frame looks blank without any message. */}
      <p className="stu-web-hint" data-testid="website-hint">
        {BLANK_HINT}
      </p>
    </div>
  );
}
