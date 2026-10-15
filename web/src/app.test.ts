import { describe, expect, it } from "vitest";
import { mountApp } from "./app";

describe("mountApp", () => {
  it("visar en tom sida", () => {
    const root = document.createElement("main");
    root.textContent = "gammalt innehåll";

    mountApp(root);

    expect(root.childElementCount).toBe(0);
    expect(root.textContent).toBe("");
    expect(root.dataset.ready).toBe("true");
  });
});
