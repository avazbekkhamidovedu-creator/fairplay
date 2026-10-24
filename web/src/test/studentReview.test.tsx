// 2026-10-24: "Student review after hand-in" set by the professor in DKU OJ (server app/review.py):
// the student pages show the right view per level and never offer a link the server refuses.
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FeedbackQuestion, StudentExamSummary, StudentFeedback, StudentGradeSummary } from "../api/types";
import { setToken } from "../api/client";
import { PastExamRow } from "../components/student/ResultRow";
import FeedbackPage from "../pages/student/FeedbackPage";
import GradesPage, { groupByCourse } from "../pages/student/GradesPage";
import { REVIEW_CLOSED, canOpenReview, waitingLabel } from "../lib/review";

type Reply = { status: number; body?: unknown };
let routes: Record<string, Reply>;

beforeEach(() => {
  routes = {};
  setToken("student", "TOK");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true, features: [] }), { status: 200 });
      if (url === "/api/me") return new Response(JSON.stringify({ username: "bob", name: "Bob Li", role: "student" }), { status: 200 });
      const r = routes[`${method} ${url}`] ?? { status: 404, body: { detail: "Not found" } };
      return new Response(r.body === undefined ? "" : JSON.stringify(r.body), { status: r.status });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

function past(over: Partial<NonNullable<StudentExamSummary["attempt"]>>, source = "dkuoj"): StudentExamSummary {
  return {
    id: "e1",
    title: "Midterm",
    duration_min: 90,
    question_count: 3,
    status: "closed",
    course: "CS 101",
    room: "",
    scheduled_at: null,
    source,
    attempt: { attempt_id: "a1", status: "submitted", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1", released: false, percent: null, ...over },
  } as StudentExamSummary;
}

function grade(over: Partial<StudentGradeSummary>): StudentGradeSummary {
  return { exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", released: false, score: null, total_points: 26, percent: null, source: "dkuoj", ...over };
}

describe("review helpers", () => {
  it("opens the page only when the level allows it", () => {
    expect(canOpenReview("dkuoj", "nothing", false)).toBe(false);
    expect(canOpenReview("dkuoj", "nothing", true)).toBe(false);
    expect(canOpenReview("dkuoj", "answers", false)).toBe(true);
    expect(canOpenReview("dkuoj", "answers_and_results", false)).toBe(true);
    expect(canOpenReview("dkuoj", "answers_and_results", true)).toBe(true);
    // FairPlay exams: feedback only once released (as before)
    expect(canOpenReview("fairplay", "answers_and_results", false)).toBe(false);
    expect(canOpenReview("fairplay", "answers_and_results", true)).toBe(true);
    expect(canOpenReview(undefined, undefined, false)).toBe(false);
    expect(waitingLabel("nothing")).toBe("Handed in");
    expect(waitingLabel("answers")).toBe("Handed in");
    expect(waitingLabel("answers_and_results")).toBe("Being graded");
    expect(waitingLabel(undefined)).toBe("Being graded");
  });

  it("does not count closed reviews as being graded", () => {
    const g = groupByCourse([grade({ exam_id: "1", review: "nothing" }), grade({ exam_id: "2", review: "answers" }),
      grade({ exam_id: "3", review: "answers_and_results" })]);
    expect([g[0].graded, g[0].waiting]).toEqual([0, 1]);
  });
});

describe("past exam row", () => {
  const row = (e: StudentExamSummary) =>
    render(
      <MemoryRouter>
        <PastExamRow exam={e} />
      </MemoryRouter>,
    );

  it("nothing: just handed in, no link", () => {
    row(past({ review: "nothing" }));
    expect(screen.getByText("Handed in")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/See your answers|See feedback/)).not.toBeInTheDocument();
  });

  it("answers: a link to the answers, no score", () => {
    row(past({ review: "answers" }));
    expect(screen.getByRole("link")).toHaveAttribute("href", "/student/grades/e1");
    expect(screen.getByText("See your answers")).toBeInTheDocument();
    expect(screen.getByText("Handed in")).toBeInTheDocument();
  });

  it("answers and results: answers while being graded, feedback once released", () => {
    const { unmount } = row(past({ review: "answers_and_results" }));
    expect(screen.getByText("Being graded")).toBeInTheDocument();
    expect(screen.getByText("See your answers")).toBeInTheDocument();
    unmount();
    row(past({ review: "answers_and_results", released: true, percent: 81 }));
    expect(screen.getByText("81%")).toBeInTheDocument();
    expect(screen.getByText("See feedback")).toBeInTheDocument();
  });

  it("FairPlay exams unchanged: no link before the release", () => {
    row(past({ review: "answers_and_results" }, "fairplay"));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Being graded")).toBeInTheDocument();
  });
});

describe("grades page", () => {
  it("shows each exam per its level", async () => {
    routes["GET /api/student/grades"] = {
      status: 200,
      body: [
        grade({ exam_id: "n", title: "Closed one", review: "nothing" }),
        grade({ exam_id: "a", title: "Answers one", review: "answers" }),
        grade({ exam_id: "r", title: "Results one", review: "answers_and_results", released: true, score: 20, percent: 76.9 }),
      ],
    };
    render(
      <MemoryRouter>
        <GradesPage />
      </MemoryRouter>,
    );
    const closed = (await screen.findByText("Closed one")).closest(".stu-result-row") as HTMLElement;
    expect(closed.tagName).toBe("DIV");
    expect(within(closed).getByText("Handed in")).toBeInTheDocument();
    const answers = screen.getByText("Answers one").closest(".stu-result-row") as HTMLElement;
    expect(answers).toHaveAttribute("href", "/student/grades/a");
    expect(within(answers).getByText("Your answers")).toBeInTheDocument();
    expect(within(answers).queryByText(/\/ 26/)).not.toBeInTheDocument();
    const results = screen.getByText("Results one").closest(".stu-result-row") as HTMLElement;
    expect(results).toHaveAttribute("href", "/student/grades/r");
    expect(within(results).getByText("20 / 26")).toBeInTheDocument();
  });
});

describe("review page", () => {
  const page = () =>
    render(
      <MemoryRouter initialEntries={["/student/grades/e1"]}>
        <Routes>
          <Route path="/student/grades/:examId" element={<FeedbackPage />} />
        </Routes>
      </MemoryRouter>,
    );

  it("nothing: says the teacher has not opened the review", async () => {
    routes["GET /api/student/grades/e1"] = { status: 403, body: { detail: REVIEW_CLOSED } };
    page();
    const card = await screen.findByTestId("review-closed");
    expect(card).toHaveTextContent(REVIEW_CLOSED);
    expect(screen.queryByText("Not released yet")).not.toBeInTheDocument();
  });

  it("not released (FairPlay exam) keeps its message", async () => {
    routes["GET /api/student/grades/e1"] = { status: 403, body: { detail: "Grades not released yet" } };
    page();
    expect(await screen.findByRole("heading", { name: "Not released yet" })).toBeInTheDocument();
  });

  function q(over: Partial<FeedbackQuestion>): FeedbackQuestion {
    return { question_id: "q1", index: 0, type: "mcq", prompt: "Which one?", options: ["Array", "Hash table"], points: 10,
      points_awarded: null, status: "answered", given: 1, correct_answer: null, tolerance: null, comment: "",
      rubric: [], rubric_scores: null, ...over } as FeedbackQuestion;
  }

  it("answers: own answers, no score, points or feedback", async () => {
    const data: StudentFeedback = {
      exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1",
      score: null, total_points: 26, percent: null, class_average_percent: null, overall_comment: "",
      source: "dkuoj", external_name: "DKU OJ", review: "answers", released: false,
      questions: [q({}), q({ question_id: "q2", index: 1, type: "short", prompt: "Explain", options: [], points: 6, given: null, status: "unanswered" })],
    };
    routes["GET /api/student/grades/e1"] = { status: 200, body: data };
    page();
    expect(await screen.findByRole("heading", { name: "Midterm" })).toBeInTheDocument();
    expect(screen.getByTestId("answers-only-note")).toHaveTextContent("Scores and feedback are not shown");
    expect(screen.queryByTestId("external-graded-note")).not.toBeInTheDocument();
    expect(screen.queryByText(/\/ 26/)).not.toBeInTheDocument();
    const cards = screen.getAllByTestId("external-feedback-question");
    expect(within(cards[0]).getByText("B · Hash table")).toBeInTheDocument();
    expect(cards[0].querySelector("[data-status]")).toHaveTextContent("10 points · answered");
    expect(cards[1].querySelector("[data-status]")).toHaveTextContent("6 points · not answered");
    expect(screen.queryByText("CORRECT ANSWER")).not.toBeInTheDocument();
    expect(screen.queryByTestId("external-feedback")).not.toBeInTheDocument();
  });

  it("answers and results: the score and feedback", async () => {
    const data: StudentFeedback = {
      exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1",
      score: 10, total_points: 26, percent: 38.5, class_average_percent: null, overall_comment: "",
      source: "dkuoj", external_name: "DKU OJ", review: "answers_and_results", released: true,
      questions: [q({ points_awarded: 10, status: "correct", comment: "Good" })],
    };
    routes["GET /api/student/grades/e1"] = { status: 200, body: data };
    page();
    expect(await screen.findByRole("heading", { name: "Midterm" })).toBeInTheDocument();
    expect(screen.getByTestId("external-graded-note")).toBeInTheDocument();
    expect(screen.queryByTestId("answers-only-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("external-feedback")).toHaveTextContent("Good");
  });
});
