// Spike 12: CSP from write-swa-config.mjs against the real static export on SWA (per-route violations + probes).

import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { mkdirSync, writeFileSync } from "node:fs";

const [webUrl, apiUrl] = process.argv.slice(2);
if (!webUrl || !apiUrl) {
  console.error("usage: node s12_csp_check.mjs <web url> <api url>");
  process.exit(2);
}
const require = createRequire(path.join(process.cwd(), "package.json"));
const { chromium } = await import(pathToFileURL(require.resolve("@playwright/test")).href);

const routes = ["/", "/plan/", "/claims/", "/chat/", "/redact/", "/review/", "/privacy/", "/settings/"];
const browser = await chromium.launch();
const context = await browser.newContext();
await context.addInitScript(() => {
  window.__cspViolations = [];
  document.addEventListener("securitypolicyviolation", (e) => {
    window.__cspViolations.push({ directive: e.effectiveDirective, blocked: e.blockedURI, sample: e.sample });
  });
});

const report = { webUrl, apiUrl, routes: {}, probes: {}, headers: {} };
for (const route of routes) {
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy|CSP/i.test(msg.text())) consoleErrors.push(msg.text());
  });
  const response = await page.goto(new URL(route, webUrl).href, { waitUntil: "networkidle" });
  if (route === "/") report.headers = await response.allHeaders();
  const violations = await page.evaluate(() => window.__cspViolations);
  report.routes[route] = { status: response.status(), violations, consoleErrors };
  console.log(`${route} -> ${response.status()}, ${violations.length} violation(s)`);
  await page.close();
}

const page = await context.newPage();
await page.goto(new URL("/", webUrl).href, { waitUntil: "networkidle" });
report.probes = await page.evaluate(async (api) => {
  const out = {};
  try {
    const worker = new Worker(URL.createObjectURL(new Blob(["postMessage('ok')"], { type: "text/javascript" })));
    out.blobWorker = await new Promise((resolve) => {
      worker.onmessage = (e) => resolve(e.data === "ok");
      worker.onerror = () => resolve(false);
      setTimeout(() => resolve(false), 3000);
    });
  } catch {
    out.blobWorker = false;
  }
  try {
    // Smallest valid module: "\0asm" + version 1.
    await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
    out.wasmCompile = true;
  } catch {
    out.wasmCompile = false;
  }
  try {
    out.apiFetch = (await fetch(`${api.replace(/\/$/, "")}/healthz`)).ok;
  } catch {
    out.apiFetch = false;
  }
  try {
    await fetch("https://example.com/");
    out.foreignFetchBlocked = false;
  } catch {
    out.foreignFetchBlocked = true;
  }
  try {
    // eslint-disable-next-line no-eval
    eval("1");
    out.evalBlocked = false;
  } catch {
    out.evalBlocked = true;
  }
  return out;
}, apiUrl);
await browser.close();

const csp = report.headers["content-security-policy"] ?? "";
const checks = {
  "CSP header present": Boolean(csp),
  "no violations on any route": Object.values(report.routes).every((r) => r.violations.length === 0),
  "blob: worker allowed": report.probes.blobWorker === true,
  "wasm compile allowed": report.probes.wasmCompile === true,
  "API fetch allowed": report.probes.apiFetch === true,
  "foreign fetch blocked": report.probes.foreignFetchBlocked === true,
  "eval blocked": report.probes.evalBlocked === true,
};
for (const [name, ok] of Object.entries(checks)) console.log(`[${ok ? "ok  " : "FAIL"}] ${name}`);
const passed = Object.values(checks).every(Boolean);

const resultsDir = path.join(path.dirname(new URL(import.meta.url).pathname), "results");
mkdirSync(resultsDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
writeFileSync(path.join(resultsDir, `s12-${stamp}.json`), JSON.stringify({ spike: "s12", passed, checks, ...report }, null, 2));
console.log(`${passed ? "PASS" : "FAIL"}: s12 -> spikes/results/s12-${stamp}.json`);
process.exit(passed ? 0 : 1);
