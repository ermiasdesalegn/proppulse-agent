import { describe, expect, it } from "vitest";
import { checkDocument, fmtSize } from "./documents";

describe("documents", () => {
  it("accepts PDF and images up to 10 MB", () => {
    expect(checkDocument({ size: 1000, type: "application/pdf" })).toBeNull();
    expect(checkDocument({ size: 10 * 1024 * 1024, type: "image/png" })).toBeNull();
  });
  it("refuses other types, empty and oversize files", () => {
    expect(checkDocument({ size: 10, type: "text/html" })).toMatch(/Only PDF/);
    expect(checkDocument({ size: 0, type: "application/pdf" })).toMatch(/empty/);
    expect(checkDocument({ size: 10 * 1024 * 1024 + 1, type: "application/pdf" })).toMatch(/10 MB/);
  });
  it("shows sizes", () => {
    expect(fmtSize(500)).toBe("500 B");
    expect(fmtSize(2048)).toBe("2 KB");
    expect(fmtSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
