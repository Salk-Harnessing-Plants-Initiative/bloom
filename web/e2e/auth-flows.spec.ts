import { test, expect } from "@playwright/test";
import { login } from "./helpers/auth";

const TEST_EMAIL = `e2e-test@salk.edu`;
const TEST_PASSWORD = "e2eTestPassword123!";

// Create test user before all auth tests
test.beforeAll(async ({ request }) => {
  const baseURL = process.env.TEST_BASE_URL || "http://localhost";
  const res = await request.post(`${baseURL}/api/auth/v1/signup`, {
    headers: {
      apikey: process.env.ANON_KEY || "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiYXVkIjoiYXV0aGVudGljYXRlZCIsImlhdCI6MTc2MjgzMTc3OSwiZXhwIjoyMDc4NDA3Nzc5fQ.atklhrmiVo5YtB0VwPY2fmj0nOa3EJzgI1W6xS3DKnw",
    },
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  });
  // 200 = created, 400 = already exists — both fine
  expect([200, 201, 400, 422]).toContain(res.status());
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

  test("signup with non-salk email shows error", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    // Switch to signup
    await page.getByRole("button", { name: /sign up/i }).click();

    await page.fill('input[name="email"]', "test-invalid");
    await page.fill('input[name="password"]', "testpassword123!");

    // Click sign up submit
    const submitButton = page.getByRole("button", { name: /sign up/i });
    await submitButton.click();

    const error = page.getByText(/salk\.edu|not allowed|invalid/i);
    await expect(error).toBeVisible({ timeout: 10000 });
  });
});
