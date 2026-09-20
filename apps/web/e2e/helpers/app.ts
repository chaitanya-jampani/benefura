import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page, type Request, type Route } from "@playwright/test";

import { FAKE_PII, makeTestBooklet } from "./booklet";
import { API_ORIGIN, WEB_ORIGIN } from "./env";
import { parseMultipart } from "./multipart";

export interface StoredBox {
  id: string;
  pageIndex: number;
  x: number;
  y: number;
  w: number;
  h: number;
  token: string | null;
  source: string;
  enabled: boolean;
}

export interface StoredPage {
  documentId: string;
  pageIndex: number;
  widthPx: number;
  heightPx: number;
  dpi: number;
  boxes: StoredBox[];
}

async function readStore<T>(page: Page, store: string): Promise<T[]> {
  return page.evaluate(
    (name) =>
      new Promise<T[]>((resolve, reject) => {
        const open = indexedDB.open("benefura");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const database = open.result;
          const tx = database.transaction(name, "readonly");
          const req = tx.objectStore(name).getAll();
          req.onsuccess = () => {
            // Blobs can't cross into the test process; only geometry and metadata are needed.
            resolve((req.result as Array<Record<string, unknown>>).map(({ image, ...rest }) => (void image, rest)) as T[]);
            database.close();
          };
          req.onerror = () => reject(req.error);
        };
      }),
    store,
  );
}

export const readPages = (page: Page) => readStore<StoredPage>(page, "pages");
export const readPlans = (page: Page) => readStore<{ id: string }>(page, "plans");
export const readSettings = (page: Page) => readStore<{ key: string; value: unknown }>(page, "settings");

export interface Upload {
  pages: number[];
  documentId: string;
  region: string;
  pdf: Buffer;
}

export interface ApiMock {
  uploads: Upload[];
  assembleBodies: string[];
}

const CORS = {
  "access-control-allow-origin": WEB_ORIGIN,
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type",
  "access-control-expose-headers": "x-benefura-trace-id, retry-after",
};

async function preflight(route: Route): Promise<boolean> {
  if (route.request().method() !== "OPTIONS") return false;
  await route.fulfill({ status: 204, headers: CORS });
  return true;
}

export function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    headers: { ...CORS, "content-type": "application/json", "x-benefura-trace-id": `trace-${Date.now()}` },
    body: JSON.stringify(body),
  });
}

function benefitRow(page: number, n: number) {
  return {
    row_id: `r-${page}-${n}`,
    page,
    quote: "Massage therapy: 80% up to $80 per visit, maximum $500 per person per benefit year",
    category_name: "Paramedical practitioners",
    category_kind: "paramedical",
    benefit_name: "Massage therapy",
    keywords: "RMT, massage",
    item_codes: null,
    coverage_kind: "percent_capped",
    coverage_percent: 80,
    coverage_cap_amount: 80,
    coverage_amount: null,
    limit_unit: "dollars",
    limit_value: 500,
    limit_period_kind: "benefit_year",
    limit_period_months: null,
    limit_scope: "per_person",
    frequency_count: null,
    frequency_period_kind: null,
    frequency_period_months: null,
    waiting_period_months: null,
    requirements: null,
    pool_name: "Paramedical combined",
    notes: null,
    meta: { rowId: `r-${page}-${n}`, kind: "benefit", page, confidence: 0.55, verifierVerdict: "supported", grounded: true },
  };
}

export function chunkResponse(pages: number[]) {
  return {
    pages,
    rows: { header: [], benefits: [benefitRow(pages[0], 1)], pools: [], cost_shares: [], rules: [], hospital_categories: [] },
    issues: [
      { code: "pii_advisory", severity: "info", message: "Organization mentioned", page: pages[0], category: "Organization", rowId: null },
      { code: "low_confidence", severity: "warning", message: "Low confidence row", page: pages[0], category: null, rowId: `r-${pages[0]}-1` },
    ],
    usage: { inputTokens: 0, outputTokens: 0, cuPages: pages.length, languageRecords: 0, contentSafetyRecords: 0, estimatedCostUsd: 0, byStepMs: {} },
    traceId: `trace-chunk-${pages.join("-")}`,
  };
}

export function assembleResponse() {
  const plan = JSON.parse(readFileSync(path.join(process.cwd(), "../../samples/fixtures/ca-northwind.plan.json"), "utf8"));
  plan.id = "plan-e2e";
  plan.categories[0].benefits[0].source = { page: 2, quote: "Massage therapy: 80% up to $80 per visit", confidence: 0.5, verifierVerdict: "supported" };
  return { plan, issues: [{ code: "duplicate_removed", severity: "info", message: "Removed 1 duplicate row from the page overlap", page: 5, category: null, rowId: null }] };
}

export async function mockBookletApi(
  page: Page,
  analyze: (upload: Upload, call: number) => { status: number; body: unknown } = (u) => ({ status: 200, body: chunkResponse(u.pages) }),
): Promise<ApiMock> {
  const mock: ApiMock = { uploads: [], assembleBodies: [] };
  let calls = 0;
  await page.route(`${API_ORIGIN}/api/plan/analyze-chunk`, async (route) => {
    if (await preflight(route)) return;
    const req = route.request();
    const parts = parseMultipart(req.postDataBuffer() ?? Buffer.alloc(0), (await req.allHeaders())["content-type"] ?? "");
    const field = (name: string) => parts.find((p) => p.name === name)?.data.toString("utf8") ?? "";
    const upload: Upload = {
      pages: field("pages").split(",").map(Number),
      documentId: field("documentId"),
      region: field("region"),
      pdf: parts.find((p) => p.name === "file")!.data,
    };
    mock.uploads.push(upload);
    const { status, body } = analyze(upload, calls++);
    await json(route, status, body);
  });
  await page.route(`${API_ORIGIN}/api/plan/assemble`, async (route) => {
    if (await preflight(route)) return;
    mock.assembleBodies.push(route.request().postData() ?? "");
    await json(route, 200, assembleResponse());
  });
  return mock;
}

export function recordRequests(page: Page): Array<{ url: string; body: Buffer }> {
  const log: Array<{ url: string; body: Buffer }> = [];
  page.on("request", (req: Request) => log.push({ url: req.url(), body: req.postDataBuffer() ?? Buffer.alloc(0) }));
  return log;
}

/** Production CSP, plus style-src for Next's inline styles. */
export const CSP = [
  "default-src 'self'",
  `connect-src 'self' ${API_ORIGIN}`,
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "worker-src 'self' blob:",
  "img-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "form-action 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
].join("; ");

/** The static server sends no CSP, so it's added here to prove pdf.js and tesseract.js need no CDN or eval. */
export async function enforceCsp(page: Page): Promise<string[]> {
  const violations: string[] = [];
  await page.route(`${WEB_ORIGIN}/**`, async (route) => {
    if (route.request().resourceType() !== "document") return route.fallback();
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": CSP } });
  });
  await page.exposeFunction("__recordCspViolation", (v: string) => violations.push(v));
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      (window as unknown as { __recordCspViolation: (v: string) => void }).__recordCspViolation(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
  page.on("console", (msg) => {
    if (/Content Security Policy/i.test(msg.text())) violations.push(msg.text());
  });
  return violations;
}

export async function uploadTestBooklet(page: Page) {
  const booklet = await makeTestBooklet();
  await page.goto("/redact/");
  await page.locator('input[type="file"]').setInputFiles({
    // A real-looking name in the file name must never reach the API.
    name: `${FAKE_PII.name} benefits booklet.pdf`,
    mimeType: "application/pdf",
    buffer: Buffer.from(booklet.bytes),
  });
  await expect(page.getByText("6 pages")).toBeVisible();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  return booklet;
}

export async function fillHideForm(page: Page, values: { name: string; employer?: string; spouse?: string }) {
  await page.getByLabel("Your name", { exact: true }).fill(values.name);
  if (values.employer) await page.getByLabel("Employer", { exact: true }).fill(values.employer);
  if (values.spouse) {
    await page.getByRole("button", { name: "Add a family member" }).click();
    await page.getByLabel("Family member 1", { exact: true }).fill(values.spouse);
  }
  await page.getByRole("button", { name: "Find personal details" }).click();
  await expect(page.getByRole("heading", { name: "On this page" })).toBeVisible();
}

export async function prepareAndAcknowledge(page: Page, pageCount: number) {
  await page.getByRole("button", { name: "Prepare images" }).click();
  await expect(page.getByRole("heading", { name: "Exactly what will be sent" })).toBeVisible();
  await expect(page.locator("[data-page-image]")).toHaveCount(pageCount, { timeout: 120_000 });
  await page.getByRole("button", { name: "These look right" }).click();
  const proceed = page.getByRole("button", { name: "Continue", exact: true });
  await expect(proceed).toBeDisabled();
  const ack = page.getByRole("checkbox", { name: /checked every page image/ });
  await ack.check();
  await expect(proceed).toBeEnabled();
  await ack.uncheck();
  await expect(proceed).toBeDisabled();
  await ack.check();
  return proceed;
}
