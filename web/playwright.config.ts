import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  use: {
    baseURL: process.env.TEST_BASE_URL || "http://localhost",
    screenshot: "only-on-failure",
    video: process.env.CI ? "on-first-retry" : "off",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  reporter: [
    ["list"],
    ["html", { open: "never" }],
  ],
});
