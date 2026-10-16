import { describe, expect, it } from "vitest";
import { lampStateText, luxText, shouldTurnOn } from "./roomPanels";

describe("panelerna under kartan", () => {
  it("beskriver lampans läge", () => {
    expect(lampStateText({ id: "a", on: true, brightness: 100, explanation: "", responding: true })).toBe("tänd");
    expect(lampStateText({ id: "a", on: true, brightness: 50, explanation: "", responding: true })).toBe("dimrad 50 %");
    expect(lampStateText({ id: "a", on: false, brightness: 100, explanation: "", responding: true })).toBe("släckt");
    expect(lampStateText({ id: "a", on: null, brightness: 100, explanation: "", responding: true })).toBe("okänt läge");
    expect(lampStateText(undefined)).toBe("okänt läge");
    expect(lampStateText({ id: "a", on: null, brightness: 100, explanation: "", responding: false })).toBe(
      "svarar inte",
    );
  });

  it("ett klick släcker en lampa som lyser, även dimrad, och tänder annars", () => {
    expect(shouldTurnOn({ id: "a", on: true, brightness: 50, explanation: "", responding: true })).toBe(false);
    expect(shouldTurnOn({ id: "a", on: false, brightness: 100, explanation: "", responding: true })).toBe(true);
    expect(shouldTurnOn(undefined)).toBe(true);
  });

  it("visar den justerade ljusnivån bara när den skiljer sig", () => {
    expect(luxText(null, null)).toBe("ljusnivån okänd");
    expect(luxText(12.34, 12.34)).toBe("12,3 lux");
    expect(luxText(160, 10)).toBe("160 lux (10 utan lampornas eget ljus)");
  });
});
