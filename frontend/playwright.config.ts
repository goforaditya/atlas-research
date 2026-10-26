import { defineConfig, devices } from "@playwright/test";

// Starts the real backend (which serves frontend/dist) unless one is already on port 8080.
// Build the UI first: npm run build.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:8080",
    trace: "retain-on-failure",
    ...(process.env.PW_CHROMIUM_PATH ? { launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH } } : {}),
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } }],
  webServer: {
    command: "uv run uvicorn backend.main:app --port 8080",
    cwd: "..",
    url: "http://localhost:8080/api/v1/health",
    reuseExistingServer: true,
    timeout: 60_000,
    env: { GEMINI_API_KEY: "" },
  },
});
