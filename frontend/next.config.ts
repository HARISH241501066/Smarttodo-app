import type { NextConfig } from "next";

// The browser only ever talks to this app; /api/* is proxied to the FastAPI backend. That keeps the
// session cookie same-origin and needs no CORS. Set BACKEND_URL when building for another host.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const dev = process.env.NODE_ENV !== "production";

// Content-Security-Policy for every page. Next.js writes its own inline scripts into each page (the
// data it hydrates from), and with Cache Components those pages are prerendered, so a per-request
// nonce is not possible; 'unsafe-inline' for scripts is the cost. Everything else is locked down:
// nothing loads from another site, no plugins, no framing, no form posts elsewhere.
const CSP = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${dev ? " ws: http://localhost:*" : ""}`,
  "worker-src 'self'",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // The browser tests build into their own folder so they never clobber a normal build.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/:path*` }];
  },
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      {
        // The service worker must never be served stale, or updates (and push fixes) get stuck.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },
};

export default nextConfig;
