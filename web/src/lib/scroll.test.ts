import { MARK_MARGIN, targetScrollTop } from "./scroll";

const base = { containerHeight: 400, contentHeight: 2000, scrollTop: 0 };

describe("targetScrollTop", () => {
  it("centers a mark that fits", () => {
    // Mark spans 1000 to 1040, its center 1020; the container center is 200 down.
    expect(targetScrollTop({ ...base, mark: { top: 1000, height: 40 } })).toBe(820);
  });

  it("does not depend on where the container is scrolled now", () => {
    const mark = { top: 1000, height: 40 };
    expect(targetScrollTop({ ...base, scrollTop: 0, mark })).toBe(targetScrollTop({ ...base, scrollTop: 1500, mark }));
  });

  it("aligns the top of a mark taller than the panel, with a margin", () => {
    expect(targetScrollTop({ ...base, containerHeight: 300, mark: { top: 1000, height: 500 } })).toBe(1000 - MARK_MARGIN);
    expect(MARK_MARGIN).toBeGreaterThanOrEqual(24);
  });

  it("aligns the top when the mark fits but leaves less than the margin above and below", () => {
    expect(targetScrollTop({ ...base, containerHeight: 300, mark: { top: 1000, height: 270 } })).toBe(1000 - MARK_MARGIN);
  });

  it("clamps a mark near the start of the content to zero", () => {
    expect(targetScrollTop({ ...base, mark: { top: 10, height: 40 } })).toBe(0);
    expect(targetScrollTop({ ...base, mark: { top: 10, height: 900 } })).toBe(0);
  });

  it("clamps a mark near the end of the content to the last scroll position", () => {
    expect(targetScrollTop({ ...base, mark: { top: 1950, height: 40 } })).toBe(1600);
    expect(targetScrollTop({ ...base, mark: { top: 1650, height: 500 } })).toBe(1600);
  });

  it("keeps the current position when there is no mark", () => {
    expect(targetScrollTop({ ...base, scrollTop: 300, mark: null })).toBe(300);
  });

  it("clamps the kept position into the scrollable range", () => {
    expect(targetScrollTop({ ...base, scrollTop: 5000, mark: null })).toBe(1600);
    expect(targetScrollTop({ ...base, scrollTop: -20, mark: null })).toBe(0);
  });

  it("returns zero when the content is shorter than the container", () => {
    expect(targetScrollTop({ containerHeight: 400, contentHeight: 250, scrollTop: 0, mark: { top: 200, height: 20 } })).toBe(0);
  });

  it("centers in the area below a sticky heading", () => {
    // Visible area is 370 tall from 30 down: its center is 30 + 185 = 215 from the top.
    expect(targetScrollTop({ ...base, topInset: 30, mark: { top: 1000, height: 40 } })).toBe(1020 - 215);
  });

  it("keeps a tall mark's top clear of a sticky heading", () => {
    expect(targetScrollTop({ ...base, topInset: 30, mark: { top: 1000, height: 900 } })).toBe(1000 - 30 - MARK_MARGIN);
  });

  it("rounds to a whole pixel", () => {
    expect(Number.isInteger(targetScrollTop({ ...base, mark: { top: 1000.4, height: 41 } }))).toBe(true);
  });
});
