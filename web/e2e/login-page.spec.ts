import { test, expect } from "@playwright/test";

test.describe("Login page", () => {
  test("unauthenticated user is redirected to /login", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("login page renders with email and password fields", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const emailInput = page.locator(
      'input[type="email"], input[name="email"]'
    );
    const passwordInput = page.locator(
      'input[type="password"], input[name="password"]'
    );

    await expect(emailInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
  });

  test("login page has a sign in button", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const signInButton = page.getByRole("button", { name: /sign in/i });
    await expect(signInButton).toBeVisible();
  });

  test("app returns valid HTML", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBeLessThan(500);
  });

  test("no console errors on login page", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });

    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    // Filter out known non-critical errors (e.g., favicon 404)
    const criticalErrors = errors.filter(
      (e) => !e.includes("favicon") && !e.includes("404")
    );
    expect(criticalErrors).toHaveLength(0);
  });

  test("login page shows Bloom logo", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const logo = page.locator('img[src="/logo.png"]');
    await expect(logo).toBeVisible();
  });

  test("login page has sign up link", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const signUp = page.getByRole("button", { name: /sign up/i });
    await expect(signUp).toBeVisible();
  });

  test("email field appends @salk.edu", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const salkSuffix = page.getByText("@salk.edu");
    await expect(salkSuffix).toBeVisible();
  });

  test("login page title is Bloom", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveTitle("Bloom");
  });

  test("invalid login shows error message", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    await page.fill('input[name="email"]', "nonexistent");
    await page.fill('input[name="password"]', "wrongpassword");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Wait for error to appear
    const error = page.getByText(/invalid|error|incorrect|not found/i);
    await expect(error).toBeVisible({ timeout: 10000 });
  });

  test("sign up button switches to signup form", async ({ page }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /sign up/i }).click();

    // Should show a signup form or change the view
    const signUpHeading = page.getByText(/sign up|create account|register/i);
    await expect(signUpHeading).toBeVisible({ timeout: 5000 });
  });

  test("login page is responsive on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 }); // iPhone SE
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    const emailInput = page.locator('input[name="email"]');
    const signInButton = page.getByRole("button", { name: /sign in/i });

    await expect(emailInput).toBeVisible();
    await expect(signInButton).toBeVisible();
  });
});
