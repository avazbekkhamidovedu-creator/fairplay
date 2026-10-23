// 2026-10-23: open to everyone or to selected students; automatic close at the end time.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Exam, ExamStudent, LiveRoom as LiveRoomData, StudentExamSummary } from "../api/types";

const teacher = vi.hoisted(() => ({
  getExam: vi.fn(),
  openExam: vi.fn(),
  examStudents: vi.fn(),
  updateOpenTo: vi.fn(),
  live: vi.fn(),
  readiness: vi.fn(),
  results: vi.fn(),
}));
const student = vi.hoisted(() => ({ listExams: vi.fn() }));

vi.mock("../api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/client")>();
  return {
    ...orig,
    auth: { ...orig.auth, me: vi.fn().mockResolvedValue({ username: "bob", name: "Bob Karimov", role: "student" }) },
    teacherApi: { ...orig.teacherApi, ...teacher },
    studentApi: { ...orig.studentApi, ...student },
  };
});

import { setToken } from "../api/client";
import { closesAtLabel } from "../components/teacher/OpenTo";
import { offlineDeadline } from "../lib/offline";
import ExamsPage from "../pages/student/ExamsPage";
import { LiveRoom } from "../pages/teacher/LiveRoom";
import TeacherExamPage from "../pages/teacher/TeacherExamPage";
import { draftFromExam, toExamInput } from "../pages/teacher/examDraft";

const STUDENTS: ExamStudent[] = [
  { id: 1, username: "alice", name: "Alice Chen", started: false },
  { id: 2, username: "bob", name: "Bob Karimov", started: false },
  { id: 3, username: "carol", name: "Carol Wang", started: true },
];

function exam(over: Partial<Exam> = {}): Exam {
  return {
    id: "e_1", title: "Midterm", duration_min: 30, status: "draft", start_code: null, questions: [], created_at: "",
    question_count: 3, total_points: 10, course: "", room: "", scheduled_at: null, shuffle_questions: false,
    grades_released: false, released_at: null, closed_at: null, pending_grading: 0, class_id: 7,
    open_to: "all", closes_at: null, auto_closed: false, external: null,
    ...over,
  } as Exam;
}

function liveData(over: Partial<LiveRoomData> = {}): LiveRoomData {
  return {
    exam_id: "e_1", status: "live", start_code: "123456", duration_min: 30, students: [], flags: [],
    counts: { writing: 0, submitted: 0, offline: 0, needs_look: 0 }, server_time: new Date().toISOString(),
    open_to: "all", selected: null, closes_at: null,
    ...over,
  };
}

function renderExamPage() {
  setToken("teacher", "T");
  return render(
    <MemoryRouter initialEntries={["/teacher/exams/e_1"]}>
      <Routes>
        <Route path="/teacher/exams/:id" element={<TeacherExamPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  teacher.examStudents.mockResolvedValue(STUDENTS);
  teacher.readiness.mockResolvedValue([]);
  teacher.live.mockResolvedValue(liveData());
});
afterEach(() => {
  vi.clearAllMocks();
});

describe("open dialog: everyone or selected students", () => {
  it("opens for everyone by default (no body, as before)", async () => {
    const user = userEvent.setup();
    teacher.getExam.mockResolvedValue(exam());
    teacher.openExam.mockResolvedValue(exam({ status: "live", start_code: "123456" }));
    renderExamPage();
    const choice = await screen.findByTestId("open-to-choice");
    expect(within(choice).getByRole("radio", { name: /Everyone/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Open exam" }));
    await waitFor(() => expect(teacher.openExam).toHaveBeenCalledWith("e_1"));
    expect(teacher.examStudents).not.toHaveBeenCalled();     // the list loads only when needed
  });

  it("opens for the ticked students, with search", async () => {
    const user = userEvent.setup();
    teacher.getExam.mockResolvedValue(exam());
    teacher.openExam.mockResolvedValue(exam({ status: "live", start_code: "123456", open_to: [2] }));
    renderExamPage();
    await user.click(await screen.findByRole("radio", { name: /Selected students/ }));
    const list = await screen.findByTestId("student-checklist");
    // nobody ticked yet: can't open
    expect(screen.getByRole("button", { name: "Open exam" })).toBeDisabled();
    await user.type(within(list).getByRole("searchbox", { name: "Search students" }), "bob");
    expect(within(list).queryByText("Alice Chen")).toBeNull();
    await user.click(within(list).getByRole("checkbox", { name: /Bob Karimov/ }));
    expect(within(list).getByText("1 student selected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open exam" }));
    await waitFor(() => expect(teacher.openExam).toHaveBeenCalledWith("e_1", [2]));
  });

  it("shows started students in the list", async () => {
    const user = userEvent.setup();
    teacher.getExam.mockResolvedValue(exam({ status: "closed" }));
    renderExamPage();
    await user.click(await screen.findByRole("radio", { name: /Selected students/ }));
    const list = await screen.findByTestId("student-checklist");
    expect(within(list).getByText("carol · started")).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "select all" }));
    expect(within(list).getByText("3 students selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-open exam" })).toBeEnabled();
  });
});

describe("live room: change the selection", () => {
  it("adds and removes students with one save", async () => {
    const user = userEvent.setup();
    teacher.live.mockResolvedValue(
      liveData({ open_to: [1], selected: [{ id: 1, username: "alice", name: "Alice Chen", started: true }] }),
    );
    teacher.updateOpenTo.mockResolvedValue(exam({ status: "live", open_to: [2] }));
    const onExamChange = vi.fn();
    render(
      <MemoryRouter>
        <LiveRoom exam={exam({ status: "live", start_code: "123456", open_to: [1] })} onClosed={vi.fn()} onExamChange={onExamChange} />
      </MemoryRouter>,
    );
    const editor = await screen.findByTestId("open-to-editor");
    await waitFor(() =>
      expect(within(editor).getByTestId("open-to-summary")).toHaveTextContent("Only 1 selected student: Alice Chen"),
    );
    await user.click(within(editor).getByRole("button", { name: "change" }));
    const list = await within(editor).findByTestId("student-checklist");
    expect(within(list).getByRole("checkbox", { name: /Alice Chen/ })).toBeChecked();
    await user.click(within(list).getByRole("checkbox", { name: /Alice Chen/ }));
    await user.click(within(list).getByRole("checkbox", { name: /Bob Karimov/ }));
    expect(within(editor).getByText(/keep writing, even if you remove them/)).toBeInTheDocument();
    await user.click(within(editor).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(teacher.updateOpenTo).toHaveBeenCalledWith("e_1", { add: [2], remove: [1] }));
    expect(onExamChange).toHaveBeenCalled();
  });

  it("switches back to everyone", async () => {
    const user = userEvent.setup();
    teacher.live.mockResolvedValue(liveData({ open_to: [1], selected: [] }));
    teacher.updateOpenTo.mockResolvedValue(exam({ status: "live" }));
    render(
      <MemoryRouter>
        <LiveRoom exam={exam({ status: "live", start_code: "123456" })} onClosed={vi.fn()} onExamChange={vi.fn()} />
      </MemoryRouter>,
    );
    const editor = await screen.findByTestId("open-to-editor");
    await waitFor(() => expect(within(editor).getByTestId("open-to-summary")).toHaveTextContent("Only 1 selected"));
    await user.click(within(editor).getByRole("button", { name: "change" }));
    await user.click(within(editor).getByRole("radio", { name: /Everyone/ }));
    await user.click(within(editor).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(teacher.updateOpenTo).toHaveBeenCalledWith("e_1", { open_to: "all" }));
  });
});

describe("student: not open for you yet", () => {
  function row(over: Partial<StudentExamSummary> = {}): StudentExamSummary {
    return {
      id: "e_1", title: "Midterm", duration_min: 30, question_count: 3, status: "live", course: "", room: "",
      scheduled_at: null, attempt: null, open_for_me: false, ...over,
    };
  }

  it("lists the exam without a way to start it", async () => {
    setToken("student", "S");
    student.listExams.mockResolvedValue([row(), row({ id: "e_2", title: "Quiz", open_for_me: true })]);
    render(
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>,
    );
    const mid = (await screen.findByText("Midterm")).closest("article")!;
    expect(within(mid).getByText("This exam isn't open for you yet")).toBeInTheDocument();
    expect(within(mid).queryByRole("link", { name: "Check in" })).toBeNull();
    const quiz = screen.getByText("Quiz").closest("article")!;
    expect(within(quiz).getByRole("link", { name: "Check in" })).toBeInTheDocument();
  });

  it("an attempt already started can always be continued", async () => {
    setToken("student", "S");
    student.listExams.mockResolvedValue([
      row({ open_for_me: true, attempt: { attempt_id: "a_1", status: "in_progress", submitted_at: null, receipt: null, released: false, percent: null } }),
    ]);
    render(
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.queryByText("This exam isn't open for you yet")).toBeNull();
  });
});

describe("automatic close label", () => {
  it("shows the close time on the exam page and in the live room", async () => {
    const at = "2026-12-01T10:00:00.000Z";
    teacher.getExam.mockResolvedValue(exam({ closes_at: at }));
    renderExamPage();
    expect(await screen.findByText(/Closes automatically at/)).toBeInTheDocument();

    teacher.live.mockResolvedValue(liveData({ closes_at: at }));
    render(
      <MemoryRouter>
        <LiveRoom exam={exam({ status: "live", start_code: "123456", closes_at: at })} onClosed={vi.fn()} onExamChange={vi.fn()} />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("auto-close-label")).toHaveTextContent("Closes automatically at");
  });

  it("is read-only and names the source for external exams", () => {
    const ext = { source: "campusjudge", name: "Campus Judge", url: null, read_only: true, graded_externally: true, package_ready: true };
    const label = closesAtLabel({ closes_at: "2026-12-01T10:00:00Z", external: ext } as Exam);
    expect(label).toMatch(/^Closes automatically at .*\(set in Campus Judge\)$/);
    expect(closesAtLabel({ closes_at: null, external: null } as Exam)).toBeNull();
  });

  it("the editor sends Close automatically at, or null", () => {
    const d = draftFromExam(exam({ closes_at: "2026-12-01T10:00:00.000Z" }));
    expect(d.closeDate).not.toBe("");
    expect(toExamInput(d).closes_at).toBe("2026-12-01T10:00:00.000Z");
    expect(toExamInput({ ...d, closeDate: "", closeTime: "" }).closes_at).toBeNull();
  });

  it("an offline start never counts down past the close time", () => {
    const now = Date.parse("2026-12-01T09:50:00Z");
    expect(offlineDeadline({ duration_min: 30, closes_at: "2026-12-01T10:00:00Z" }, now)).toBe(Date.parse("2026-12-01T10:00:00Z"));
    expect(offlineDeadline({ duration_min: 30, closes_at: null }, now)).toBe(now + 30 * 60_000);
  });
});
