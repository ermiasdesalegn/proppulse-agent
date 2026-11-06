import { describe, expect, it } from "vitest";
import { actionLabel, actorLabel, canRetry, describeEntry, jobLabel, jobOutcome, jobTone, keySummary, keyTone, looksLikeBotToken, looksLikeServiceAccount, dollars, usagePercent, usageTone, type AuditEntry, type Job, type KeyStatus, type Usage } from "./agency";

const entry = (over: Partial<AuditEntry>): AuditEntry => ({
  id: 1, actor_kind: "member", actor_id: "11111111-2222-4333-8444-555555555555", action: "settings.change", target: null, detail: {}, created_at: "2026-11-02T10:00:00Z", ...over,
});
const usage = (over: Partial<Usage>): Usage => ({ plan: "free", daily_message_limit: 100, buyer_messages_today: 10, model_calls_today: 0, remaining: 90, over_limit: false, month_spend_usd: 0.5, monthly_budget_usd: 2, over_budget: false, model_calls_month: 10, model_calls_without_tokens: 0, prices_as_of: "2026-11-02", jobs_today: 5, daily_job_limit: 300, ...over });

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
    expect(usageTone(usage({ month_spend_usd: 1.7 }))).toBe("warn");
    expect(usageTone(usage({ month_spend_usd: 2, over_budget: true }))).toBe("bad");
  });
  it("shows money in dollars and small amounts as small, not zero", () => {
    expect(dollars(0)).toBe("$0.00");
    expect(dollars(0.004)).toBe("under $0.01");
    expect(dollars(12.345)).toBe("$12.35");
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

const job = (over: Partial<Job>): Job => ({
  id: 7, org_id: "o", organization: "alpha", kind: "proactive.reverse_rag", status: "queued", attempts: 0, max_attempts: 5,
  run_after: "2026-11-02T10:00:00Z", last_error: null, result: null, created_at: "2026-11-02T10:00:00Z", started_at: null, finished_at: null, ...over,
});

describe("background jobs", () => {
  it("names the known kinds and leaves an unknown one as it is", () => {
    expect(jobLabel("proactive.reverse_rag")).toBe("Price-drop and new-listing alerts");
    expect(jobLabel("future.kind")).toBe("future.kind");
  });
  it("lets only the operator retry, and only a dead or failed job", () => {
    expect(canRetry(job({ status: "dead" }), true)).toBe(true);
    expect(canRetry(job({ status: "failed" }), true)).toBe(true);
    expect(canRetry(job({ status: "dead" }), false)).toBe(false);
    expect(canRetry(job({ status: "done" }), true)).toBe(false);
    expect(canRetry(job({ status: "running" }), true)).toBe(false);
  });
  it("says what happened in one line", () => {
    expect(jobOutcome(job({ status: "done", result: { sent: 2, skipped: 1 } }))).toBe("2 sent, 1 skipped");
    expect(jobOutcome(job({ status: "failed", attempts: 2 }))).toBe("attempt 2 of 5 failed; retrying");
    expect(jobOutcome(job({ status: "dead", attempts: 5 }))).toBe("gave up after 5 attempts");
    expect(jobOutcome(job({ status: "done", kind: "keys.rewrap", result: { status: "rewrapped" } }))).toBe("rewrapped");
    expect(jobLabel("keys.rewrap")).toBe("Master-key re-wrap");
    expect(jobTone("dead")).toBe("bad");
    expect(jobTone("done")).toBe("good");
  });
});

const keys = (over: Partial<KeyStatus>): KeyStatus => ({ configured: true, agencies: 10, current: "NEWkey01", loaded: ["NEWkey01"], by_key: { NEWkey01: 10 }, unknown: 0, done: true, ...over });

describe("master-key status", () => {
  it("says when a rotation is under way and when the old key may go", () => {
    expect(keySummary(keys({ loaded: ["NEWkey01", "OLDkey01"], by_key: { NEWkey01: 4, OLDkey01: 6 }, done: false }))).toBe("Rotation in progress: 6 of 10 agencies are still on an older key. Both keys must stay loaded.");
    expect(keySummary(keys({ loaded: ["NEWkey01", "OLDkey01"] }))).toBe("Every agency is on NEWkey01. The older keys (OLDkey01) can now be removed.");
    expect(keyTone(keys({ done: false }))).toBe("warn");
  });
  it("is red when a key is missing or none is set", () => {
    expect(keyTone(keys({ unknown: 2 }))).toBe("bad");
    expect(keySummary(keys({ configured: false, current: null }))).toContain("No master key");
  });
});
