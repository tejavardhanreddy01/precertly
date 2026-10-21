import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { SegmentedTabs } from "./SegmentedTabs";

const TABS = [
  { id: "criteria", label: "Criteria", controls: "pane-criteria" },
  { id: "evidence", label: "Evidence", controls: "pane-evidence" },
  { id: "verdict", label: "Verdict", controls: "pane-verdict" },
] as const;

type Id = (typeof TABS)[number]["id"];

function Harness({ start = "criteria" }: { start?: Id }) {
  const [value, setValue] = useState<Id>(start);
  return <SegmentedTabs tabs={TABS} value={value} onChange={setValue} label="Case sections" />;
}

const tab = (name: string) => screen.getByRole("tab", { name });

describe("SegmentedTabs", () => {
  it("is a tablist of three tabs with only the selected one in the tab order", () => {
    render(<Harness />);
    expect(screen.getByRole("tablist", { name: "Case sections" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Criteria", "Evidence", "Verdict"]);
    expect(tab("Criteria")).toHaveAttribute("aria-selected", "true");
    expect(tab("Evidence")).toHaveAttribute("aria-selected", "false");
    expect(tab("Criteria")).toHaveAttribute("tabindex", "0");
    expect(tab("Evidence")).toHaveAttribute("tabindex", "-1");
    expect(tab("Verdict")).toHaveAttribute("tabindex", "-1");
    expect(tab("Evidence")).toHaveAttribute("aria-controls", "pane-evidence");
  });

  it("lays the tabs out in equal columns without an inline style", () => {
    render(<Harness />);
    const list = screen.getByRole("tablist");
    expect(list).not.toHaveAttribute("style");
    expect(list).toHaveClass("grid", "grid-flow-col", "auto-cols-fr");
  });

  it("Tab reaches the selected tab and leaves the control in one more press", async () => {
    render(
      <>
        <button type="button">before</button>
        <Harness start="evidence" />
        <button type="button">after</button>
      </>,
    );
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    expect(tab("Evidence")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "after" })).toHaveFocus();
  });

  it("moves with the arrow keys, selects the focused tab and wraps around", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    tab("Criteria").focus();
    await user.keyboard("{ArrowRight}");
    expect(tab("Evidence")).toHaveFocus();
    expect(tab("Evidence")).toHaveAttribute("aria-selected", "true");
    expect(tab("Criteria")).toHaveAttribute("aria-selected", "false");
    await user.keyboard("{ArrowRight}");
    expect(tab("Verdict")).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(tab("Criteria")).toHaveFocus();
    expect(tab("Criteria")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(tab("Verdict")).toHaveFocus();
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowLeft}");
    expect(tab("Evidence")).toHaveFocus();
  });

  it("jumps to the first and last tab with Home and End", async () => {
    render(<Harness start="evidence" />);
    const user = userEvent.setup();
    tab("Evidence").focus();
    await user.keyboard("{End}");
    expect(tab("Verdict")).toHaveFocus();
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(tab("Criteria")).toHaveFocus();
    expect(tab("Criteria")).toHaveAttribute("aria-selected", "true");
  });

  it("selects a tab on click and ignores other keys", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(tab("Verdict"));
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    await user.keyboard("a");
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    expect(tab("Verdict")).toHaveFocus();
  });
});
