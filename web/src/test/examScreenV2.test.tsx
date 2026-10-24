import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { AnswerValue, StudentQuestion } from "../api/types";
import { AnswerInput } from "../components/student/AnswerInput";
import { LockedBar } from "../components/student/LockedBar";
import { batteryTone } from "../lib/battery";
import {
  stepTextSize,
  TEXT_SIZES,
  readCrossed,
  readTextSize,
  readTimerHidden,
  toggleCrossed,
  uncross,
  writeCrossed,
  writeTextSize,
  writeTimerHidden,
} from "../lib/examPrefs";
import { examDirections } from "../pages/student/ExamTakingPage";

const mcq: StudentQuestion = {
  id: "q1",
  type: "mcq",
  prompt: "2 + 2 = ?",
  points: 1,
  options: ["3", "4", "5", "22"],
} as unknown as StudentQuestion;

function Mcq({ crossOn = true }: { crossOn?: boolean }) {
  const [value, setValue] = useState<AnswerValue | undefined>(undefined);
  const [crossed, setCrossed] = useState<number[]>([]);
  return (
    <>
      <h1 id="p">2 + 2 = ?</h1>
      <AnswerInput
        question={mcq}
        value={value}
        onChange={setValue}
        labelledBy="p"
        cross={{ on: crossOn, crossed, onToggle: (i) => setCrossed((c) => (c.includes(i) ? c.filter((x) => x !== i) : [...c, i])) }}
      />
      <output data-testid="value">{value === undefined ? "none" : String(value)}</output>
    </>
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("exam preferences (this device only)", () => {
  it("keeps crossed-out options per attempt and cleans bad data", () => {
    let c = toggleCrossed({}, "q1", 2);
    c = toggleCrossed(c, "q1", 0);
    expect(c.q1).toEqual([0, 2]);
    writeCrossed("a1", c);
    expect(readCrossed("a1")).toEqual({ q1: [0, 2] });
    expect(readCrossed("a2")).toEqual({});
    c = toggleCrossed(c, "q1", 2);
    expect(c.q1).toEqual([0]);
    expect(uncross(c, "q1", 0).q1).toEqual([]);
    writeCrossed("a1", uncross(c, "q1", 0));
    expect(localStorage.getItem("fairplay:crossed:a1")).toBeNull();
    localStorage.setItem("fairplay:crossed:a3", JSON.stringify({ q: [1, "x", -2, 1.5, 1] }));
    expect(readCrossed("a3")).toEqual({ q: [1] });
    localStorage.setItem("fairplay:crossed:a4", "not json");
    expect(readCrossed("a4")).toEqual({});
  });

  it("remembers the hidden timer per attempt and the text size for every exam", () => {
    writeTimerHidden("a1", true);
    expect(readTimerHidden("a1")).toBe(true);
    expect(readTimerHidden("a2")).toBe(false);
    expect(readTextSize()).toBe(1);
    expect(stepTextSize(1, 1)).toBe(1.15);
    expect(stepTextSize(1, -1)).toBe(0.9);
    // 2026-10-24: no wrap-around at the ends
    expect(stepTextSize(1.5, 1)).toBe(1.5);
    expect(stepTextSize(0.9, -1)).toBe(0.9);
    expect([...TEXT_SIZES]).toEqual([0.9, 1, 1.15, 1.3, 1.5]);
    writeTextSize(1.3);
    expect(readTextSize()).toBe(1.3);
    localStorage.setItem("fairplay:textsize", "9");
    expect(readTextSize()).toBe(1);
  });
});

describe("cross out on multiple choice", () => {
  it("crosses out and brings back an option without choosing it", () => {
    render(<Mcq />);
    fireEvent.click(screen.getByRole("button", { name: "Cross out A" }));
    expect(screen.getByRole("button", { name: "Undo cross out A" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("value").textContent).toBe("none");
    fireEvent.click(screen.getByRole("button", { name: "Undo cross out A" }));
    expect(screen.getByRole("button", { name: "Cross out A" })).toHaveAttribute("aria-pressed", "false");
  });

  it("choosing a crossed-out option un-crosses it", () => {
    render(<Mcq />);
    fireEvent.click(screen.getByRole("button", { name: "Cross out C" }));
    fireEvent.click(screen.getAllByRole("radio")[2]!);
    expect(screen.getByTestId("value").textContent).toBe("2");
    expect(screen.getByRole("button", { name: "Cross out C" })).toBeInTheDocument();
  });

  it("crossing out the chosen option clears the answer", () => {
    render(<Mcq />);
    fireEvent.click(screen.getAllByRole("radio")[1]!);
    expect(screen.getByTestId("value").textContent).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Cross out B" }));
    expect(screen.getByTestId("value").textContent).toBe("none");
    expect(screen.getByRole("button", { name: "Undo cross out B" })).toBeInTheDocument();
  });

  it("has no cross-out buttons when the tool is off", () => {
    render(<Mcq crossOn={false} />);
    expect(screen.queryByRole("button", { name: /Cross out/ })).toBeNull();
    expect(screen.getByText("Click an option to choose it. Click it again to clear.")).toBeInTheDocument();
  });
});

describe("exam top bar", () => {
  it("hides the timer on request and shows it again in the last 5 minutes", () => {
    function Bar({ remaining }: { remaining: number }) {
      const [hidden, setHidden] = useState(false);
      return <LockedBar title="Midterm" locked native sync="synced" remaining={remaining} timerHidden={hidden} onTimerHidden={setHidden} />;
    }
    const { rerender } = render(<Bar remaining={20 * 60_000} />);
    expect(screen.getByRole("timer", { name: "Time left" }).textContent).toBe("20:00");
    // layout D: an eye button named "Hide time" / "Show time"
    fireEvent.click(screen.getByRole("button", { name: "Hide time" }));
    expect(screen.getByRole("timer", { name: "Timer hidden" }).textContent).toBe("");
    expect(screen.getByRole("button", { name: "Show time" })).toHaveAttribute("aria-pressed", "true");
    rerender(<Bar remaining={4 * 60_000} />);
    expect(screen.getByRole("timer", { name: "Time left" }).textContent).toBe("4:00");
    expect(screen.queryByRole("button", { name: "Show time" })).toBeNull();
  });

  it("opens Directions and the More menu (text size, help)", () => {
    let size = 0 as number;
    render(
      <LockedBar
        title="Midterm"
        course="MATH 201"
        locked
        native
        sync="synced"
        remaining={60 * 60_000}
        directions={examDirections(60, 22)}
        textSize={1}
        onTextSize={(n) => (size = n)}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Directions" }));
    expect(screen.getByText("22 questions · 60 minutes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByText("22 questions · 60 minutes.")).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Larger text" }));
    expect(size).toBe(1.15);
    // the menu stays open for the next step
    expect(screen.getByRole("menu", { name: "More" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    expect(screen.getByRole("dialog", { name: "Help" })).toHaveTextContent("Raise your hand");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull();
  });

  it("shows the save status in our words (or leaves it to the question screen's footer)", () => {
    const { rerender } = render(<LockedBar title="Midterm" locked native sync="synced" remaining={60_000} />);
    expect(screen.getByText("All answers saved")).toBeInTheDocument();
    rerender(<LockedBar title="Midterm" locked native sync="pending" remaining={60_000} />);
    expect(screen.getByText("Saved on this computer")).toBeInTheDocument();
    rerender(<LockedBar title="Midterm" locked native sync="pending" remaining={60_000} showSync={false} />);
    expect(screen.queryByText("Saved on this computer")).toBeNull();
  });

  it("shows the battery from the app and warns when low", () => {
    (window as unknown as { fairplayBattery?: unknown }).fairplayBattery = { level: 0.14, charging: false };
    render(<LockedBar title="Midterm" locked native sync="synced" remaining={60_000} />);
    expect(screen.getByLabelText("Battery 14%, plug in your charger")).toHaveClass("low");
    delete (window as unknown as { fairplayBattery?: unknown }).fairplayBattery;
  });

  it("warns once when the battery drops under 10 %", () => {
    const w = window as unknown as { fairplayBattery?: unknown };
    w.fairplayBattery = { level: 0.08, charging: false };
    render(<LockedBar title="Midterm" locked native sync="synced" remaining={60_000} />);
    expect(screen.getByRole("alertdialog", { name: "Battery low" })).toHaveTextContent("Battery 8%");
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByRole("alertdialog", { name: "Battery low" })).toBeNull();
    delete w.fairplayBattery;
  });

  it("rates the battery", () => {
    expect(batteryTone({ level: 0.5, charging: false })).toBe("ok");
    expect(batteryTone({ level: 0.15, charging: false })).toBe("low");
    expect(batteryTone({ level: 0.05, charging: false })).toBe("critical");
    expect(batteryTone({ level: 0.05, charging: true })).toBe("ok");
  });
});
