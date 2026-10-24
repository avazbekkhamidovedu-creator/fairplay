// 2026-10-23 (exam tools): "Your materials" on Check-in and "Before you start" of an
// open-book exam. Drag files in or choose them; the list shows each file's size with
// Remove. Up to 5 files, 20 MB in all (2026-10-23 review: was 50), PDF / PNG / JPEG / TXT / MD. Locked once the exam
// has started (the server says so: can_change false).
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { ApiError, NetworkError, materialsApi } from "../../api/client";
import type { MaterialsInfo } from "../../api/types";
import { ACCEPT, MAX_BYTES, MAX_FILES, MAX_LABEL, checkFile, formatBytes } from "../../lib/materials";
import { pdfNeedsPassword } from "../../lib/pdf";
import { dateTime, timeLeft } from "./format";
import { FileIcon, UploadIcon } from "./icons";
import "../../styles/tools.css";

const KIND_LABEL: Record<string, string> = { pdf: "PDF", png: "PNG", jpg: "JPG", txt: "TXT", md: "MD" };

export function MaterialsUpload({ examId, compact = false }: { examId: string; compact?: boolean }) {
  const [info, setInfo] = useState<MaterialsInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    try {
      setInfo(await materialsApi.list(examId));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof NetworkError ? "Can't reach the server. Your materials will show when you're back online." : e instanceof ApiError ? e.detail : "Your materials can't be shown right now.");
    }
  }, [examId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const add = async (list: FileList | File[] | null) => {
    if (!list || !info) return;
    const files = Array.from(list);
    const problems: string[] = [];
    let count = info.files.length;
    let used = info.used_bytes;
    for (const f of files) {
      const why = checkFile(f, count, used);
      if (why) {
        problems.push(why);
        continue;
      }
      setBusy(f.name);
      // 2026-10-23 review: a PDF that needs a password can't be opened in the exam
      if (/\.pdf$/i.test(f.name) && (await pdfNeedsPassword(f).catch(() => false))) {
        problems.push(`${f.name} is password-protected. Upload a copy without a password.`);
        continue;
      }
      try {
        await materialsApi.upload(examId, f);
        count += 1;
        used += f.size;
      } catch (e) {
        problems.push(
          e instanceof NetworkError
            ? `${f.name}: can't reach the server. Try again.`
            : e instanceof ApiError
              ? `${f.name}: ${e.detail}`
              : `${f.name}: upload failed.`,
        );
      }
    }
    setBusy(null);
    setErrors(problems);
    if (inputRef.current) inputRef.current.value = "";
    await reload();
  };

  const remove = async (id: string, name: string) => {
    setBusy(name);
    try {
      await materialsApi.remove(examId, id);
      setErrors([]);
    } catch (e) {
      setErrors([e instanceof ApiError ? e.detail : `${name} could not be removed. Try again.`]);
    }
    setBusy(null);
    await reload();
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    if (info?.can_change && !busy) void add(e.dataTransfer?.files ?? null);
  };

  // 2026-10-24: the early upload window: "Uploads close Oct 26, 14:00 · in 2 days 3 h", ticking;
  // when it closes, the list is asked again (the server then says the files are fixed)
  const closesAt = info?.upload_closes_at ?? null;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!closesAt || !info?.can_change) return;
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      if (n >= new Date(closesAt).getTime()) void reload();
    }, 30_000);
    return () => clearInterval(t);
  }, [closesAt, info?.can_change, reload]);

  const used = info?.used_bytes ?? 0;
  const canChange = !!info?.can_change;
  const full = !!info && info.files.length >= (info.max_files || MAX_FILES);

  return (
    <section className={`stu-upload${compact ? " compact" : ""}`} aria-labelledby={`mat-h-${examId}`} data-testid="materials-upload">
      <div className="stu-upload-head">
        <h2 id={`mat-h-${examId}`}>Your materials</h2>
        <span className="stu-muted stu-small">Open book: bring up to {MAX_FILES} files (PDF, PNG, JPEG, TXT, MD), {MAX_LABEL} in all.</span>
      </div>
      {info && closesAt && canChange && (
        <p className="stu-upload-window" data-testid="upload-window">
          <b>Uploads close {dateTime(closesAt)}</b>
          {timeLeft(closesAt, now) && <span className="stu-muted"> · in {timeLeft(closesAt, now)}</span>}
        </p>
      )}
      {loadError && (
        <p className="stu-alert warn" role="alert" style={{ margin: 0 }}>
          {loadError}
        </p>
      )}
      {info && canChange && (
        <div
          className={`stu-drop${over ? " over" : ""}${full ? " full" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          data-testid="materials-drop"
        >
          <UploadIcon />
          <span>{full ? "That's 5 files. Remove one to add another." : "Drag files here, or"}</span>
          {!full && (
            <>
              <button type="button" className="stu-btn sm" onClick={() => inputRef.current?.click()} disabled={!!busy}>
                Choose files
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPT}
                hidden
                aria-label="Choose files to upload"
                data-testid="materials-input"
                onChange={(e) => void add(e.target.files)}
              />
            </>
          )}
        </div>
      )}
      {info && !canChange && info.reason && (
        <p className="stu-muted stu-small" style={{ margin: 0 }}>
          {info.reason}.
        </p>
      )}
      {busy && (
        <p className="stu-muted stu-small" role="status" style={{ margin: 0 }}>
          Working on {busy}…
        </p>
      )}
      {errors.length > 0 && (
        <ul className="stu-alert error stu-upload-errors" role="alert">
          {errors.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      {info && info.files.length > 0 && (
        <ul className="stu-filelist" aria-label="Uploaded materials">
          {info.files.map((f) => (
            <li key={f.id} className="stu-filerow static">
              <FileIcon size={22} label={KIND_LABEL[f.kind]} />
              <span className="stu-filerow-name">{f.name}</span>
              <span className="stu-filerow-size">{formatBytes(f.size)}</span>
              {canChange && (
                <button
                  type="button"
                  className="stu-linkbtn"
                  onClick={() => void remove(f.id, f.name)}
                  disabled={!!busy}
                  aria-label={`Remove ${f.name}`}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {info && (
        <div className="stu-upload-meter" aria-label={`${info.files.length} of ${MAX_FILES} files, ${formatBytes(used)} of ${MAX_LABEL}`}>
          <span style={{ width: `${Math.min(100, (used / (info.max_bytes || MAX_BYTES)) * 100)}%` }} />
          <small>
            {info.files.length} of {MAX_FILES} files · {formatBytes(used)} of {MAX_LABEL}
          </small>
        </div>
      )}
    </section>
  );
}
