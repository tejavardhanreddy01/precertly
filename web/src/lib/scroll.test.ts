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

  it("centers a mark that fits the visible area but not with the margin above and below", () => {
    // Visible area 300; a 270 px mark leaves 15 px each side, less than MARK_MARGIN. Under the old
    // threshold (height + 2 * margin <= visible) this was top-aligned and its bottom was cut off.
    const input = { ...base, containerHeight: 300, mark: { top: 1000, height: 270 } };
    expect(targetScrollTop(input)).toBe(1000 + 135 - 150);
  });

  describe("a mark between visible - 2 * MARK_MARGIN and visible tall", () => {
    const cases = [
      { containerHeight: 300, topInset: 0 },
      { containerHeight: 400, topInset: 49 },
      { containerHeight: 400, topInset: 69 },
    ];
    for (const { containerHeight, topInset } of cases) {
      const visible = containerHeight - topInset;
      for (const height of [visible - 2 * MARK_MARGIN + 1, visible - MARK_MARGIN, visible - 1, visible]) {
        it(`keeps all of a ${height} px mark in view (container ${containerHeight}, inset ${topInset})`, () => {
          const mark = { top: 1000.5, height };
          const scrollTop = targetScrollTop({ ...base, containerHeight, topInset, mark });
          // Where the mark sits in the container after scrolling.
          const top = mark.top - scrollTop;
          expect(top).toBeGreaterThanOrEqual(topInset - 0.5);
          expect(top + height).toBeLessThanOrEqual(containerHeight + 0.5);
        });
      }
    }
  });

  it("fills the visible area exactly when the mark is as tall as it", () => {
    // 300 px visible below a 40 px heading: the mark's top lands on the heading's bottom edge.
    const mark = { top: 1000, height: 300 };
    expect(targetScrollTop({ ...base, containerHeight: 340, topInset: 40, mark })).toBe(1000 - 40);
  });

  it("switches to the top margin as soon as the mark is one pixel taller than the visible area", () => {
    const input = { ...base, containerHeight: 340, topInset: 40 };
    expect(targetScrollTop({ ...input, mark: { top: 1000, height: 300 } })).toBe(960);
    expect(targetScrollTop({ ...input, mark: { top: 1000, height: 301 } })).toBe(1000 - 40 - MARK_MARGIN);
  });

  it("cannot show all of a mark that fits when the content ends first", () => {
    // Scroll range ends at 1600; a mark that would need 1700 is clamped, so this is allowed.
    expect(targetScrollTop({ ...base, mark: { top: 1980, height: 20 } })).toBe(1600);
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
