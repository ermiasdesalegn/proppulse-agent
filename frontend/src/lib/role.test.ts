import { describe, expect, it } from "vitest";
import { buyerHeaders, backendUrl } from "./proxy";
import { mightBeBroker, roleFromClaims } from "./role";

const broker = "Broker@Example.com";

describe("roleFromClaims", () => {
  it("makes the confirmed broker email the broker, whatever the letter case", () => {
    expect(roleFromClaims({ sub: "u1", email: "broker@example.com" }, broker, true)).toBe("broker");
  });

  it("does not trust the broker's address until the auth server says it is confirmed", () => {
    expect(roleFromClaims({ sub: "u1", email: "broker@example.com" }, broker, false)).toBe("buyer");
  });

  it("treats every other account as a buyer", () => {
    expect(roleFromClaims({ sub: "u2", email: "abel@example.com" }, broker, true)).toBe("buyer");
    expect(roleFromClaims({ sub: "u2" }, broker, true)).toBe("buyer");
  });

  it("never makes anyone the broker when no broker email is set", () => {
    expect(roleFromClaims({ sub: "u1", email: "" }, "", true)).toBe("buyer");
    expect(roleFromClaims({ sub: "u1", email: "a@b.c" }, "  ", true)).toBe("buyer");
  });

  it("treats a missing, anonymous or subject-less sign-in as signed out", () => {
    expect(roleFromClaims(null, broker, true)).toBe("anonymous");
    expect(roleFromClaims({ email: "broker@example.com" }, broker, true)).toBe("anonymous");
    expect(roleFromClaims({ sub: "u3", is_anonymous: true }, broker, true)).toBe("anonymous");
  });

  it("only asks the auth server about the account that could become the broker", () => {
    expect(mightBeBroker({ sub: "u1", email: "BROKER@example.com" }, broker)).toBe(true);
    expect(mightBeBroker({ sub: "u2", email: "abel@example.com" }, broker)).toBe(false);
    expect(mightBeBroker({ sub: "u1", email: "x@y.z" }, "")).toBe(false);
  });
});

describe("buyer proxy", () => {
  it("sends the buyer's token and never the admin key or cookies", () => {
    const incoming = new Headers({ "content-type": "application/json", accept: "*/*", cookie: "sb-x=secret", "x-admin-key": "nope", authorization: "Bearer forged" });
    const out = buyerHeaders(incoming, "real-token");
    expect(out.get("authorization")).toBe("Bearer real-token");
    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("cookie")).toBeNull();
    expect(out.get("x-admin-key")).toBeNull();
  });

  it("sends no authorization at all for the public showcase", () => {
    expect(buyerHeaders(new Headers({ authorization: "Bearer forged" }), null).get("authorization")).toBeNull();
  });

  it("builds /api/me and /api/public addresses and refuses path tricks", () => {
    expect(backendUrl(["me", "listings"], "?sort=price_asc", "http://b:8000/")).toBe("http://b:8000/api/me/listings?sort=price_asc");
    expect(backendUrl(["public", "covers", "abc"], "", "http://b:8000")).toBe("http://b:8000/api/public/covers/abc");
    expect(backendUrl(["me", "..", "buyers"], "", "http://b:8000")).toBeNull();
    expect(backendUrl(["me", "%2e%2e"], "", "http://b:8000")).toBeNull();
  });
});
