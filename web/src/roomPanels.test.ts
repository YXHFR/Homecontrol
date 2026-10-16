import { describe, expect, it } from "vitest";
import { lampStateText, luxText, nextLampState } from "./roomPanels";

describe("panelerna under kartan", () => {
  it("beskriver lampans läge", () => {
    expect(lampStateText({ id: "a", on: true, brightness: 100, explanation: "" })).toBe("tänd");
    expect(lampStateText({ id: "a", on: true, brightness: 50, explanation: "" })).toBe("dimrad 50 %");
    expect(lampStateText({ id: "a", on: false, brightness: 100, explanation: "" })).toBe("släckt");
    expect(lampStateText({ id: "a", on: null, brightness: 100, explanation: "" })).toBe("okänt läge");
    expect(lampStateText(undefined)).toBe("okänt läge");
  });

  it("ett klick släcker en lampa som lyser, även dimrad, och tänder annars", () => {
    expect(nextLampState({ id: "a", on: true, brightness: 50, explanation: "" })).toBe(false);
    expect(nextLampState({ id: "a", on: false, brightness: 100, explanation: "" })).toBe(true);
    expect(nextLampState(undefined)).toBe(true);
  });

  it("visar den justerade ljusnivån bara när den skiljer sig", () => {
    expect(luxText(null, null)).toBe("ljusnivån okänd");
    expect(luxText(12.34, 12.34)).toBe("12,3 lux");
    expect(luxText(160, 10)).toBe("160 lux (10 utan lampornas eget ljus)");
  });
});
