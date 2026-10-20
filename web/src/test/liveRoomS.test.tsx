// S3-S6 (2026-10-20) live-room badges and their buttons; editor settings round trip.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import type { Exam, LiveStudent } from "../api/types";
import { draftFromExam, toExamInput } from "../pages/teacher/examDraft";
import { SeatBadges } from "../pages/teacher/LiveRoom";
import { ExamEditor } from "../pages/teacher/ExamEditor";
import { MemoryRouter } from "react-router-dom";

function student(over: Partial<LiveStudent> = {}): LiveStudent {
  return {
    attempt_id: "a_1", username: "alice", name: "Alice", status: "in_progress",
    started_at: "", submitted_at: null, deadline_at: "", answered: 0, question_count: 3,
    current_question: 0, locked: true, last_heartbeat_at: null, online: true, event_count: 0,
    last_event: null, needs_look: false, ...over,
  };
}

function okFetch() {
  const fn = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const urlOf = (fn: ReturnType<typeof okFetch>, i = 0) => (fn.mock.calls[i] as unknown as [string, RequestInit])[0];

describe("live room S3-S6", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("nothing to show for a normal seat", () => {
    const { container } = render(<SeatBadges examId="e_1" student={student()} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("Waiting for approval + Approve", async () => {
    setToken("teacher", "T");
    const f = okFetch();
    const changed = vi.fn();
    render(<SeatBadges examId="e_1" student={student({ approval: "pending" })} onChanged={changed} />);
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve Alice to continue" }));
    expect(urlOf(f)).toBe("/api/exams/e_1/attempts/a_1/approve-resume");
    expect(changed).toHaveBeenCalled();
  });

  it("sealed answers pending on the student's computer", () => {
    render(<SeatBadges examId="e_1" student={student({ status: "submitted", sealed_pending: true })} onChanged={vi.fn()} />);
    expect(screen.getByText("submission pending on this student's computer")).toBeInTheDocument();
  });
});

describe("exam settings S3 / S6", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("weak lock badge", () => {
    render(<SeatBadges examId="e_1" student={student({ weak_lock: true })} onChanged={vi.fn()} />);
    expect(screen.getByText("Not using the strongest lock")).toBeInTheDocument();
  });

  it("strongest lock defaults off and round-trips", () => {
    const d = draftFromExam();
    expect(d.strongLock).toBe(false);
    expect(toExamInput(d).require_strong_lock).toBe(false);
    const on = draftFromExam({ title: "x", duration_min: 5, questions: [], require_strong_lock: true } as unknown as Exam);
    expect(toExamInput(on).require_strong_lock).toBe(true);
  });

  it("the editor shows both toggles under More settings", async () => {
    setToken("teacher", "T");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(
      <MemoryRouter>
        <ExamEditor onSaved={vi.fn()} cancelTo="/teacher" />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: /More settings/ }));
    const resume = screen.getByLabelText("Teacher approves a student coming back after FairPlay closed");
    const strong = screen.getByLabelText("Require the strongest lock");
    expect(resume).toHaveAttribute("aria-checked", "true");
    expect(strong).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/Mac: needs Apple assessment mode, not available yet/)).toBeInTheDocument();
    await userEvent.click(strong);
    expect(strong).toHaveAttribute("aria-checked", "true");
  });

  it("resume approval defaults on and round-trips", () => {
    const d = draftFromExam();
    expect(d.resumeNeedsApproval).toBe(true);
    expect(toExamInput(d).resume_needs_approval).toBe(true);
    const off = draftFromExam({ ...({} as Exam), title: "x", duration_min: 5, questions: [], resume_needs_approval: false } as Exam);
    expect(off.resumeNeedsApproval).toBe(false);
    expect(toExamInput(off).resume_needs_approval).toBe(false);
  });
});
