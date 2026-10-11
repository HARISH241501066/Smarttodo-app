import type { NextConfig } from "next";

// Production: the app is exported as plain static files (`next build` writes them to `out/`) and the FastAPI backend serves them from the same origin; the browser calls the
// API under /api there, so nothing here proxies anything and the security headers come from FastAPI.
// Development (`next dev`): /api/* is proxied to the backend so the session cookie stays same-origin.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const exporting = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  ...(exporting ? { output: "export" as const } : {}),
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  ...(exporting
    ? {}
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${BACKEND_URL}/:path*` }];
        },
      }),
};

export default nextConfig;
