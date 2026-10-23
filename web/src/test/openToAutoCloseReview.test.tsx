// Review of open-to / automatic close: the editor's close time checks, names shown as text.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ExamStudent } from "../api/types";
import { StudentChecklist } from "../components/teacher/OpenTo";
import { draftFromExam, validateDraft } from "../pages/teacher/examDraft";

function draft(over: Record<string, string> = {}) {
  return { ...draftFromExam(), title: "Midterm", ...over };
}

describe("Close automatically at: editor checks", () => {
  it("needs a time with the date (no silent 09:00)", () => {
    expect(validateDraft(draft({ closeDate: "2026-12-01", closeTime: "" })).closes).toBe(
      "Pick a time for the automatic close.",
    );
    expect(validateDraft(draft({ closeDate: "", closeTime: "11:00" })).closes).toBe("Pick a date for the automatic close.");
    expect(validateDraft(draft({ closeDate: "2026-12-01", closeTime: "11:00" })).closes).toBeUndefined();
  });

  it("must come after the planned start", () => {
    const d = draft({ date: "2026-12-01", time: "10:00", closeDate: "2026-12-01", closeTime: "09:30" });
    expect(validateDraft(d).closes).toBe("The automatic close must be after the start time.");
    expect(validateDraft({ ...d, closeTime: "10:00" }).closes).toBe("The automatic close must be after the start time.");
    expect(validateDraft({ ...d, closeTime: "12:00" }).closes).toBeUndefined();
  });

  it("is ignored for paper exams", () => {
    expect(validateDraft({ ...draft({ closeDate: "2026-12-01", closeTime: "" }), delivery: "paper" }).closes).toBeUndefined();
  });
});

describe("student names", () => {
  it("are shown as text, never as markup", async () => {
    const user = userEvent.setup();
    const evil: ExamStudent[] = [
      { id: 9, username: "<b>x</b>", name: '<img src=x onerror="alert(1)">', started: false },
    ];
    const onChange = vi.fn();
    const { container } = render(<StudentChecklist students={evil} selected={[]} onChange={onChange} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    const list = screen.getByTestId("student-checklist");
    expect(within(list).getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
    await user.type(within(list).getByRole("searchbox"), "<img");
    expect(within(list).getByRole("checkbox", { name: /onerror/ })).toBeInTheDocument();
  });
});
