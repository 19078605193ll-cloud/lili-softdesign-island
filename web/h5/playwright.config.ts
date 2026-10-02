import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 90000,
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: process.env.H5_BASE_URL || "http://127.0.0.1:8000",
    viewport: { width: 390, height: 844 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
  reporter: "list",
  webServer: {
    command: "npm run dev -- --port 5179 --strictPort",
    url: "http://127.0.0.1:5179/h5/e2e/fixtures/ai-text.html",
    reuseExistingServer: false,
  },
});
