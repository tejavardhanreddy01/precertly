import { render, screen } from "@testing-library/react";
import { App } from "./App";

describe("App shell", () => {
  it("says nothing runs when the site is open", () => {
    render(<App />);
    expect(screen.getByText(/Nothing runs when you open this site/)).toBeInTheDocument();
  });
});
