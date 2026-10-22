// The FairPlay logo (mark + name) sits at the top of every app screen:
// teacher nav, sign-in, student top nav and the exam bar (the Mac, Windows and iPad apps
// show these pages).
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LogoMark, Wordmark } from "../components/ui/Icon";
import { StudentLogo } from "../components/student/StudentLayout";
import { LockedBar } from "../components/student/LockedBar";

describe("FairPlay logo", () => {
  it("draws the split circle on the brand blue square", () => {
    const { container } = render(<LogoMark size={40} />);
    const svg = container.querySelector("svg.fp-mark")!;
    expect(svg.getAttribute("width")).toBe("40");
    expect(svg.querySelector("rect")!.getAttribute("fill")).toBe("#2F4BD8");
    expect([...svg.querySelectorAll("path")].map((p) => p.getAttribute("d"))).toEqual([
      "M29.7 13.64A18.5 18.5 0 0 0 29.7 50.36Z",
      "M34.3 13.64A18.5 18.5 0 0 1 34.3 50.36Z",
    ]);
    expect(svg.getAttribute("aria-hidden")).toBe("true");
  });

  it("shows the name next to the mark", () => {
    const { container } = render(<Wordmark />);
    expect(container.querySelector(".fp-wordmark")!.textContent).toBe("FairPlay");
  });

  it("is in the student top nav", () => {
    const { container } = render(
      <MemoryRouter>
        <StudentLogo />
      </MemoryRouter>,
    );
    expect(container.querySelector("a.stu-logo svg.fp-mark")).not.toBeNull();
    expect(screen.getByRole("link", { name: "FairPlay home" }).textContent).toBe("FairPlay");
  });

  it("is in the exam bar, named for screen readers", () => {
    render(<LockedBar title="Midterm" locked native sync="synced" remaining={60_000} />);
    const bar = screen.getByRole("banner");
    // the logo opens the left group of the bar
    expect(bar.firstElementChild?.firstElementChild?.classList.contains("fp-mark")).toBe(true);
    expect(screen.getByRole("img", { name: "FairPlay" })).toBeTruthy();
  });
});
