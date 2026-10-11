import { defineConfig } from "@playwright/test";

// Browser tests run the real production build of the dashboard against a throwaway backend
// (backend/scripts/e2e_server.py): temporary database, seeded data, fake Gmail and push.
//   npm run e2e        builds into .next-e2e, starts both servers, runs the tests
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    serviceWorkers: "allow",
    // The full Chromium build in new-headless mode: the stripped-down headless shell always reports
    // Notification.permission as "denied", which would make push impossible to test.
    channel: "chromium",
    permissions: ["notifications"],
  },
  webServer: [
    {
      command: "node e2e/run-backend.mjs",
      url: "http://127.0.0.1:8001/health",
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: "npx next start -p 3100",
      url: "http://localhost:3100/login",
      timeout: 120_000,
      reuseExistingServer: false,
      env: { NEXT_DIST_DIR: ".next-e2e" },
    },
  ],
});
