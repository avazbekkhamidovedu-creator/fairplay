import { useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import type { AnswerMap, AnswerValue, StudentQuestion } from "../../api/types";
import { LogoMark } from "../../components/ui/Icon";
import { AnswerInput } from "../../components/student/AnswerInput";
import { ArrowLeftIcon, ArrowRightIcon, ChevronIcon, CloseIcon } from "../../components/student/icons";
import * as bridge from "../../lib/bridge";
import "../../styles/exam-d.css";
import "../../styles/student.css";

const START_SECONDS = 60;

const SAMPLE: StudentQuestion[] = [
  { id: "s1", type: "mcq", prompt: "2 + 2 = ?", points: 1, options: ["3", "4", "5", "22"] },
  { id: "s2", type: "numeric", prompt: "What is 12 × 5?", points: 1, options: [] },
  { id: "s3", type: "short", prompt: "Name the capital city of France.", points: 1, options: [] },
  { id: "s4", type: "code", prompt: "Write a function that returns the sum of two numbers.", points: 2, options: [] },
];

export function clock(total: number): string {
  const s = Math.max(0, Math.floor(total));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function startRemaining(state: unknown): number {
  const r = (state as { remaining?: unknown } | null)?.remaining;
  return typeof r === "number" && Number.isFinite(r) && r > 0 ? r : START_SECONDS;
}

export default function DeviceTestPage() {
  const location = useLocation();
  if (!bridge.isNative()) return <Navigate to="/student/login" replace />;
  const entry = new URLSearchParams(location.search).get("nativeEntry") === "1";
  if (!entry && !bridge.deviceTestConfirmed()) return <NoPreview />;
  return <DeviceTest />;
}

function NoPreview() {
  const navigate = useNavigate();
  function back() {
    bridge.deviceTestLeave();
    navigate("/student/home", { replace: true });
  }
  return (
    <div className="stu" data-testid="device-test-missing">
      <main className="stu-alert warn" role="alert">
        <p>The device test is not running. Start it again from the home screen.</p>
        <button type="button" className="stu-btn primary" onClick={back}>
          Back to home
        </button>
      </main>
    </div>
  );
}

function DeviceTest() {
  const navigate = useNavigate();
  const location = useLocation();
  const [remaining, setRemaining] = useState(() => startRemaining(location.state));
  const [current, setCurrent] = useState(0);
  const [navOpen, setNavOpen] = useState(false);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const left = useRef(false);
  const nativeStarted = useRef(false);

  function leaveToHome() {
    if (left.current) return;
    left.current = true;
    bridge.setDeviceTestConfirmed(false);
    navigate("/student/home", { replace: true });
  }

  useEffect(
    () =>
      bridge.onDeviceTest((m) => {
        if (m.state === "ended") leaveToHome();
        else if (typeof m.remaining === "number" && m.remaining > 0) setRemaining(m.remaining);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useEffect(() => {
    if (new URLSearchParams(location.search).get("nativeEntry") !== "1" || nativeStarted.current) return;
    nativeStarted.current = true;
    bridge.deviceTestStart(START_SECONDS);
  }, [location.search]);

  useEffect(() => {
    const id = window.setInterval(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (remaining <= 0 && !left.current) {
      bridge.deviceTestLeave();
      leaveToHome();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining]);

  function leave() {
    bridge.deviceTestLeave();
    leaveToHome();
  }

  const q = SAMPLE[current]!;
  const isLast = current === SAMPLE.length - 1;
  const setAnswer = (id: string, v: AnswerValue | undefined) =>
    setAnswers((a) => {
      const next = { ...a };
      if (v === undefined) delete next[id];
      else next[id] = v;
      return next;
    });
  const answeredCount = SAMPLE.filter((s) => answers[s.id] !== undefined).length;

  const ask =
    q.type === "mcq" ? "Choose the right option" : q.type === "code" ? "Write your code" : "Your answer";

  return (
    <div className="stu stu-exam exam-d xd-fill" data-testid="device-test">
      <header className="stu-lockbar">
        <div className="stu-lb-left">
          <LogoMark size={28} title="FairPlay" />
          <div className="stu-lockbar-title">
            <small className="c">Device test</small>
            <span className="t">Sample exam</span>
          </div>
        </div>
        <div className="stu-lb-center">
          <span className="stu-timer" role="timer" aria-live="off" data-testid="device-test-countdown">
            Leaves automatically in {clock(remaining)}
          </span>
        </div>
        <div className="stu-lb-right">
          <button type="button" className="stu-btn primary" onClick={leave}>
            Leave
          </button>
        </div>
      </header>
      <div className="stu-alert warn xd-preview-note" role="status" data-testid="device-test-banner">
        Preview: nothing here is saved or graded
      </div>

      <main className={`xd-card${q.type === "code" ? " code" : ""}`} aria-labelledby="question-prompt" data-testid="exam-card">
        <div className="xd-left">
          <div className="xd-left-body">
            <div className="xd-qmeta">
              <span className="xd-eyebrow" aria-hidden="true">
                Question {current + 1}
              </span>
              <span className="sr-only">
                Question {current + 1} of {SAMPLE.length}
              </span>
              <span className="stu-qhead-pts">
                {q.points} {q.points === 1 ? "point" : "points"}
              </span>
            </div>
            <p className="stu-qtitle">Sample questions</p>
            <h1 id="question-prompt" className="stu-prompt" tabIndex={-1}>
              {q.prompt}
            </h1>
          </div>
        </div>
        <div className="xd-divider" aria-hidden="true">
          <span />
        </div>
        <div className="xd-right">
          <h2 className="xd-ask" id="answer-heading">
            {ask}
          </h2>
          {q.type === "code" ? (
            <div className="stu-editor">
              <textarea
                key={q.id}
                aria-labelledby="question-prompt"
                spellCheck={false}
                style={{ fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: 14, lineHeight: 1.6 }}
                value={typeof answers[q.id] === "string" ? (answers[q.id] as string) : ""}
                onChange={(e) => setAnswer(q.id, e.target.value === "" ? undefined : e.target.value)}
              />
            </div>
          ) : (
            <AnswerInput
              key={q.id}
              question={q}
              value={answers[q.id]}
              onChange={(v) => setAnswer(q.id, v)}
              labelledBy="question-prompt"
            />
          )}
        </div>
      </main>

      <nav className="stu-bottombar xd-foot" aria-label="Questions">
        <div className="stu-bottombar-inner">
          <button
            type="button"
            className="stu-qtoggle"
            aria-expanded={navOpen}
            aria-controls="question-navigator"
            aria-label={`Question ${current + 1} of ${SAMPLE.length}`}
            onClick={() => setNavOpen((o) => !o)}
          >
            <span>
              Question {current + 1}
              <span className="xd-of"> of {SAMPLE.length}</span>
            </span>
            <ChevronIcon className="chev" />
          </button>
          <div className="stu-bottombar-nav">
            <button type="button" className="stu-btn lg" onClick={() => setCurrent((c) => Math.max(0, c - 1))} disabled={current === 0}>
              <ArrowLeftIcon />
              Back
            </button>
            <button type="button" className="stu-btn primary lg" onClick={() => setCurrent((c) => c + 1)} disabled={isLast}>
              Next
              <ArrowRightIcon />
            </button>
          </div>
          {navOpen && (
            <div id="question-navigator" className="stu-navigator" role="dialog" aria-labelledby="nav-h">
              <div className="stu-navigator-top">
                <div className="stu-navigator-head">
                  <h2 id="nav-h">Sample questions: questions</h2>
                  <button type="button" className="stu-icon-btn" aria-label="Close" onClick={() => setNavOpen(false)}>
                    <CloseIcon />
                  </button>
                </div>
                <div className="stu-legend">
                  <span>Answered {answeredCount}</span>
                </div>
              </div>
              <div className="stu-qgrid">
                {SAMPLE.map((s, i) => {
                  const done = answers[s.id] !== undefined;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      className={`stu-qcell${done ? " answered" : ""}${i === current ? " current" : ""}`}
                      aria-label={`Question ${i + 1}, ${done ? "answered" : "not answered"}${i === current ? ", current" : ""}`}
                      aria-current={i === current ? "step" : undefined}
                      onClick={() => {
                        setCurrent(i);
                        setNavOpen(false);
                      }}
                    >
                      {i + 1}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </nav>
    </div>
  );
}
