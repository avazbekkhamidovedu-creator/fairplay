// S4 (2026-10-20): 409 object detail, swap request from the second computer, Approve swap.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    studentApi: { ...actual.studentApi, start: vi.fn(), listExams: vi.fn(), swapRequest: vi.fn() },
  };
});

import { ApiError, SwapNeededError, request, setToken, studentApi, teacherApi } from "../api/client";
import { isSessionConflict } from "../components/student/ReenterExam";
import { StartCodeForm, startErrorMessage } from "../components/student/StartCodeForm";
import { SeatBadges } from "../pages/teacher/LiveRoom";
import type { LiveStudent } from "../api/types";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const MSG = "This exam is already open on another computer. Ask the invigilator to move it to this one.";

describe("swap (S4)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("an object detail becomes a readable message plus a code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { code: "swap_needed", message: MSG } }), { status: 409 })));
    const err = (await request("student", "POST", "/api/student/exams/e_1/start", {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBe(MSG);
    expect(err.code).toBe("swap_needed");
    // every caller that reads detail as text still works
    expect(isSessionConflict(err.status, err.detail)).toBe(true);
    expect(startErrorMessage(err)).toBe(MSG);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { code: "already_submitted" } }), { status: 409, statusText: "Conflict" })));
    const e2 = (await request("student", "POST", "/x", {}).catch((e: unknown) => e)) as ApiError;
    expect(e2.code).toBe("already_submitted");
    expect(e2.detail).toBe("Conflict");
  });

  it("the second computer asks for a swap and starts once approved", async () => {
    const user = userEvent.setup();
    api.start
      .mockRejectedValueOnce(new SwapNeededError(MSG, "hw-bbbb2222"))
      .mockRejectedValueOnce(new SwapNeededError(MSG, "hw-bbbb2222"))
      .mockResolvedValueOnce({ attempt_id: "a_1" });
    api.listExams.mockResolvedValue([{ id: "e_1", attempt: { attempt_id: "a_1", status: "in_progress" } }]);
    api.swapRequest.mockResolvedValue({ ok: true });
    render(
      <MemoryRouter initialEntries={["/s"]}>
        <Routes>
          <Route path="/s" element={<StartCodeForm examId="e_1" />} />
          <Route path="/student/exam/:aid" element={<p>exam page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    for (const [i, d] of "123456".split("").entries()) {
      await user.type(screen.getByLabelText(new RegExp(`Digit ${i + 1}`)), d);
    }
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByTestId("swap-waiting")).toBeInTheDocument();
    expect(api.swapRequest).toHaveBeenCalledWith("a_1", "hw-bbbb2222");
    await vi.advanceTimersByTimeAsync(5000);     // still not approved
    await vi.advanceTimersByTimeAsync(5000);     // approved: binds and opens the exam
    expect(await screen.findByText("exam page")).toBeInTheDocument();
    expect(api.start).toHaveBeenCalledTimes(3);
    expect(api.swapRequest).toHaveBeenCalledTimes(1);
  });

  it("a browser (no device id) just shows the message", async () => {
    const user = userEvent.setup();
    api.start.mockRejectedValueOnce(new SwapNeededError(MSG, null));
    render(
      <MemoryRouter>
        <StartCodeForm examId="e_1" />
      </MemoryRouter>,
    );
    for (const [i, d] of "123456".split("").entries()) {
      await user.type(screen.getByLabelText(new RegExp(`Digit ${i + 1}`)), d);
    }
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByText(MSG)).toBeInTheDocument();
    expect(api.swapRequest).not.toHaveBeenCalled();
  });

  it("Approve swap in the live room", async () => {
    setToken("teacher", "T");
    const f = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    const s = { attempt_id: "a_1", name: "Alice", status: "in_progress", swap_requested: true } as LiveStudent;
    render(<SeatBadges examId="e_1" student={s} onChanged={vi.fn()} />);
    expect(screen.getByText("Wants to continue on another computer")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve swap for Alice" }));
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe("/api/exams/e_1/attempts/a_1/approve-swap");
    expect(teacherApi.approveSwap).toBeTypeOf("function");
  });
});
