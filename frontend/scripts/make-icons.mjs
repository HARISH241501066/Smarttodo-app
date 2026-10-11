// Renders the PWA icons from an SVG with headless Chromium:  node scripts/make-icons.mjs
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const out = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "icons");
await mkdir(out, { recursive: true });

const BLUE = "#2563eb";

// `scale` shrinks the check mark (maskable icons need it inside the central safe zone).
const svg = (size, { rounded, scale }) => `
<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="${rounded ? 112 : 0}" fill="${BLUE}"/>
  <g transform="translate(256 256) scale(${scale}) translate(-256 -256)">
    <path d="M136 268 L222 354 L376 170" fill="none" stroke="#ffffff" stroke-width="48"
          stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>`;

const icons = [
  { file: "icon-192.png", size: 192, rounded: true, scale: 1 },
  { file: "icon-512.png", size: 512, rounded: true, scale: 1 },
  { file: "maskable-512.png", size: 512, rounded: false, scale: 0.72 },
  { file: "apple-touch-icon.png", size: 180, rounded: false, scale: 0.9 },
];

const browser = await chromium.launch();
const page = await browser.newPage();
for (const { file, size, rounded, scale } of icons) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}svg{display:block}</style>${svg(size, { rounded, scale })}`,
  );
  await page.screenshot({ path: path.join(out, file), omitBackground: true });
  console.log("wrote", file);
}
await browser.close();
