import { describe, expect, it } from "vitest";
import {
  estimateRecallInjectionTokens,
  formatRecallInjectionLog,
  resolveExperimentalRecallSettings,
} from "./index.js";

describe("holographic recall POC config", () => {
  it("defaults to the baseline recall path", () => {
    expect(resolveExperimentalRecallSettings({})).toEqual({
      enabled: false,
      shadow: false,
      variant: "baseline",
    });
  });

  it("keeps shadow mode baseline-injected while recording enhanced variant", () => {
    expect(
      resolveExperimentalRecallSettings({
        experimentalHolographicEnhancements: true,
        experimentalRecallShadow: true,
        experimentalRecallVariant: "structural",
      })
    ).toEqual({
      enabled: true,
      shadow: true,
      variant: "structural",
    });
  });

  it("selects active enhanced mode when shadow is disabled", () => {
    expect(
      resolveExperimentalRecallSettings({
        experimentalHolographicEnhancements: true,
        experimentalRecallVariant: "trust",
      })
    ).toEqual({
      enabled: true,
      shadow: false,
      variant: "trust",
    });
  });

  it("logs variant and recall counts for measurement", () => {
    const line = formatRecallInjectionLog({
      bankId: "openclaw",
      variant: "entity_tools",
      raw: 7,
      deduped: 5,
      injected: 3,
      tokens: 42,
    });

    expect(line).toContain("variant: entity_tools");
    expect(line).toContain("raw: 7");
    expect(line).toContain("deduped: 5");
    expect(line).toContain("tokens: 42");
  });

  it("estimates compact injection tokens", () => {
    expect(estimateRecallInjectionTokens("[world] Igor prefers compact answers")).toBe(5);
  });
});
