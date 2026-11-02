import { describe, expect, it } from "vitest";
import { actionLabel, actorLabel, describeEntry, looksLikeBotToken, looksLikeServiceAccount, usagePercent, usageTone, type AuditEntry, type Usage } from "./agency";

const entry = (over: Partial<AuditEntry>): AuditEntry => ({
  id: 1, actor_kind: "member", actor_id: "11111111-2222-4333-8444-555555555555", action: "settings.change", target: null, detail: {}, created_at: "2026-11-02T10:00:00Z", ...over,
});
const usage = (over: Partial<Usage>): Usage => ({ plan: "free", daily_message_limit: 100, buyer_messages_today: 10, model_calls_today: 0, remaining: 90, over_limit: false, ...over });

describe("bot token and key file checks", () => {
  it("accepts the shape BotFather gives and rejects typos", () => {
    expect(looksLikeBotToken(` 123456789:${"A".repeat(35)} `)).toBe(true);
    expect(looksLikeBotToken("123456789")).toBe(false);
    expect(looksLikeBotToken(`abc:${"A".repeat(35)}`)).toBe(false);
  });
  it("needs both fields of a service-account key", () => {
    expect(looksLikeServiceAccount('{"client_email":"a@b.iam","private_key":"k"}')).toBe(true);
    expect(looksLikeServiceAccount('{"client_email":"a@b.iam"}')).toBe(false);
    expect(looksLikeServiceAccount("not json")).toBe(false);
  });
});

describe("usage", () => {
  it("caps the bar and survives a zero limit", () => {
    expect(usagePercent(50, 100)).toBe(50);
    expect(usagePercent(500, 100)).toBe(100);
    expect(usagePercent(5, 0)).toBe(0);
  });
  it("warns from 80 percent and goes red over the limit", () => {
    expect(usageTone(usage({ buyer_messages_today: 79 }))).toBe("good");
    expect(usageTone(usage({ buyer_messages_today: 80 }))).toBe("warn");
    expect(usageTone(usage({ buyer_messages_today: 101, over_limit: true }))).toBe("bad");
  });
});

describe("audit trail wording", () => {
  it("names known actions and falls back to the raw one", () => {
    expect(actionLabel("channel.telegram_connect")).toBe("Connected a Telegram bot");
    expect(actionLabel("something.new")).toBe("something.new");
  });
  it("shows a role change with both roles", () => {
    expect(describeEntry(entry({ action: "member.role_change", target: "u1", detail: { old_role: "agent", new_role: "admin" } }))).toBe("Changed a member's role u1 (agent to admin)");
    expect(describeEntry(entry({ action: "invitation.create", target: "a@b.co", detail: { role: "viewer" } }))).toBe("Invited someone a@b.co (viewer)");
  });
  it("says who did it without showing a whole id", () => {
    expect(actorLabel(entry({}))).toBe("Member 11111111");
    expect(actorLabel(entry({ actor_kind: "platform", actor_id: null }))).toBe("Platform operator");
  });
});
