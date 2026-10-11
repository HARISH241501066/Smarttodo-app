// Exports the dashboard (static files in out/) for the browser tests. The test backend serves them.
import { execSync } from "node:child_process";

execSync("npx next build", { stdio: "inherit" });
