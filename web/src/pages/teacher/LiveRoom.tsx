import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { NetworkError, teacherApi } from "../../api/client";
import type { Exam, LiveFlag, LiveRoom as LiveRoomData, LiveStudent, ReadinessRow } from "../../api/types";
import { errorText, eventDetailText, eventLabel, plural } from "../../components/teacher/util";
import { clockOffset, formatClock, formatCountdown } from "../../lib/timer";
import { OpenToEditor, closesAtLabel } from "../../components/teacher/OpenTo";

export const LIVE_POLL_MS = 3000;

export type SeatState = "writing" | "done" | "offline" | "look";

/** Writing, but the FairPlay app itself is silent (killed, or the page runs outside it). */
export function appSilent(s: LiveStudent): boolean {
  return s.status === "in_progress" && s.online && s.app_online === false;
}

export function seatState(s: LiveStudent): SeatState {
  if (s.needs_look) return "look";
  if (s.status === "submitted") return "done";
  if (!s.online) return "offline";
  if (appSilent(s)) return "look";
  return "writing";
}

const SEAT_LABEL: Record<SeatState, string> = { writing: "Writing", done: "Finished", offline: "Offline", look: "Needs your look" };

function seatSub(s: LiveStudent, st: SeatState): string {
  if (s.status === "submitted") {
    const done = s.late ? "Done late" : "Done";
    return st === "look" ? `${done} · look` : done;
  }
  if (st === "offline") return `Q${s.current_question + 1} · saved`;
  if (s.client === "browser") return "Browser · not locked";
  if (appSilent(s)) return "App silent";
  if (s.status === "in_progress" && s.online && s.app_online && !s.locked) return "Not locked";
  if (st === "look") return `Q${s.current_question + 1} · look`;
  return `Q${s.current_question + 1} · ${s.answered}/${s.question_count}`;
}

/** Room counts. Students whose FairPlay app is silent count as "need your look". */
export function countsOf(live: LiveRoomData): LiveRoomData["counts"] {
  if (live.counts) {
    const silent = live.students.filter((s) => !s.needs_look && appSilent(s)).length;
    return { ...live.counts, writing: Math.max(0, live.counts.writing - silent), needs_look: live.counts.needs_look + silent };
  }
  const st = live.students;
  return {
    writing: st.filter((s) => s.status === "in_progress" && s.online).length,
    submitted: st.filter((s) => s.status === "submitted").length,
    offline: st.filter((s) => s.status === "in_progress" && !s.online).length,
    needs_look: st.filter((s) => s.needs_look).length,
  };
}

/** Time until the last writing student must finish, or null if nobody is writing. */
export function latestDeadlineMs(students: LiveStudent[], now: number): number | null {
  const writing = students.filter((s) => s.status === "in_progress");
  if (writing.length === 0) return null;
  const latest = Math.max(...writing.map((s) => new Date(s.deadline_at).getTime()).filter(Number.isFinite));
  return Number.isFinite(latest) ? Math.max(0, latest - now) : null;
}

/** A locked Mac that cannot reach the server shows a "Mac code" (Ctrl-Opt-Cmd-E,
 *  "Offline code…"); this turns it into the 8-digit code that unlocks it. */
export function OfflineExitBox({ examId, students }: { examId: string; students: LiveStudent[] }) {
  const [open, setOpen] = useState(false);
  const [aid, setAid] = useState("");
  const [macCode, setMacCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const writing = students.filter((s) => s.status === "in_progress");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCodes(null);
    try {
      const r = await teacherApi.offlineExit(examId, aid, macCode);
      setCodes([r.code]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="t-dark-box">
        <span className="t-dark-box-label">
          Mac offline or stuck?
          <button type="button" className="t-mini-link" onClick={() => setOpen(true)}
            title="For a locked Mac that cannot reach the server: it shows a Mac code after Ctrl-Opt-Cmd-E → Offline code">
            offline exit
          </button>
        </span>
      </div>
    );
  }
  return (
    <form className="t-dark-box" onSubmit={submit} aria-label="Offline exit code">
      <span className="t-dark-box-label">
        Offline exit · pick the student, type the Mac code{" "}
        <button type="button" className="t-mini-link" onClick={() => { setOpen(false); setCodes(null); setMacCode(""); setAid(""); setError(null); }}>
          close
        </button>
      </span>
      <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <select
          aria-label="Student"
          value={aid}
          onChange={(e) => {
            setAid(e.target.value);
            setCodes(null);
          }}
          style={{ fontFamily: "inherit", maxWidth: 180 }}
        >
          <option value="">Student…</option>
          {writing.map((s) => (
            <option key={s.attempt_id} value={s.attempt_id}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Mac code"
          placeholder="K7QM-3XPA"
          value={macCode}
          maxLength={20}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setMacCode(e.target.value.toUpperCase())}
          style={{ width: 120, fontFamily: "inherit", textTransform: "uppercase" }}
        />
        <button type="submit" className="t-mini-link" disabled={busy || !aid || macCode.trim().length < 8}>
          {busy ? "…" : "get code"}
        </button>
      </span>
      {codes && (
        <div className="t-code t-code-sm" title="Type this on the locked Mac. The Mac logs the exit.">
          {codes.join(" / ")}
        </div>
      )}
      {error && <span className="t-small" style={{ color: "var(--warn)" }}>{error}</span>}
    </form>
  );
}

interface Props {
  exam: Exam;
  onClosed: (exam: Exam) => void;
  onExamChange: (exam: Exam) => void;
}

export function LiveRoom({ exam, onClosed, onExamChange }: Props) {
  const [live, setLive] = useState<LiveRoomData | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [closing, setClosing] = useState(false);
  const [extending, setExtending] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [offset, setOffset] = useState(0);

  const load = useCallback(async () => {
    try {
      const data = await teacherApi.live(exam.id);
      setLive(data);
      if (data.server_time) setOffset(clockOffset(data.server_time));
      setPollError(null);
    } catch (e) {
      setPollError(e instanceof NetworkError ? "Connection lost. Retrying every 3 seconds…" : errorText(e));
    }
  }, [exam.id]);

  useEffect(() => {
    void load();
    const poll = setInterval(load, LIVE_POLL_MS);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [load]);

  async function close() {
    setClosing(true);
    setActionError(null);
    try {
      const updated = await teacherApi.closeExam(exam.id);
      setConfirming(false);
      onClosed(updated);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setClosing(false);
    }
  }

  async function rotate() {
    setRotating(true);
    setActionError(null);
    try {
      const updated = await teacherApi.rotateCode(exam.id);
      onExamChange(updated);
      await load();
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setRotating(false);
    }
  }

  async function extend() {
    setExtending(true);
    setActionError(null);
    try {
      const updated = await teacherApi.extend(exam.id, 10);
      onExamChange(updated);
      await load();
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setExtending(false);
    }
  }

  const students = live?.students ?? [];
  const counts = live ? countsOf(live) : { writing: 0, submitted: 0, offline: 0, needs_look: 0 };
  const flags = live?.flags ?? [];
  const code = live?.start_code ?? exam.start_code;
  const exitCode = live?.invigilator_code ?? exam.invigilator_code ?? null;
  // Audit W-13: count down on the server's clock, not this laptop's.
  const left = latestDeadlineMs(students, now + offset);
  const stillWriting = counts.writing + counts.offline;
  const sub = [exam.course, exam.room && `Room ${exam.room}`, plural(students.length, "student")].filter(Boolean).join(" · ");
  // 2026-10-23: who may start, and the automatic close (external exams: from their source)
  const openTo = live?.open_to ?? exam.open_to ?? "all";
  const closesLabel = closesAtLabel(exam, live?.closes_at !== undefined ? live.closes_at : exam.closes_at);

  return (
    <>
      <section className="t-live-head" aria-label="Live exam controls">
        <div className="t-live-title">
          <h2>{exam.title}</h2>
          <span>{sub}</span>
        </div>
        <div className="t-dark-box">
          <span className="t-dark-box-label">
            Start code · read it aloud{" "}
            <button type="button" className="t-mini-link" onClick={rotate} disabled={rotating || closing}
              title="Make a new start code. Students writing online are not affected; students who started offline type the new code to send their answers.">
              {rotating ? "changing…" : "new code"}
            </button>
          </span>
          <div className="t-code">
            {code ?? "------"}
          </div>
        </div>
        <div className="t-dark-box">
          <span className="t-dark-box-label">{left == null ? "Each student gets" : "Time left"}</span>
          <div className="t-live-time" title={left == null ? undefined : "Until the last student who is writing must finish"}>
            {left == null ? formatCountdown(exam.duration_min * 60_000) : formatCountdown(left)}
          </div>
        </div>
        <div className="t-live-actions">
          <button type="button" className="t-btn-dark-outline" onClick={extend} disabled={extending || closing}>
            {extending ? "Adding…" : "Add 10 min"}
          </button>
          {!confirming && (
            <button type="button" className="t-btn-dark-outline t-btn-dark-danger" onClick={() => setConfirming(true)}>
              Close exam
            </button>
          )}
        </div>
        {confirming && (
          <div className="t-confirm-dark" role="alertdialog" aria-labelledby="close-q">
            <p id="close-q">
              <strong>Close this exam now?</strong>{" "}
              {stillWriting > 0
                ? `${plural(stillWriting, "student")} still writing will be submitted automatically.`
                : "Everyone has finished."}{" "}
              The start code stops working.
            </p>
            <button type="button" className="t-btn-light t-btn-solid-danger" onClick={close} disabled={closing}>
              {closing ? "Closing…" : "Yes, close exam"}
            </button>
            <button type="button" className="t-btn-dark-outline" onClick={() => setConfirming(false)} disabled={closing}>
              Cancel
            </button>
          </div>
        )}
      </section>
      {/* Audit W-06: invigilator tools sit apart from the start code a teacher projects,
          and the exit code shows only when asked for. */}
      <section className="t-live-tools" aria-label="Invigilator tools">
        {exitCode && <SecretCode code={exitCode} />}
        <OfflineExitBox examId={exam.id} students={students} />
        {closesLabel && (
          <div className="t-dark-box" data-testid="auto-close-label">
            <span className="t-dark-box-label">{closesLabel}</span>
          </div>
        )}
      </section>
      {actionError && (
        <div className="alert alert-error" role="alert">
          {actionError}
        </div>
      )}

      <div className="t-columns">
        <section aria-labelledby="seat-h" className="t-aside" style={{ flex: "999 1 560px", minWidth: 0, gap: 18 }}>
          <div className="t-stats" aria-label="Room summary">
            <div className="t-stat">
              <span className="t-stat-value blue">{counts.writing}</span>
              <span className="t-stat-label">Writing</span>
            </div>
            <div className="t-stat">
              <span className="t-stat-value green">{counts.submitted}</span>
              <span className="t-stat-label">Finished</span>
            </div>
            <div className="t-stat">
              <span className="t-stat-value grey">{counts.offline}</span>
              <span className="t-stat-label">Offline · answers saved</span>
            </div>
            <div className="t-stat warn">
              <span className="t-stat-value orange">{counts.needs_look}</span>
              <span className="t-stat-label">Need your look</span>
            </div>
          </div>

          <div className="t-card">
            <div className="t-card-head">
              <h2 id="seat-h">Students</h2>
              <div className="t-legend" aria-label="Legend">
                <span>
                  <i className="t-swatch writing" />
                  Writing
                </span>
                <span>
                  <i className="t-swatch done" />
                  Finished
                </span>
                <span>
                  <i className="t-swatch offline" />
                  Offline
                </span>
                <span>
                  <i className="t-swatch look" />
                  Look
                </span>
              </div>
            </div>
            <span className="t-small" aria-live="polite" style={{ color: pollError ? "var(--warn)" : "var(--muted)" }}>
              {pollError ?? "Updates every 3 seconds"}
            </span>
            {live === null && !pollError ? (
              <div className="t-empty">Loading students…</div>
            ) : students.length === 0 ? (
              <div className="t-empty">
                <strong>No students yet</strong>
                Students appear here as soon as they enter the start code.
              </div>
            ) : (
              <ul className="t-seats" aria-label="Students in the room">
                {students.map((s) => {
                  const st = seatState(s);
                  const desc = `${s.name}: ${SEAT_LABEL[st]}, ${s.answered} of ${s.question_count} answered${
                    s.status === "in_progress" ? `, on question ${s.current_question + 1}` : ""
                  }${s.locked ? "" : ", not locked"}${
                    s.client === "browser" ? ", in a web browser" : appSilent(s) ? ", FairPlay app not responding" : ""
                  }${(s.entries ?? 0) > 1 ? `, started ${s.entries} times` : ""}`;
                  return (
                    <li key={s.attempt_id} className={`t-seat ${st}`} title={desc}>
                      <Link
                        to={`/teacher/exams/${exam.id}/submissions/${s.attempt_id}`}
                        className="t-seat-link"
                        aria-label={`${desc}. Open submission`}
                      >
                        <span className="t-seat-name">{s.name}</span>
                        <span className="t-seat-sub">{seatSub(s, st)}</span>
                      </Link>
                      <SeatBadges examId={exam.id} student={s} onChanged={load} flags={flags} />
                      {s.status === "in_progress" && s.client !== "browser" && (
                        <MoveDevice examId={exam.id} student={s} onMoved={load} />
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <OpenToEditor
            exam={exam}
            openTo={openTo}
            selected={live?.selected}
            onChanged={(e) => {
              onExamChange(e);
              void load();
            }}
          />
          <DeviceReadiness examId={exam.id} />
        </section>

        <FlagsPanel examId={exam.id} flags={flags} onReviewed={load} />
      </div>
    </>
  );
}

function FlagsPanel({ examId, flags, onReviewed }: { examId: string; flags: LiveFlag[]; onReviewed: () => void }) {
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const shown = flags.filter((f) => !hidden.has(f.event_id));
  return (
    <aside aria-labelledby="flag-h" className="t-card" style={{ flex: "1 1 340px", minWidth: 0 }}>
      <h2 id="flag-h">Needs your look</h2>
      <p className="t-small t-muted" style={{ margin: 0, lineHeight: 1.5 }}>
        The system only suggests. You decide — nothing is reported until you confirm.
      </p>
      {shown.length === 0 ? (
        <div className="t-empty">
          <strong>Nothing to look at</strong>
          Blocked shortcuts and other lockdown events show up here.
        </div>
      ) : (
        shown.map((f) => (
          <FlagCard
            key={f.event_id}
            examId={examId}
            flag={f}
            onDone={() => {
              setHidden((h) => new Set(h).add(f.event_id));
              onReviewed();
            }}
          />
        ))
      )}
    </aside>
  );
}

function FlagCard({ examId, flag, onDone }: { examId: string; flag: LiveFlag; onDone: () => void }) {
  const [moving, setMoving] = useState(false);
  const [moved, setMoved] = useState(false);
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteId = `flag-note-${flag.event_id}`;

  async function review(status: "dismissed" | "noted") {
    setBusy(true);
    setError(null);
    try {
      await teacherApi.reviewEvent(examId, flag.event_id, status, status === "noted" ? note.trim() : "");
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  async function moveDevice() {
    setMoving(true);
    setError(null);
    try {
      await teacherApi.releaseDevice(examId, flag.attempt_id);
      setMoved(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setMoving(false);
    }
  }

  return (
    <article className="t-flag" aria-label={`${flag.name}: ${eventLabel(flag.type)}`}>
      <div className="t-flag-top">
        <span className="t-flag-who">
          {flag.name} · {eventLabel(flag.type)}
        </span>
        <span className="t-muted">{formatClock(flag.server_time)}</span>
      </div>
      {flag.detail && <span className="t-flag-detail mono">{eventDetailText(flag.type, flag.detail)}</span>}
      {noting && (
        <div className="t-field">
          <label htmlFor={noteId}>Note for the record</label>
          <textarea
            id={noteId}
            className="textarea"
            rows={2}
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            autoFocus
          />
        </div>
      )}
      {error && <span className="t-inline-error">{error}</span>}
      {flag.type === "second_device" && (
        <div className="t-flag-actions">
          {moved ? (
            <span className="t-small">The student can now start on the other computer.</span>
          ) : (
            <button type="button" className="t-btn-outline" onClick={moveDevice} disabled={moving}
              title="Only if you checked the student and their computer really broke">
              {moving ? "Allowing…" : "Allow the other computer"}
            </button>
          )}
        </div>
      )}
      <div className="t-flag-actions">
        {noting ? (
          <>
            <button type="button" className="t-btn-outline" onClick={() => setNoting(false)} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="t-btn-warn" onClick={() => review("noted")} disabled={busy || !note.trim()}>
              Save note
            </button>
          </>
        ) : (
          <>
            <button type="button" className="t-btn-outline" onClick={() => review("dismissed")} disabled={busy}>
              Looks fine
            </button>
            <button type="button" className="t-btn-outline" onClick={() => setNoting(true)} disabled={busy}>
              Add note
            </button>
          </>
        )}
      </div>
    </article>
  );
}


/** 2026-10-23: "Battery 14% · not charging" under 20 % (laptop known, not charging).
 *  2026-10-23 (review): charging unknown (null) still warns, as just "Battery 14%". */
export function lowBatteryBadge(s: Pick<LiveStudent, "battery" | "charging" | "online">): string | null {
  if (typeof s.battery !== "number" || s.charging === true || !s.online) return null;
  if (s.battery >= 0.2) return null;
  const pct = `Battery ${Math.round(s.battery * 100)}%`;
  return s.charging === false ? `${pct} · not charging` : pct;
}

/** Pilot 0.8.8: why a Mac / Windows student is shown as blocked or not locked, in plain words:
 *  from the app's own check summary (beats) and the open flags of that attempt. */
export function seatBlockReasons(s: Pick<LiveStudent, "app_checks" | "attempt_id" | "weak_lock">, flags: LiveFlag[] = []): string[] {
  const out: string[] = [];
  const checks: Record<string, string> = {};
  for (const part of (s.app_checks ?? "").split(",")) {
    const [k, , v] = part.trim().split(/(=)/);
    if (k) checks[k] = v ?? "";
  }
  const types = new Set(flags.filter((f) => f.attempt_id === s.attempt_id).map((f) => f.type.replace(/^client_/, "")));
  if (checks.vm === "1" || types.has("vm_detected")) out.push("Virtual machine detected");
  if (checks.remote === "1" || types.has("remote_session")) out.push("Remote session on this computer");
  if (s.weak_lock || checks.sep === "0" || types.has("lock_fallback")) out.push("Weaker lock (exam is not on its own desktop)");
  if (checks.mir === "1" || types.has("mirrored_display")) out.push("Screen mirrored or second display");
  if (checks.tap === "0" || types.has("tap_lost") || types.has("tap_unavailable")) out.push("Keyboard lock not working");
  if (types.has("win_foreign_desktop") || types.has("win_session_locked")) out.push("Blocked screen: another desktop or the Windows lock screen is in front");
  if (types.has("watchdog_app_killed") || types.has("previous_run_killed") || types.has("win_watchdog_missing")
    || types.has("win_policy_restored")) out.push("FairPlay was restarted by its watchdog");
  return out;
}

/** S3-S6: what needs the invigilator at this seat, as short badges (+ the button for it). */
export function SeatBadges({ examId, student: s, onChanged, flags }: { examId: string; student: LiveStudent; onChanged: () => void; flags?: LiveFlag[] }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const writing = s.status === "in_progress";
  const badges: string[] = [];
  if (s.sealed_pending) badges.push("submission pending on this student's computer");
  if (writing && s.approval === "pending") badges.push("Waiting for approval");
  if (writing && s.swap_requested) badges.push("Wants to continue on another computer");
  if (writing && s.weak_lock) badges.push("Not using the strongest lock");
  if (writing) {
    for (const r of seatBlockReasons(s, flags)) {
      if (!(s.weak_lock && r.startsWith("Weaker lock"))) badges.push(r);
    }
  }
  const low = lowBatteryBadge(s);
  if (writing && low) badges.push(low);
  if (badges.length === 0) return null;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="t-seat-action" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {badges.map((b) => (
        <span key={b} className="t-small" style={{ color: "var(--warn)", fontWeight: 600 }}>
          {b}
        </span>
      ))}
      {writing && s.approval === "pending" && (
        <button type="button" className="t-mini-link" disabled={busy} aria-label={`Approve ${s.name} to continue`}
          title="FairPlay closed on this computer and was opened again. Check the student, then let them continue."
          onClick={() => void act(() => teacherApi.approveResume(examId, s.attempt_id))}>
          {busy ? "…" : "Approve"}
        </button>
      )}
      {writing && s.swap_requested && (
        <button type="button" className="t-mini-link" disabled={busy} aria-label={`Approve swap for ${s.name}`}
          title="Only if you checked the student: the exam moves to the other computer, this one stops."
          onClick={() => void act(() => teacherApi.approveSwap(examId, s.attempt_id))}>
          {busy ? "…" : "Approve swap"}
        </button>
      )}
      {error && <span className="t-small" style={{ color: "var(--warn)" }}>{error}</span>}
    </span>
  );
}

/** S1: each student's latest FairPlay device check (Readiness page "Run check"). */
export function DeviceReadiness({ examId }: { examId: string }) {
  const [rows, setRows] = useState<ReadinessRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRows(await teacherApi.readiness(examId));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [examId]);
  useEffect(() => {
    void load();
  }, [load]);
  const notReady = (rows ?? []).filter((r) => r.ok !== true).length;
  return (
    <section className="t-card" aria-labelledby="ready-h" data-testid="device-readiness">
      <div className="t-card-head">
        <h2 id="ready-h">Device readiness</h2>
        <button type="button" className="t-mini-link" onClick={() => void load()}>
          refresh
        </button>
      </div>
      {error && <span className="t-inline-error">{error}</span>}
      {rows === null && !error ? (
        <div className="t-empty">Loading…</div>
      ) : rows && rows.length === 0 ? (
        <div className="t-empty">No students in this exam's class yet.</div>
      ) : (
        <>
          <span className="t-small t-muted">
            {notReady === 0 ? "Every student's computer passed its check." : `${plural(notReady, "student")} not ready or not checked yet.`}
          </span>
          <ul className="t-ready-list" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
            {(rows ?? []).map((r) => (
              <li key={r.student_id}>
                <strong>{r.name}</strong>
                {" · "}
                {r.checked_at == null
                  ? "not checked yet"
                  : r.ok
                    ? `ready (${platformName(r.platform)} ${r.app_version ?? ""}, ${formatClock(r.checked_at)})`
                    : `needs a fix: ${(r.failed ?? []).join(", ")} (${formatClock(r.checked_at)})`}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function platformName(p: string | null): string {
  return p === "mac" ? "Mac" : p === "windows" ? "Windows" : p === "ipad" ? "iPad" : p ?? "";
}

export const SECRET_SHOW_MS = 20_000;

/** Audit W-06: the invigilator exit code, hidden until clicked, hidden again after 20 s. */
export function SecretCode({ code }: { code: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!shown) return;
    const t = setTimeout(() => setShown(false), SECRET_SHOW_MS);
    return () => clearTimeout(t);
  }, [shown]);
  return (
    <div className="t-dark-box">
      <span className="t-dark-box-label">
        Invigilator exit code · keep private{" "}
        <button type="button" className="t-mini-link" onClick={() => setShown((v) => !v)} aria-pressed={shown}>
          {shown ? "hide" : "show"}
        </button>
      </span>
      <div
        className="t-code t-code-sm"
        aria-live="polite"
        title="On a locked Mac press Ctrl-Opt-Cmd-E and type this code. Every use is logged."
      >
        {shown ? code : "••••••"}
      </div>
    </div>
  );
}

/** Audit W-07: move a writing student to another computer from their seat, any time. */
export function MoveDevice({ examId, student, onMoved }: { examId: string; student: LiveStudent; onMoved: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function move() {
    setBusy(true);
    try {
      await teacherApi.releaseDevice(examId, student.attempt_id);
      setMsg("Moved: they type the start code on the other computer.");
      setAsking(false);
      onMoved();
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  if (msg && !asking)
    return (
      <span className="t-seat-action t-small" role="status">
        {msg}
      </span>
    );
  return asking ? (
    <span className="t-seat-action">
      <span className="t-small">Move {student.name} to another computer?</span>{" "}
      <button type="button" className="t-mini-link" onClick={() => void move()} disabled={busy}>
        {busy ? "moving…" : "yes, move"}
      </button>{" "}
      <button type="button" className="t-mini-link" onClick={() => setAsking(false)}>
        cancel
      </button>
    </span>
  ) : (
    <button
      type="button"
      className="t-mini-link t-seat-action"
      onClick={() => setAsking(true)}
      aria-label={`Move ${student.name} to another computer`}
    >
      move computer
    </button>
  );
}
