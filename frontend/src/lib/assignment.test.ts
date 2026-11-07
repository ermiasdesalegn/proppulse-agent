import { describe, expect, it } from "vitest";
import { isMine, memberLabel, splitBody } from "./assignment";

describe("assignment", () => {
  it("labels members", () => {
    expect(memberLabel(null, "u1")).toBe("Unassigned");
    expect(memberLabel("u1", "u1")).toBe("You");
    expect(memberLabel("12345678-aaaa", "u1")).toBe("Member 12345678");
  });
  it("mine is assigned to me or shared with me; without an identity everything shows", () => {
    expect(isMine({ assigned_to: "u1", split: [] }, "u1")).toBe(true);
    expect(isMine({ assigned_to: "u2", split: [{ user_id: "u1", percent: 50 }, { user_id: "u2", percent: 50 }] }, "u1")).toBe(true);
    expect(isMine({ assigned_to: null, split: [] }, "u1")).toBe(false);
    expect(isMine({ assigned_to: null, split: [] }, null)).toBe(true);
  });
  it("checks a split", () => {
    expect(splitBody([{ user_id: "a", percent: "60" }, { user_id: "b", percent: "40" }, { user_id: "", percent: "" }])).toEqual({ shares: [{ user_id: "a", percent: 60 }, { user_id: "b", percent: 40 }] });
    expect(splitBody([])).toEqual({ shares: [] });
    expect(splitBody([{ user_id: "a", percent: "60" }])).toEqual({ error: "The shares must add up to 100." });
    expect(splitBody([{ user_id: "a", percent: "50" }, { user_id: "a", percent: "50" }])).toEqual({ error: "Each member can appear once." });
    expect(splitBody([{ user_id: "", percent: "100" }])).toEqual({ error: "Choose a member and a share between 0 and 100 on every row." });
  });
});
