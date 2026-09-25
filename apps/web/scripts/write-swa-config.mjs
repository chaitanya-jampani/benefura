// CSP: a Next.js static export needs 'unsafe-inline' scripts and the raw alias map lives in IndexedDB,
// so connect-src and form-action are what stop exfiltration. 'wasm-unsafe-eval' and worker-src blob:
// are for tesseract.js and pdf.js.

import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEFAULT_API = "http://localhost:8000";

export function apiOrigin(apiBaseUrl = DEFAULT_API) {
  const url = new URL(apiBaseUrl);
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error(`NEXT_PUBLIC_API_BASE_URL must be http(s), got ${apiBaseUrl}`);
  }
  return url.origin;
}

export function buildCsp(apiBaseUrl = DEFAULT_API) {
  const directives = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'"],
    "worker-src": ["'self'", "blob:"],
    "connect-src": ["'self'", apiOrigin(apiBaseUrl)],
    "img-src": ["'self'", "data:", "blob:"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "font-src": ["'self'", "data:"],
    "form-action": ["'self'"],
    "base-uri": ["'self'"],
    "object-src": ["'none'"],
    "frame-ancestors": ["'none'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${[...new Set(values)].join(" ")}`)
    .join("; ");
}

export function buildSwaConfig(apiBaseUrl = DEFAULT_API) {
  return {
    trailingSlash: "always",
    globalHeaders: {
      "Content-Security-Policy": buildCsp(apiBaseUrl),
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    },
    responseOverrides: {
      404: { rewrite: "/404.html", statusCode: 404 },
    },
    mimeTypes: {
      ".wasm": "application/wasm",
      ".mjs": "text/javascript",
      ".traineddata": "application/octet-stream",
      // tesseract fetches eng.traineddata.gz and gunzips it itself: serve bytes, not Content-Encoding.
      ".gz": "application/octet-stream",
    },
  };
}

function main() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const outDir = process.env.SWA_OUT_DIR ?? path.resolve(here, "..", "out");
  if (!existsSync(outDir)) {
    console.error(`write-swa-config: ${outDir} does not exist; run next build first.`);
    process.exit(1);
  }
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API;
  const config = buildSwaConfig(apiBaseUrl);
  const file = path.join(outDir, "staticwebapp.config.json");
  writeFileSync(file, `${JSON.stringify(config, null, 2)}\n`);
  console.log(`write-swa-config: wrote ${file} (connect-src API origin ${apiOrigin(apiBaseUrl)})`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
