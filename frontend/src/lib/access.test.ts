import { describe, expect, it } from "vitest";
import { decide, emailLinkRedirect, homeFor, safeNext } from "./access";

describe("access rules", () => {
  it("lets anyone open the public pages and sign-in endpoints", () => {
    for (const path of ["/", "/login", "/signup", "/auth/callback", "/api/auth/login", "/api/public/featured"]) {
      expect(decide(path, "anonymous")).toEqual({ action: "next" });
    }
  });

  it("sends a signed-out visitor from the portal to the sign-in page, remembering where they were going", () => {
    expect(decide("/portal/listings", "anonymous", "?beds=3")).toEqual({ action: "redirect", to: "/login?next=%2Fportal%2Flistings%3Fbeds%3D3" });
    expect(decide("/api/me/saved", "anonymous")).toEqual({ action: "deny", status: 401 });
  });

  it("keeps buyers out of the console and the admin proxy", () => {
    expect(decide("/console", "buyer")).toEqual({ action: "redirect", to: "/portal" });
    expect(decide("/console/crm", "buyer")).toEqual({ action: "redirect", to: "/portal" });
    expect(decide("/api/proxy/buyers", "buyer")).toEqual({ action: "deny", status: 403 });
    expect(decide("/portal", "buyer")).toEqual({ action: "next" });
    expect(decide("/api/me/chat", "buyer")).toEqual({ action: "next" });
  });

  it("closes the console to signed-out visitors and opens it to the broker", () => {
    expect(decide("/console/map", "anonymous")).toEqual({ action: "redirect", to: "/login" });
    expect(decide("/api/proxy/buyers", "anonymous")).toEqual({ action: "deny", status: 401 });
    expect(decide("/console/map", "broker")).toEqual({ action: "next" });
    expect(decide("/api/proxy/buyers", "broker")).toEqual({ action: "next" });
    expect(decide("/portal/listings", "broker")).toEqual({ action: "next" });
  });

  it("lets any signed-in person start an agency, since they have no organization yet", () => {
    expect(decide("/start", "buyer")).toEqual({ action: "next" });
    expect(decide("/api/orgs/mine", "buyer")).toEqual({ action: "next" });
    expect(decide("/api/orgs", "broker")).toEqual({ action: "next" });
    expect(decide("/start", "anonymous")).toEqual({ action: "redirect", to: "/login?next=%2Fstart" });
    expect(decide("/api/orgs/mine", "anonymous")).toEqual({ action: "deny", status: 401 });
    expect(decide("/join", "buyer")).toEqual({ action: "next" });
    expect(decide("/api/invitations/accept", "buyer")).toEqual({ action: "next" });
    expect(decide("/join", "anonymous", "?token=abc")).toEqual({ action: "redirect", to: "/login?next=%2Fjoin%3Ftoken%3Dabc" });
    expect(decide("/startup", "buyer")).toEqual({ action: "redirect", to: "/portal" });
  });

  it("treats a path nobody planned for as broker-only", () => {
    expect(decide("/anything-new", "buyer")).toEqual({ action: "redirect", to: "/portal" });
    expect(decide("/api/anything-new", "anonymous")).toEqual({ action: "deny", status: 401 });
    expect(decide("/loginx", "anonymous")).toEqual({ action: "redirect", to: "/login" });
    expect(decide("/portalx", "anonymous")).toEqual({ action: "redirect", to: "/login" });
  });

  it("sends signed-in people past the sign-in pages to their own home", () => {
    expect(decide("/login", "broker")).toEqual({ action: "redirect", to: "/console" });
    expect(decide("/login", "buyer")).toEqual({ action: "redirect", to: "/portal" });
    expect(decide("/signup", "buyer", "?next=%2Fportal%2Fsaved")).toEqual({ action: "redirect", to: "/portal/saved" });
    expect(decide("/login", "buyer", "?next=https%3A%2F%2Fevil.example")).toEqual({ action: "redirect", to: "/portal" });
  });

  it("only follows same-site next paths", () => {
    expect(safeNext("/portal/saved")).toBe("/portal/saved");
    expect(safeNext("//evil.example")).toBeNull();
    expect(safeNext("https://evil.example")).toBeNull();
    expect(safeNext("/\\evil.example")).toBeNull();
    expect(safeNext(null)).toBeNull();
    expect(homeFor("anonymous")).toBe("/login");
  });
});

describe("email links that Supabase sent to the home page", () => {
  const at = (query: string, path = "/") => emailLinkRedirect(path, new URLSearchParams(query));

  it("passes a sign-in code on to the callback", () => {
    expect(at("code=bb79709c-a42a-4d79-aea6-653d60e362a7")).toBe("/auth/callback?code=bb79709c-a42a-4d79-aea6-653d60e362a7");
  });

  it("sends a used or expired link to the sign-in page with an explanation", () => {
    expect(at("error=access_denied&error_code=otp_expired")).toBe("/login?notice=link");
  });

  it("leaves every other page and odd codes alone", () => {
    expect(at("")).toBeNull();
    expect(at("code=abc", "/portal")).toBeNull();
    expect(at("code=<script>")).toBeNull();
  });
});
