import { describe, expect, it } from "vitest";
import { backendUrl, buyerHeaders, forwardedHeaders, memberHeaders } from "./proxy";
import { roleFromClaims } from "./role";

describe("roleFromClaims", () => {
  it("makes a signed-in member of an organization a console user", () => {
    expect(roleFromClaims({ sub: "u1", email: "anyone@example.com" }, 1)).toBe("broker");
    expect(roleFromClaims({ sub: "u1" }, 3)).toBe("broker");
  });

  it("treats a signed-in person with no organization as a buyer, whatever their email says", () => {
    expect(roleFromClaims({ sub: "u2", email: "broker@example.com" }, 0)).toBe("buyer");
    expect(roleFromClaims({ sub: "u2" }, 0)).toBe("buyer");
  });

  it("treats a missing, anonymous or subject-less sign-in as signed out, even with memberships on record", () => {
    expect(roleFromClaims(null, 2)).toBe("anonymous");
    expect(roleFromClaims({ email: "x@example.com" }, 2)).toBe("anonymous");
    expect(roleFromClaims({ sub: "u3", is_anonymous: true }, 2)).toBe("anonymous");
  });
});

describe("member proxy", () => {
  it("sends the member's own token and chosen organization, never the admin key or cookies", () => {
    const incoming = new Headers({ "content-type": "application/json", cookie: "sb-x=secret", "x-admin-key": "nope", authorization: "Bearer forged", "x-org-id": "forged" });
    const out = memberHeaders(incoming, "real-token", "11111111-1111-4111-8111-111111111111");
    expect(out.get("authorization")).toBe("Bearer real-token");
    expect(out.get("x-org-id")).toBe("11111111-1111-4111-8111-111111111111");
    expect(out.get("x-admin-key")).toBeNull();
    expect(out.get("cookie")).toBeNull();
    expect(out.get("content-type")).toBe("application/json");
  });

  it("leaves the organization out when none was chosen (the backend picks a lone one)", () => {
    expect(memberHeaders(new Headers({ "x-org-id": "forged" }), "t", null).get("x-org-id")).toBeNull();
  });

  it("lets the operator's password session name an organization too", () => {
    expect(forwardedHeaders(new Headers(), "k", "22222222-2222-4222-8222-222222222222").get("x-org-id")).toBe("22222222-2222-4222-8222-222222222222");
    expect(forwardedHeaders(new Headers(), "k").get("x-org-id")).toBeNull();
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
