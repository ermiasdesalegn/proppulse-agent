import { describe, expect, it, vi } from "vitest";
import { agencyHref, agencyRequestHeaders, cleanAgency, colorChannels, routeAgency, safeColor, safeLogo } from "./agencysite";
import { buyerHeaders } from "./proxy";
import { loadAgency, loadDirectory, loadFeatured } from "./portal";

describe("agency addresses", () => {
  it("accepts a well-formed slug and nothing that is the website's own route or malformed", () => {
    expect(cleanAgency("bole-homes")).toBe("bole-homes");
    expect(cleanAgency("  Bole-Homes ")).toBe("bole-homes");
    for (const bad of ["portal", "console", "api", "login", "join", "start", "agencies", "a", "-ab", "ab-", "has space", "up_per", "../etc", "a".repeat(41), "", null, undefined]) {
      expect(cleanAgency(bad as string | null | undefined)).toBeNull();
    }
  });

  it("reads the agency from the first path segment and serves the rest", () => {
    expect(routeAgency("/bole-homes", null)).toEqual({ slug: "bole-homes", path: "/", remember: true, forget: false });
    expect(routeAgency("/bole-homes/portal/chat", null)).toEqual({ slug: "bole-homes", path: "/portal/chat", remember: true, forget: false });
    expect(routeAgency("/bole-homes/login", "other-agency").slug).toBe("bole-homes");
  });

  it("leaves the website's own routes alone and falls back to the remembered agency", () => {
    expect(routeAgency("/portal/chat", "bole-homes")).toEqual({ slug: "bole-homes", path: "/portal/chat", remember: false, forget: false });
    expect(routeAgency("/portal/chat", null).slug).toBeNull();
    expect(routeAgency("/api/me/listings", "bole-homes").slug).toBe("bole-homes");
    expect(routeAgency("/portal", "not a slug!").slug).toBeNull();
    expect(routeAgency("/console", "../x").slug).toBeNull();
  });

  it("forgets the remembered agency when the bare home page is opened", () => {
    expect(routeAgency("/", "bole-homes")).toEqual({ slug: null, path: "/", remember: false, forget: true });
    expect(routeAgency("/", null).forget).toBe(false);
  });

  it("keeps links with the agency", () => {
    expect(agencyHref("bole-homes", "/")).toBe("/bole-homes");
    expect(agencyHref("bole-homes", "/#homes")).toBe("/bole-homes#homes");
    expect(agencyHref("bole-homes", "/login")).toBe("/login");
    expect(agencyHref(null, "/")).toBe("/");
    expect(agencyHref("portal", "/")).toBe("/");
  });

  it("uses only plain colours and https logos", () => {
    expect(safeColor("#AA3300")).toBe("#AA3300");
    for (const bad of ["red", "#12345", "#12345g", "url(x)", "#aa3300; background:red", null]) expect(safeColor(bad as string | null)).toBeNull();
    expect(colorChannels("#aa3300")).toBe("170 51 0");
    expect(colorChannels("red")).toBeNull();
    expect(safeLogo("https://cdn.example.com/a.png")).toBe("https://cdn.example.com/a.png");
    for (const bad of ["http://x.com/a.png", "javascript:alert(1)", 'https://x.com/"onerror=1', "https://x.com/a b", null]) expect(safeLogo(bad as string | null)).toBeNull();
  });
});

describe("the agency reaches every call to the backend, and only a valid one", () => {
  it("is added to the headers the website's API routes send", () => {
    const incoming = new Headers({ "content-type": "application/json", "x-agency": "bole-homes" });
    expect(buyerHeaders(incoming, "tok").get("x-agency")).toBe("bole-homes");
    expect(buyerHeaders(new Headers({ "x-agency": "../etc" }), null).has("x-agency")).toBe(false);
    expect(buyerHeaders(new Headers({ "x-agency": "portal" }), null).has("x-agency")).toBe(false);
    expect(buyerHeaders(new Headers(), null).has("x-agency")).toBe(false);
    expect(agencyRequestHeaders("bole-homes")).toEqual({ "x-agency": "bole-homes" });
    expect(agencyRequestHeaders("nope nope")).toEqual({});
  });

  it("is sent by the page loaders, and an invalid slug is never sent", async () => {
    const seen: { url: string; headers: Record<string, string> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url, headers: (init?.headers ?? {}) as Record<string, string> });
      const body = url.includes("/agencies") ? { agencies: [] } : url.includes("/agency") ? { slug: "bole-homes", name: "Bole Homes", brand_color: null, logo_url: null } : { units: [], stats: { units: 0, developers: 0, areas: 0 }, sub_cities: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    await loadFeatured("bole-homes");
    await loadAgency("bole-homes");
    await loadFeatured("../../etc/passwd");
    await loadAgency("portal");
    await loadDirectory();
    vi.unstubAllGlobals();
    expect(seen[0].headers["x-agency"]).toBe("bole-homes");
    expect(seen[1].headers["x-agency"]).toBe("bole-homes");
    expect(seen[2].headers["x-agency"]).toBeUndefined();
    expect(seen[3].headers["x-agency"]).toBeUndefined();
    expect(seen[4].headers["x-agency"]).toBeUndefined();
    expect(seen.map((s) => s.url).some((u) => u.includes("passwd"))).toBe(false);
  });

  it("tells the page when the agency is not found", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    expect(await loadAgency("no-such-agency")).toBe("not-found");
    expect(await loadFeatured("no-such-agency")).toBeNull();
    vi.unstubAllGlobals();
  });
});
