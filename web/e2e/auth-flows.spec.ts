import { test, expect, request as playwrightRequest } from "@playwright/test";
import { login } from "./helpers/auth";

const TEST_EMAIL = `e2e-test@salk.edu`;
const TEST_PASSWORD = "e2eTestPassword123!";

const ANON_KEY = process.env.ANON_KEY;
if (!ANON_KEY) throw new Error("ANON_KEY env var required for e2e tests");

// Create test user before all auth tests
// Note: beforeAll only supports worker-scoped fixtures, not the test-scoped
// `request` fixture, so we create the API context manually.
test.beforeAll(async () => {
  const baseURL = process.env.TEST_BASE_URL || "http://localhost";
  const context = await playwrightRequest.newContext({ baseURL });
  const res = await context.post(`${baseURL}/api/auth/v1/signup`, {
    headers: { apikey: ANON_KEY },
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  });
  // 200 = created, 400 = already exists — both fine
  expect([200, 201, 400, 422]).toContain(res.status());
  await context.dispose();
});

test.describe("Auth flows", () => {
  test("successful login lands on app page", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD);
    // Should be on the app, not login
    expect(page.url()).not.toContain("/login");
  });

  test("logged in user does not see login link", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD);
    const loginLink = page.getByRole("link", { name: /login/i });
    await expect(loginLink).not.toBeVisible();
  });

  test("logout redirects to login page", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD);

    // Find and click logout
    const logoutButton = page.getByRole("button", { name: /logout|sign out/i });
    if (await logoutButton.isVisible()) {
      await logoutButton.click();
    } else {
      // Try link instead of button
      const logoutLink = page.getByRole("link", { name: /logout|sign out/i });
      await logoutLink.click();
    }

    await page.waitForURL(/\/login/, { timeout: 10000 });
    expect(page.url()).toContain("/login");
  });

  test("session persists across navigation", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD);
    // Navigate to a different page
    await page.goto("/app/expression");
    await page.waitForLoadState("networkidle");

    // Should still be authenticated, not redirected to login
    expect(page.url()).not.toContain("/login");
  });

  test("protected route /app/expression redirects when unauthenticated", async ({ page }) => {
    await page.goto("/app/expression");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("protected route /app/chat redirects when unauthenticated", async ({ page }) => {
    await page.goto("/app/chat");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("protected route /app/phenotypes redirects when unauthenticated", async ({ page }) => {
    await page.goto("/app/phenotypes");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("after login can access multiple protected routes", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD);

    for (const route of ["/app/expression", "/app/phenotypes", "/app/genes"]) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      expect(page.url()).not.toContain("/login");
    }
  });

});
