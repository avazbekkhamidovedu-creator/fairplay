// 0.6.2 second review: a busy server at the start rush (503) is retried online; the
// student is not silently moved to the offline package.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, studentApi: { listExams: vi.fn(), start: vi.fn() } };
});

import { ServerBusyError, studentApi } from "../api/client";
import { StartCodeForm, START_BUSY_WAIT_MS } from "../components/student/StartCodeForm";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderForm() {
  return render(
    <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
      <Routes>
        <Route path="/student/exams/:examId/start" element={<StartCodeForm examId="e_1" legend="Start code" />} />
        <Route path="/student/exam/:aid" element={<p>exam page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("start while the server is busy", () => {
  it("tries again online and then opens the exam", async () => {
    api.start
      .mockRejectedValueOnce(new ServerBusyError())
      .mockRejectedValueOnce(new ServerBusyError())
      .mockResolvedValueOnce({ attempt_id: "a_1" });
    renderForm();
    for (let i = 1; i <= 6; i++) {
      fireEvent.change(screen.getByRole("textbox", { name: `Digit ${i} of 6` }), { target: { value: String(i) } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByText(/server is busy. Trying again/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(START_BUSY_WAIT_MS * 3 + 100);
    });
    expect(await screen.findByText("exam page")).toBeInTheDocument();
    expect(api.start).toHaveBeenCalledTimes(3);
  });
});
