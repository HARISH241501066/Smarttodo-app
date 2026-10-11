// Builds the dashboard for the browser tests: its own output folder, proxying /api to the test backend.
import { execSync } from "node:child_process";

execSync("npx next build", {
  stdio: "inherit",
  env: { ...process.env, NEXT_DIST_DIR: ".next-e2e", BACKEND_URL: "http://127.0.0.1:8001" },
});
