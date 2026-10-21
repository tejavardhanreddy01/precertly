import { render, screen } from "@testing-library/react";
import { PILL_TEXT, Pill, type PillState } from "./Pill";

const STATES = Object.keys(PILL_TEXT) as PillState[];

describe("Pill", () => {
  it("has the five verdict states and not applicable", () => {
    expect(STATES.sort()).toEqual(
      ["false_approval", "insufficient", "invalidated", "met", "not_applicable", "not_met"].sort(),
    );
  });

  for (const state of STATES) {
    it(`${state} renders its words and an icon`, () => {
      const { container } = render(<Pill state={state} />);
      const pill = container.querySelector(`[data-outcome="${state}"]`);
      expect(pill).not.toBeNull();
      expect(pill).toHaveTextContent(PILL_TEXT[state]);
      expect(screen.getByText(PILL_TEXT[state])).toBeInTheDocument();
      const icon = pill?.querySelector("svg");
      expect(icon).not.toBeNull();
      // The icon is decoration: the words are what a screen reader reads.
      expect(icon).toHaveAttribute("aria-hidden", "true");
      expect(icon?.querySelector("path, circle, rect")).not.toBeNull();
    });
  }

  it("never relies on color: every state has its own words and its own icon shape", () => {
    const words = new Set<string>();
    const shapes = new Set<string>();
    for (const state of STATES) {
      const { container, unmount } = render(<Pill state={state} />);
      words.add(container.textContent ?? "");
      shapes.add(container.querySelector("svg")?.innerHTML ?? "");
      unmount();
    }
    expect(words.size).toBe(STATES.length);
    expect(shapes.size).toBe(STATES.length);
  });

  it("puts a prefix before the words", () => {
    render(<Pill state="met" prefix="Label:" />);
    expect(screen.getByText("Label: Met")).toBeInTheDocument();
  });
});
