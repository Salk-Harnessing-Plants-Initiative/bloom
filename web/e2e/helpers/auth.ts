import { Page } from "@playwright/test";

/**
 * Log in with email and password via the Supabase auth UI.
 * Stores the session so subsequent navigations stay authenticated.
 */
export async function login(
  page: Page,
  email: string = process.env.TEST_USER_EMAIL || "test@salk.edu",
  password: string = process.env.TEST_USER_PASSWORD || "testpassword123!"
) {
  await page.goto("/login");
  await page.waitForLoadState("networkidle");

  // Fill login form (email field auto-appends @salk.edu)
  await page.fill('input[name="email"]', email.replace("@salk.edu", ""));
  await page.fill('input[name="password"]', password);
  await page.getByRole("button", { name: /sign in/i }).click();

  // Wait for redirect away from login
  await page.waitForURL((url) => !url.pathname.includes("/login"), {
    timeout: 10000,
  });
}
