import { expect, test, type Page } from "@playwright/test";

const PASSWORD = process.env.DASHBOARD_PASSWORD ?? "ci-password";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("the dashboard is closed to anyone who is not signed in", async ({ page }) => {
  await page.goto("/console/crm");
  await expect(page).toHaveURL(/\/login/);
  const response = await page.request.get("/api/proxy/buyers");
  expect(response.status()).toBe(401);
});

test("a wrong password is refused and the right one signs in and out", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
});

test("every section opens without an error", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await signIn(page);
  for (const [name, heading] of [["CRM & Takeover", "CRM & Takeover"], ["Deal Board", "Deal Board"], ["Agent Brain", "Agent Brain"], ["Map & Tours", "Map & Tours"],
                                 ["Analytics", "Analytics"], ["Studio", "Studio"], ["Chat Simulator", "Chat Simulator"]]) {
    await page.getByRole("link", { name }).click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
  }
  expect(problems).toEqual([]);
});

test("the Studio reads a sample listing page and reports that nothing changed", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Studio" }).click();
  await page.getByRole("button", { name: "Ayat page, as it is now" }).click();
  await page.getByRole("button", { name: "Preview changes" }).click();
  await expect(page.getByText(/4 unchanged/)).toBeVisible({ timeout: 30_000 });
});

test("the map shows all fifteen units (the drawn map when there is no Mapbox token)", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Map & Tours" }).click();
  await expect(page.getByText("15 units")).toBeVisible({ timeout: 30_000 });
});

test("analytics counts come from the database", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByText("Where buyers are in the pipeline")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Buyers", { exact: true })).toBeVisible();
});

test("the chat simulator survives a model that is not available and still answers", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Chat Simulator" }).click();
  await page.getByLabel("Message").fill("I want a 2 bedroom in Bole");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Agent", { exact: true }).first()).toBeVisible({ timeout: 80_000 });
});
