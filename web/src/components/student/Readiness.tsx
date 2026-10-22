import { useCallback, useEffect, useState } from "react";
import { studentApi } from "../../api/client";
import * as bridge from "../../lib/bridge";
import { AlertIcon, CheckIcon, DotsIcon } from "./icons";

export interface Readiness {
  /** Running inside the FairPlay app (Mac, Windows or iPad). */
  native: boolean;
  /** Which app: "mac" | "windows" | "ipad" (null in a browser). */
  platform: "mac" | "windows" | "ipad" | null;
  /** Accessibility (lock) permission: true / false, or null while unknown. */
  trusted: boolean | null;
  /** 2026-10-22 (Mac): a newer FairPlay the app can install (null/missing = none). */
  update?: bridge.AppUpdate | null;
}

/** What we can truly know about this computer: Mac app present + lock permission. */
export function useReadiness(): Readiness {
  const [state, setState] = useState(() => bridge.getNativeState());
  useEffect(() => {
    const off = bridge.onNative((msg) => {
      if (msg.type === "state") setState(bridge.getNativeState());
    });
    // Ask again: the answer to the app-level hello may have arrived before we listened.
    bridge.hello();
    return off;
  }, []);
  const native = bridge.isNative();
  return {
    native,
    platform: bridge.nativePlatform(),
    trusted: native && state ? state.trusted : null,
    update: native && state?.update ? state.update : null,
  };
}

type UpdateRun = { kind: "idle" } | { kind: "installing" } | { kind: "failed"; reason: bridge.UpdateFailure };

/**
 * 2026-10-22: "FairPlay X is available · Update now" above the device checks (Mac app).
 * The app downloads and verifies the update, replaces itself and reopens; this line only
 * shows progress and, if that fails, what to do instead.
 */
export function UpdateNotice({ update }: { update: bridge.AppUpdate }) {
  const [run, setRun] = useState<UpdateRun>({ kind: "idle" });
  useEffect(
    () =>
      bridge.onNative((msg) => {
        if (msg.type === "updateProgress") setRun({ kind: "installing" });
        else if (msg.type === "updateFailed") {
          const reason = bridge.parseUpdateFailure(msg.reason);
          // "busy": an install is already running; keep showing it.
          setRun(reason === "busy" ? { kind: "installing" } : { kind: "failed", reason });
        }
      }),
    [],
  );

  function start() {
    if (bridge.installUpdate()) setRun({ kind: "installing" });
  }

  if (run.kind === "installing") {
    return (
      <p className="stu-dev-line stu-update" role="status" data-testid="update-notice">
        <DotsIcon size={16} />
        <span>Updating… FairPlay restarts by itself.</span>
      </p>
    );
  }
  const retry = run.kind === "idle" || run.reason === "download";
  return (
    <div className="stu-update-box" data-testid="update-notice">
      <p className="stu-dev-line stu-update" role="status">
        <span className="stu-update-text">FairPlay {update.version} is available</span>
        {retry && (
          <button type="button" className="stu-btn sm" onClick={start}>
            Update now
          </button>
        )}
      </p>
      {run.kind === "failed" && (
        <p className="stu-dev-sub" role="alert">
          {run.reason === "download" ? (
            "Couldn't download the update. Check the connection and try again."
          ) : run.reason === "locked" ? (
            "Can't update during an exam."
          ) : (
            <>
              Download the new version from the{" "}
              <a
                href={bridge.DOWNLOAD_PAGE}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => {
                  if (bridge.openDownloadPage()) e.preventDefault();
                }}
              >
                download page
              </a>
              .
            </>
          )}
        </p>
      )}
    </div>
  );
}

type Tone = "ok" | "warn" | "wait" | "info";

const SR_PREFIX: Record<Tone, string> = { ok: "Ready: ", warn: "Needs attention: ", wait: "Checking: ", info: "Note: " };

function CheckRow({ tone, title, detail }: { tone: Tone; title: string; detail?: string }) {
  return (
    <li className={`stu-check${tone === "ok" ? " ok" : tone === "warn" ? " warn" : ""}`}>
      {tone === "ok" ? <CheckIcon /> : tone === "warn" ? <AlertIcon /> : <DotsIcon />}
      <span>
        <span className="sr-only">{SR_PREFIX[tone]}</span>
        {title}
        {detail && <span className="stu-check-detail">{detail}</span>}
      </span>
    </li>
  );
}

/** The checks list. Only shows checks we can actually verify from the web page. */
export function ReadinessChecks({ readiness }: { readiness: Readiness }) {
  const { native, trusted, platform } = readiness;
  const app =
    platform === "windows" ? "FairPlay Windows app"
    : platform === "mac" ? "FairPlay Mac app"
    : platform === "ipad" ? "FairPlay iPad app"
    : "FairPlay app";
  return (
    <ul className="stu-checks" aria-label="Laptop checks">
      {native ? (
        <CheckRow tone="ok" title={`Running in the ${app}`} />
      ) : (
        <CheckRow
          tone="warn"
          title="Not running in the FairPlay app"
          detail="You are in a normal browser. Use the FairPlay app for real exams."
        />
      )}
      {native && platform === "ipad" && (
        <CheckRow tone="wait" title="Assessment Mode starts with the exam" detail="iPadOS asks you to allow it. Tap Yes." />
      )}
      {native && platform !== "windows" && platform !== "ipad" &&
        (trusted === true ? (
          <CheckRow tone="ok" title="Lock permission granted" />
        ) : trusted === false ? (
          <CheckRow
            tone="warn"
            title="Lock permission is off"
            detail="Open System Settings → Privacy & Security → Accessibility and turn on FairPlay."
          />
        ) : (
          <CheckRow tone="wait" title="Checking lock permission…" />
        ))}
    </ul>
  );
}

export function readinessOk(r: Readiness): boolean {
  // The Windows and iPad apps have no separate lock permission to grant.
  return r.native && (r.platform === "windows" || r.platform === "ipad" || r.trusted === true);
}

type RunState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; report: bridge.PreflightReport; saved: boolean }
  | { kind: "noAnswer" };

/** S1: run the app's device check on demand, show every row and store the result for the
 *  teacher (POST /api/student/device-check). Only inside the app. */
export function DeviceCheckRun({ examId = null }: { examId?: string | null }) {
  const [state, setState] = useState<RunState>({ kind: "idle" });
  if (!bridge.isNative()) return null;

  async function run() {
    setState({ kind: "running" });
    const report = await bridge.preflightReport();
    if (!report) {
      setState({ kind: "noAnswer" });
      return;
    }
    let saved = false;
    try {
      await studentApi.deviceCheck({
        platform: report.platform,
        app_version: report.app_version,
        os_version: report.os_version,
        checks: report.checks,
        exam_id: examId,
      });
      saved = true;
    } catch {
      /* offline: the rows still help the student; the teacher sees the next check */
    }
    setState({ kind: "done", report, saved });
  }

  return (
    <div className="stu-device-check" data-testid="device-check">
      <button type="button" className="stu-btn" onClick={() => void run()} disabled={state.kind === "running"}>
        {state.kind === "running" ? "Checking…" : state.kind === "done" ? "Check again" : "Run check"}
      </button>
      {state.kind === "noAnswer" && (
        <p className="stu-hint" role="status">
          This version of FairPlay cannot run the full check. The exam still checks before it locks.
        </p>
      )}
      {state.kind === "done" && <DeviceCheckRows report={state.report} saved={state.saved} />}
    </div>
  );
}

export function DeviceCheckRows({ report, saved }: { report: bridge.PreflightReport; saved: boolean }) {
  // Older apps and Windows builds send only `problems`: show those as rows.
  const rows = report.checks.length
    ? report.checks
    : report.problems.map((p, i) => ({ id: `problem_${i}`, ok: false, message: p, info: false }));
  return (
    <>
      <ul className="stu-checks" aria-label="Device check results">
        {rows.length === 0 && <CheckRow tone="ok" title="Everything looks ready" />}
        {rows.map((c) => (
          <CheckRow
            key={c.id}
            tone={c.ok ? "ok" : c.info ? "info" : "warn"}
            title={c.message || c.id}
            detail={c.info && !c.ok ? "Just so you know: this does not stop the exam." : undefined}
          />
        ))}
      </ul>
      <p className="stu-hint" role="status">
        {saved ? "Your teacher can see this check." : "Could not send this check to your teacher. Try again when online."}
      </p>
    </>
  );
}

function deviceWord(platform: string | null): string {
  return platform === "windows" ? "PC" : platform === "ipad" ? "iPad" : platform === "mac" ? "Mac" : "computer";
}

/**
 * 2026-10-22: "Test my device" card (like Bluebook): the check runs by itself, then ONE line
 * says whether this device is ready. Only problems are spelled out; notes (battery, VPN) are
 * one small line each; every row is behind "Show all checks". The result is stored for the
 * teacher as with DeviceCheckRun.
 */
export function DeviceStatus({ readiness }: { readiness: Readiness }) {
  const [state, setState] = useState<RunState>({ kind: "idle" });
  const [showAll, setShowAll] = useState(false);
  const device = deviceWord(readiness.platform);

  const run = useCallback(async () => {
    setState({ kind: "running" });
    const report = await bridge.preflightReport();
    if (!report) {
      setState({ kind: "noAnswer" });
      return;
    }
    let saved = false;
    try {
      await studentApi.deviceCheck({
        platform: report.platform,
        app_version: report.app_version,
        os_version: report.os_version,
        checks: report.checks,
        exam_id: null,
      });
      saved = true;
    } catch {
      /* offline: the result still helps the student */
    }
    setState({ kind: "done", report, saved });
  }, []);

  useEffect(() => {
    if (readiness.native) void run();
  }, [readiness.native, run]);

  if (!readiness.native) {
    return (
      <p className="stu-dev-line warn" role="status">
        <AlertIcon size={16} />
        <span>Open the FairPlay app to test this computer.</span>
      </p>
    );
  }
  if (state.kind === "idle" || state.kind === "running") {
    return (
      <p className="stu-dev-line" role="status">
        <DotsIcon size={16} />
        <span>Checking this {device}…</span>
      </p>
    );
  }
  if (state.kind === "noAnswer") {
    return (
      <p className="stu-dev-line" role="status">
        <span>This version of FairPlay can't run the full check. The exam still checks before it locks.</span>
      </p>
    );
  }

  const { report, saved } = state;
  const rows = report.checks.length
    ? report.checks
    : report.problems.map((p, i) => ({ id: `problem_${i}`, ok: false, message: p, info: false }));
  const problems = rows.filter((c) => !c.ok && !c.info);
  const notes = rows.filter((c) => !c.ok && c.info);
  const passed = rows.filter((c) => c.ok).length;
  const apps = report.apps_to_close.length;

  return (
    <div className="stu-dev" data-testid="device-status">
      {problems.length === 0 ? (
        <p className="stu-dev-line ok" role="status">
          <CheckIcon size={16} />
          <span>
            This {device} is ready
            {passed > 0 && <span className="stu-dev-sub"> · {passed} checks passed</span>}
          </span>
        </p>
      ) : (
        <>
          <p className="stu-dev-line warn" role="status">
            <AlertIcon size={16} />
            <span>{problems.length === 1 ? "1 thing to fix" : `${problems.length} things to fix`}</span>
          </p>
          <ul className="stu-dev-list" aria-label="Things to fix">
            {problems.map((c) => {
              const pane = readiness.platform === "mac" ? bridge.settingsPaneFor(c.message) : null;
              return (
                <li key={c.id}>
                  {c.message || c.id}
                  {pane && (
                    <>
                      {" "}
                      <button type="button" className="stu-linkbtn" onClick={() => bridge.openSettings(pane)}>
                        Open settings
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      {(notes.length > 0 || apps > 0) && (
        <ul className="stu-dev-notes" aria-label="Notes">
          {notes.map((c) => (
            <li key={c.id}>{c.message || c.id}</li>
          ))}
          {apps > 0 && <li>{apps === 1 ? "1 app will close" : `${apps} apps will close`} when an exam starts.</li>}
        </ul>
      )}
      {showAll && (
        <ul className="stu-dev-all" aria-label="All checks">
          {rows.map((c) => (
            <li key={c.id} className={c.ok ? "ok" : c.info ? "" : "warn"}>
              {c.ok ? <CheckIcon size={14} /> : c.info ? <DotsIcon size={16} /> : <AlertIcon size={14} />}
              <span>{c.message || c.id}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="stu-dev-actions">
        <button type="button" className="stu-linkbtn" onClick={() => void run()}>
          Check again
        </button>
        <button type="button" className="stu-linkbtn" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
          {showAll ? "Hide checks" : "Show all checks"}
        </button>
      </div>
      {!saved && <p className="stu-dev-sub">Couldn't send this check to your teacher. It will be sent next time.</p>}
    </div>
  );
}
