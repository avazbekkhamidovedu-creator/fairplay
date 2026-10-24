// 2026-10-24: "Student review after hand-in" set by the professor in DKU OJ (server app/review.py):
// the student pages show the right view per level and never offer a link the server refuses.
import { fireEvent, render, screen, within } from "@testing-library/react";
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
    expect(canOpenReview("dkuoj", "score", true)).toBe(false);
    expect(canOpenReview("dkuoj", "score", false)).toBe(false);
    expect(canOpenReview("dkuoj", "breakdown", false)).toBe(false);
    expect(canOpenReview("dkuoj", "breakdown", true)).toBe(true);
    expect(waitingLabel("score")).toBe("Being graded");
    expect(waitingLabel("breakdown")).toBe("Being graded");
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

describe("past exam row (grade card, style A)", () => {
  const row = (e: StudentExamSummary, g?: StudentGradeSummary) =>
    render(
      <MemoryRouter>
        <PastExamRow exam={e} grade={g} />
      </MemoryRouter>,
    );
  const card = () => screen.getByTestId("grade-card");

  it("nothing: handed in, private, no link, no ring", () => {
    row(past({ review: "nothing" }));
    expect(card()).toHaveAttribute("data-state", "private");
    expect(card().tagName).toBe("ARTICLE");
    expect(screen.getByTestId("grade-line")).toHaveTextContent(/^Handed in Oct 24 · your teacher keeps this one private$/);
    expect(screen.getByTestId("ring-handed-in")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("score: the ring with the percent and points, no link, no chips", () => {
    row(past({ review: "score", released: true, percent: 86.2 }), grade({ review: "score", released: true, score: 22.4, percent: 86.2 }));
    expect(screen.getByRole("img", { name: "86 percent" })).toBeInTheDocument();
    expect(screen.getByTestId("grade-line")).toHaveTextContent("22.4 / 26 points · Oct 24");
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByTestId("breakdown-chips")).toBeNull();
    expect(screen.queryByText(/Feedback/)).toBeNull();
  });

  it("score before the release: being graded (dashed ring)", () => {
    row(past({ review: "score" }));
    expect(card()).toHaveAttribute("data-state", "grading");
    expect(screen.getByTestId("ring-grading")).toBeInTheDocument();
    expect(screen.getByTestId("grade-line")).toHaveTextContent("being graded");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("breakdown: ring + chips per question, no feedback link", () => {
    const bd = [
      { index: 0, label: "Q1", points: 4, points_awarded: 4 },
      { index: 1, label: "Q2", points: 8, points_awarded: 2 },
    ];
    row(past({ review: "breakdown", released: true, percent: 50 }), grade({ review: "breakdown", released: true, score: 6, total_points: 12, percent: 50, breakdown: bd }));
    expect(screen.getByRole("img", { name: "50 percent" })).toBeInTheDocument();
    const chips = within(screen.getByRole("list", { name: "Points per question" })).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["Q1 · 4/4Q1: 4 of 4 points", "Q2 · 2/8Q2: 2 of 8 points"]);
    expect(chips[0]).toHaveClass("good");
    expect(chips[1]).toHaveClass("low");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("breakdown with many questions links to all of them", () => {
    const bd = Array.from({ length: 20 }, (_, i) => ({ index: i, label: `Q${i + 1}`, points: 1, points_awarded: 1 }));
    row(past({ review: "breakdown", released: true, percent: 100 }), grade({ review: "breakdown", released: true, score: 20, total_points: 20, percent: 100, breakdown: bd }));
    expect(screen.getByRole("link")).toHaveAttribute("href", "/student/grades/e1");
    expect(screen.getByRole("link")).toHaveTextContent("All 20 questions");
    expect(screen.getByText("+8 more")).toBeInTheDocument();
  });

  it("answers: a link to the answers, no score", () => {
    row(past({ review: "answers" }));
    expect(screen.getByRole("link")).toHaveAttribute("href", "/student/grades/e1");
    expect(screen.getByText("Your answers")).toBeInTheDocument();
    expect(screen.getByTestId("grade-line")).toHaveTextContent("Handed in Oct 24 · scores are not shared");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("answers and results: answers while being graded, feedback once released", () => {
    const { unmount } = row(past({ review: "answers_and_results" }));
    expect(screen.getByTestId("grade-line")).toHaveTextContent("being graded");
    expect(screen.getByText("Your answers")).toBeInTheDocument();
    unmount();
    row(past({ review: "answers_and_results", released: true, percent: 81 }));
    expect(screen.getByRole("img", { name: "81 percent" })).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveTextContent("Feedback");
  });

  it("FairPlay exams unchanged: no link before the release", () => {
    row(past({ review: "answers_and_results" }, "fairplay"));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByTestId("grade-line")).toHaveTextContent("being graded");
  });

  it("ring colours: green from 85, blue from 65, amber below; the text stays dark", () => {
    for (const [p, c] of [[90, "#1d7a3d"], [70, "#3340d6"], [40, "#b25e00"]] as const) {
      const { unmount } = row(past({ review: "answers_and_results", released: true, percent: p }));
      const ring = screen.getByRole("img", { name: `${p} percent` });
      expect(ring.querySelectorAll("circle")[1]).toHaveAttribute("stroke", c);
      expect(ring.querySelector("text")).toHaveAttribute("fill", "#161a2c");
      unmount();
    }
  });
});

describe("grades page", () => {
  it("shows each exam per its level, in cards", async () => {
    routes["GET /api/student/grades"] = {
      status: 200,
      body: [
        grade({ exam_id: "n", title: "Closed one", review: "nothing" }),
        grade({ exam_id: "s", title: "Score one", review: "score", released: true, score: 13, percent: 50 }),
        grade({ exam_id: "b", title: "Points one", review: "breakdown", released: true, score: 13, percent: 50,
          breakdown: [{ index: 0, label: "Q1", points: 26, points_awarded: 13 }] }),
        grade({ exam_id: "a", title: "Answers one", review: "answers" }),
        grade({ exam_id: "r", title: "Results one", review: "answers_and_results", released: true, score: 20, percent: 76.9 }),
        grade({ exam_id: "w", title: "Waiting one", review: "answers_and_results", course: "MATH 201" }),
      ],
    };
    render(
      <MemoryRouter>
        <GradesPage />
      </MemoryRouter>,
    );
    const cardOf = async (t: string) => (await screen.findByText(t)).closest("[data-testid=grade-card]") as HTMLElement;
    const closed = await cardOf("Closed one");
    expect(closed.tagName).toBe("ARTICLE");
    expect(closed).toHaveTextContent("your teacher keeps this one private");
    const score = await cardOf("Score one");
    expect(score.tagName).toBe("ARTICLE");
    expect(within(score).getByRole("img", { name: "50 percent" })).toBeInTheDocument();
    expect(score).toHaveTextContent("13 / 26 points");
    const pts = await cardOf("Points one");
    expect(within(pts).getByRole("list", { name: "Points per question" })).toHaveTextContent("Q1 · 13/26");
    const answers = await cardOf("Answers one");
    expect(answers).toHaveAttribute("href", "/student/grades/a");
    expect(within(answers).getByText("Your answers")).toBeInTheDocument();
    expect(answers).not.toHaveTextContent(/\/ 26/);
    const results = await cardOf("Results one");
    expect(results).toHaveAttribute("href", "/student/grades/r");
    expect(results).toHaveTextContent("20 / 26 points");
    expect(within(results).getByText("Feedback")).toBeInTheDocument();
    expect(screen.getByTestId("grades-summary")).toHaveTextContent("3 graded · 1 being graded · 2 handed in");
    // course filter
    fireEvent.click(screen.getByRole("button", { name: "MATH 201" }));
    expect(screen.getAllByTestId("grade-card")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "MATH 201" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "All courses" }));
    expect(screen.getAllByTestId("grade-card")).toHaveLength(6);
  });

  it("60 grades render as cards", async () => {
    routes["GET /api/student/grades"] = {
      status: 200,
      body: Array.from({ length: 60 }, (_, i) =>
        grade({ exam_id: `g${i}`, title: `Quiz ${i}`, review: "answers_and_results", released: true, score: i % 27, percent: ((i % 27) / 26) * 100 }),
      ),
    };
    render(
      <MemoryRouter>
        <GradesPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Quiz 59")).toBeInTheDocument();
    expect(screen.getAllByTestId("grade-card")).toHaveLength(60);
    expect(screen.getAllByTestId("score-ring")).toHaveLength(60);
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

  it("score: the ring and total only, no questions", async () => {
    const data = {
      exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1",
      score: 13, total_points: 26, percent: 50, class_average_percent: null, overall_comment: "",
      source: "dkuoj", review: "score", released: true, questions: [],
    } as StudentFeedback;
    routes["GET /api/student/grades/e1"] = { status: 200, body: data };
    page();
    expect(await screen.findByTestId("score-only")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "50 percent" })).toBeInTheDocument();
    expect(screen.getByTestId("score-only-note")).toHaveTextContent("grade only");
    expect(screen.queryByTestId("breakdown-table")).toBeNull();
    expect(screen.queryByTestId("external-feedback-question")).toBeNull();
  });

  it("breakdown: points per question, no question text", async () => {
    const data = {
      exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1",
      score: 12, total_points: 26, percent: 46.2, class_average_percent: null, overall_comment: "",
      source: "dkuoj", review: "breakdown", released: true,
      questions: [
        { question_id: "q1", index: 0, label: "Q1", title: "Hash maps", points: 10, points_awarded: 10, status: "correct" },
        { question_id: "q2", index: 1, label: "Q2", title: "", points: 16, points_awarded: null, status: "pending" },
      ],
    } as unknown as StudentFeedback;
    routes["GET /api/student/grades/e1"] = { status: 200, body: data };
    page();
    const table = await screen.findByTestId("breakdown-table");
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Q1 · Hash maps10 / 10");
    expect(rows[2]).toHaveTextContent("Q2— / 16");
    expect(screen.getByRole("img", { name: "46 percent" })).toBeInTheDocument();
  });

  it("score before the release: being graded", async () => {
    const data = {
      exam_id: "e1", title: "Midterm", course: "CS 101", submitted_at: "2026-10-24T09:00:00Z", receipt: "R1",
      score: null, total_points: 26, percent: null, class_average_percent: null, overall_comment: "",
      source: "dkuoj", review: "score", released: false, questions: [],
    } as StudentFeedback;
    routes["GET /api/student/grades/e1"] = { status: 200, body: data };
    page();
    expect(await screen.findByText("Being graded")).toBeInTheDocument();
    expect(screen.queryByTestId("score-ring")).toBeNull();
  });
});
