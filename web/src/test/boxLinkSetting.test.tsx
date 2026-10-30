import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Exam, ExamInput } from "../api/types";

const api = vi.hoisted(() => ({ updateExam: vi.fn(), createExam: vi.fn(), openExam: vi.fn(), toolSettings: vi.fn() }));

vi.mock("../api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/client")>();
  return {
    ...orig,
    teacherApi: { ...orig.teacherApi, ...api },
    slice6Api: { ...orig.slice6Api, topics: vi.fn().mockResolvedValue([]) },
    classesApi: { ...orig.classesApi, list: vi.fn().mockResolvedValue([]) },
  };
});

import { ApiError } from "../api/client";
import { boxLinkProblem, BOX_LINK_MESSAGE } from "../lib/boxLink";
import { ExamEditor } from "../pages/teacher/ExamEditor";

const LINK = "https://duke.box.com/s/AbCd1234efgh";
const OTHER = "https://duke.app.box.com/s/Zz9Yy8Xx7Ww6";

function exam(over: Partial<Exam> = {}): Exam {
  return {
    id: "e_1",
    title: "Quiz",
    duration_min: 20,
    status: "draft",
    start_code: null,
    questions: [
      {
        id: "q1", type: "mcq", prompt: "Pick one", points: 1, options: ["a", "b"], correct_index: 0,
        answer_number: null, tolerance: null, accepted_answers: [], rubric: [], min_words: null, max_words: null, topics: ["algebra"],
      },
    ],
    created_at: "2026-10-01T10:00:00Z",
    question_count: 1,
    total_points: 1,
    course: "MATH 201",
    room: "AB 1087",
    scheduled_at: null,
    shuffle_questions: false,
    grades_released: false,
    released_at: null,
    closed_at: null,
    pending_grading: 0,
    ...over,
  } as Exam;
}

function renderEditor(e: Exam) {
  return render(
    <MemoryRouter>
      <ExamEditor exam={e} onSaved={vi.fn()} cancelTo="/teacher" />
    </MemoryRouter>,
  );
}

const field = () => screen.getByLabelText("Duke Box shared folder link") as HTMLInputElement;
const sentInput = () => api.updateExam.mock.calls[api.updateExam.mock.calls.length - 1][1] as ExamInput;

beforeEach(() => {
  api.updateExam.mockReset();
  api.toolSettings.mockReset();
  api.updateExam.mockImplementation(async (_id: string, input: ExamInput) => exam({ box_link: input.box_link ?? null }));
});

describe("Duke Box folder setting", () => {
  it("renders empty without a saved link and filled with one", () => {
    const a = renderEditor(exam());
    expect(field().value).toBe("");
    expect(field().placeholder).toBe("https://duke.box.com/s/…");
    expect(screen.getByText(/Students open this folder in a secure window/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    a.unmount();
    renderEditor(exam({ box_link: LINK }));
    expect(field().value).toBe(LINK);
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("sends a typed valid link exactly", async () => {
    renderEditor(exam());
    await userEvent.type(field(), OTHER);
    expect(screen.queryByText(BOX_LINK_MESSAGE)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledTimes(1));
    expect(sentInput().box_link).toBe(OTHER);
  });

  it("shows the client message for an invalid link and does not save", async () => {
    renderEditor(exam());
    await userEvent.type(field(), "https://example.com/s/abc");
    expect(screen.getByText(BOX_LINK_MESSAGE)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    expect(api.updateExam).not.toHaveBeenCalled();
  });

  it("clearing sends null", async () => {
    renderEditor(exam({ box_link: LINK }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(field().value).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledTimes(1));
    expect(sentInput().box_link).toBeNull();
  });

  it("leaves box_link out when the link did not change", async () => {
    renderEditor(exam({ box_link: LINK }));
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledTimes(1));
    expect("box_link" in sentInput()).toBe(false);
  });

  it("shows the server's 400 message without the field prefix", async () => {
    api.updateExam.mockRejectedValue(new ApiError(400, "box_link: That folder link is not a shared link."));
    renderEditor(exam());
    await userEvent.type(field(), LINK);
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    expect(await screen.findByText("That folder link is not a shared link.")).toBeInTheDocument();
    expect(screen.queryByText(/box_link:/)).toBeNull();
    expect(field().disabled).toBe(false);
  });

  it("shows the 409 message and disables the input", async () => {
    const msg = "The Duke Box folder cannot be changed after students have started.";
    api.updateExam.mockRejectedValue(new ApiError(409, msg));
    renderEditor(exam({ box_link: LINK }));
    await userEvent.clear(field());
    await userEvent.type(field(), OTHER);
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    expect(await screen.findByText(msg)).toBeInTheDocument();
    expect(field().disabled).toBe(true);
  });
});

describe("boxLinkProblem", () => {
  it("accepts only duke.box.com and duke.app.box.com shared links", () => {
    expect(boxLinkProblem("")).toBeNull();
    expect(boxLinkProblem(LINK)).toBeNull();
    expect(boxLinkProblem(OTHER)).toBeNull();
    expect(boxLinkProblem("https://duke.box.com/s/abc")).toBe(BOX_LINK_MESSAGE);
    expect(boxLinkProblem("https://duke.box.com/s/" + "a".repeat(65))).toBe(BOX_LINK_MESSAGE);
    for (const bad of ["http://duke.box.com/s/abc", "https://duke.box.com/folder/1", "https://evil.com/s/abc", "https://duke.box.com.evil.com/s/abc", "https://duke.box.com/s/"])
      expect(boxLinkProblem(bad)).toBe(BOX_LINK_MESSAGE);
  });
});

describe("Duke Box folder on exams that are not drafts or come from DKU OJ", () => {
  const live = () => exam({ status: "live", box_link: LINK });
  const dku = () => exam({ source: "dkuoj", mode: "questions", status: "draft", box_link: null, websites: ["docs.python.org"] });
  const saveBtn = () => screen.getByRole("button", { name: "Save Duke Box folder" });

  it("uses PATCH, not PUT, for a live exam and sends only the changed field", async () => {
    api.toolSettings.mockResolvedValue(exam({ status: "live", box_link: OTHER }));
    renderEditor(live());
    expect(saveBtn()).toBeDisabled();
    await userEvent.clear(field());
    await userEvent.type(field(), OTHER);
    await userEvent.click(saveBtn());
    await waitFor(() => expect(api.toolSettings).toHaveBeenCalledTimes(1));
    expect(api.toolSettings).toHaveBeenCalledWith("e_1", { box_link: OTHER });
    await waitFor(() => expect(saveBtn()).toBeDisabled());
    expect(field().value).toBe(OTHER);
    expect(api.updateExam).not.toHaveBeenCalled();
  });

  it("clearing sends null", async () => {
    api.toolSettings.mockResolvedValue(exam({ status: "live", box_link: null }));
    renderEditor(live());
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    await userEvent.click(saveBtn());
    await waitFor(() => expect(api.toolSettings).toHaveBeenCalledWith("e_1", { box_link: null }));
  });

  it("a DKU OJ exam saves the link and the websites by PATCH with only what changed", async () => {
    api.toolSettings.mockResolvedValue(exam({ source: "dkuoj", mode: "questions", box_link: LINK }));
    renderEditor(dku());
    expect(screen.getByText("docs.python.org")).toBeInTheDocument();
    await userEvent.type(field(), LINK);
    await userEvent.click(saveBtn());
    await waitFor(() => expect(api.toolSettings).toHaveBeenCalledTimes(1));
    expect(api.toolSettings).toHaveBeenLastCalledWith("e_1", { box_link: LINK });

    await userEvent.type(screen.getByLabelText("Website to allow"), "example.org{Enter}");
    await userEvent.click(saveBtn());
    await waitFor(() => expect(api.toolSettings).toHaveBeenCalledTimes(2));
    expect(api.toolSettings).toHaveBeenLastCalledWith("e_1", { websites: ["docs.python.org", "example.org"] });
    expect(api.updateExam).not.toHaveBeenCalled();
  });

  it("shows the 400 message without the prefix and keeps the input enabled", async () => {
    api.toolSettings.mockRejectedValue(new ApiError(400, "box_link: That folder link is not a shared link."));
    renderEditor(live());
    await userEvent.clear(field());
    await userEvent.type(field(), OTHER);
    await userEvent.click(saveBtn());
    expect(await screen.findByText("That folder link is not a shared link.")).toBeInTheDocument();
    expect(field().disabled).toBe(false);
  });

  it("shows the 409 message and disables the input", async () => {
    const msg = "The Duke Box folder cannot be changed after students have started.";
    api.toolSettings.mockRejectedValue(new ApiError(409, msg));
    renderEditor(live());
    await userEvent.clear(field());
    await userEvent.type(field(), OTHER);
    await userEvent.click(saveBtn());
    expect(await screen.findByText(msg)).toBeInTheDocument();
    expect(field().disabled).toBe(true);
  });

  it("a draft native exam still saves through PUT and has no separate button", async () => {
    renderEditor(exam());
    expect(screen.queryByRole("button", { name: "Save Duke Box folder" })).toBeNull();
    await userEvent.type(field(), LINK);
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledTimes(1));
    expect(sentInput().box_link).toBe(LINK);
    expect(api.toolSettings).not.toHaveBeenCalled();
  });

  it("a website exam has no Duke Box section and never sends box_link", async () => {
    api.updateExam.mockResolvedValue(exam({ mode: "external" }));
    renderEditor(exam({ mode: "external", status: "draft", external_url: "https://quiz.example.edu/x", questions: [], box_link: null }));
    expect(screen.queryByLabelText("Duke Box shared folder link")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save Duke Box folder" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save exam" }));
    for (const c of api.updateExam.mock.calls) expect("box_link" in (c[1] as object)).toBe(false);
    expect(api.toolSettings).not.toHaveBeenCalled();
  });
});
