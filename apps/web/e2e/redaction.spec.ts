import { expect, test } from "@playwright/test";

import {
  enforceCsp,
  fillHideForm,
  mockBookletApi,
  prepareAndAcknowledge,
  readPages,
  readPlans,
  readSettings,
  recordRequests,
  uploadTestBooklet,
  type StoredBox,
} from "./helpers/app";
import { FAKE_PII, piiNeedles } from "./helpers/booklet";
import { API_ORIGIN } from "./helpers/env";
import { boxPixels, decodeLuma, inset, pageJpegs, pdfTextAndMetadata, samples, type Luma, type PixelRect } from "./helpers/pdf-inspect";

/** Only one centred dark cluster (the label) may sit inside a token box, so original text around it fails. */
function expectLabelOnly(img: Luma, rect: PixelRect, what: string) {
  const inner = inset(rect, 6);
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let y = inner.y; y < inner.y + inner.h; y++) {
    for (let x = inner.x; x < inner.x + inner.w; x++) {
      if (img.data[(y * img.width + x) * 4] < 128) {
        x0 = Math.min(x0, x);
        x1 = Math.max(x1, x);
        y0 = Math.min(y0, y);
        y1 = Math.max(y1, y);
      }
    }
  }
  expect(x1, `${what}: label is drawn`).toBeGreaterThan(x0);
  const cx = (x0 + x1) / 2 - (rect.x + rect.w / 2);
  const cy = (y0 + y1) / 2 - (rect.y + rect.h / 2);
  expect(Math.abs(cx), `${what}: label is centred horizontally`).toBeLessThanOrEqual(Math.max(4, rect.w * 0.04));
  expect(Math.abs(cy), `${what}: label is centred vertically`).toBeLessThanOrEqual(Math.max(4, rect.h * 0.1));
  let minOutside = 255;
  for (let y = inner.y; y < inner.y + inner.h; y++) {
    for (let x = inner.x; x < inner.x + inner.w; x++) {
      if (x >= x0 - 4 && x <= x1 + 4 && y >= y0 - 4 && y <= y1 + 4) continue;
      minOutside = Math.min(minOutside, img.data[(y * img.width + x) * 4]);
    }
  }
  expect(minOutside, `${what}: nothing but the label inside the box`).toBeGreaterThanOrEqual(170);
}

async function findAll(page: import("@playwright/test").Page, value: string, token: string) {
  await page.getByLabel("Hide something else").fill(value);
  const select = page.getByLabel("Label for the value");
  await select.selectOption(token);
  await page.getByRole("button", { name: "Find all" }).click();
  await expect(page.getByRole("complementary", { name: "Boxes" }).getByText(value, { exact: true })).toBeVisible();
}

test("redacts in the browser and sends only image-only, burned-in pages", async ({ page }) => {
  const violations = await enforceCsp(page);
  const requests = recordRequests(page);
  const api = await mockBookletApi(page);

  await uploadTestBooklet(page);
  await fillHideForm(page, { name: FAKE_PII.name, employer: FAKE_PII.employer, spouse: FAKE_PII.spouse });

  // Includes the SIN drawn as two runs.
  const list = page.getByRole("complementary", { name: "Boxes" });
  for (const value of [FAKE_PII.name, FAKE_PII.sin, FAKE_PII.healthCard, FAKE_PII.certificate, FAKE_PII.phone, FAKE_PII.email, FAKE_PII.dob]) {
    await expect(list.getByText(value, { exact: true }).first()).toBeVisible();
  }
  expect(requests.filter((r) => r.url.startsWith(`${API_ORIGIN}/api/`))).toHaveLength(0);

  await page.getByLabel("Page", { exact: true }).selectOption("4");
  await findAll(page, FAKE_PII.memberRefPage5, "new:ID");
  const idToken = await page.getByLabel("Label for the value").inputValue();
  expect(idToken).toMatch(/^\[ID_\d+\]$/);
  await findAll(page, FAKE_PII.memberRefPage6, idToken);
  await findAll(page, FAKE_PII.reference, "");

  await page.getByLabel("Page", { exact: true }).selectOption("5");
  await page.getByRole("button", { name: "Draw a box" }).click();
  const canvas = page.getByRole("group", { name: /Page 6 of 6/ });
  await canvas.evaluate((el) => el.scrollIntoView({ block: "start" }));
  const frame = (await canvas.boundingBox())!;
  await page.mouse.move(frame.x + frame.width * 0.55, frame.y + frame.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(frame.x + frame.width * 0.8, frame.y + frame.height * 0.55, { steps: 5 });
  await page.mouse.up();
  await expect(list.getByText("Box you drew")).toBeVisible();

  const proceed = await prepareAndAcknowledge(page, 6);
  expect(requests.filter((r) => r.url.startsWith(`${API_ORIGIN}/api/`))).toHaveLength(0);
  await proceed.click();

  await page.waitForURL(/\/review\/?\?doc=/);
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  await expect(page.getByText("2 of 2 uploads read")).toBeVisible();

  const uploads = [...api.uploads].sort((a, b) => a.pages[0] - b.pages[0]);
  expect(uploads.map((u) => u.pages)).toEqual([[1, 2, 3, 4, 5], [5, 6]]);
  expect(uploads.every((u) => u.region === "CA" && u.documentId)).toBe(true);

  const stored = await readPages(page);
  const boxesByPage = new Map(stored.map((p) => [p.pageIndex + 1, p.boxes.filter((b) => b.enabled)]));
  const jpegsByPage = new Map<number, Buffer>();

  for (const upload of uploads) {
    expect(upload.pdf.byteLength).toBeLessThanOrEqual(8 * 1024 * 1024);
    const inspected = await pdfTextAndMetadata(new Uint8Array(upload.pdf));
    expect(inspected.numPages).toBe(upload.pages.length);
    expect(inspected.textItems.every((n) => n === 0)).toBe(true);
    for (const key of ["Title", "Author", "Subject", "Keywords", "Creator", "Producer"]) expect(inspected.info[key]).toBeUndefined();
    expect(inspected.metadata).toBeNull();
    const raw = upload.pdf.toString("latin1");
    expect(raw).not.toMatch(/\/Type\s*\/Font|\/ToUnicode|\/Info/);

    const jpegs = await pageJpegs(new Uint8Array(upload.pdf));
    upload.pages.forEach((p, i) => jpegsByPage.set(p, jpegs[i]));
  }

  let checkedBoxes = 0;
  for (const [pageNumber, jpeg] of jpegsByPage) {
    const img = await decodeLuma(jpeg);
    for (const box of boxesByPage.get(pageNumber) ?? []) {
      const rect = boxPixels(box, img.width, img.height);
      if (box.token === null) {
        expect(Math.max(...samples(img, inset(rect, 3)))).toBeLessThanOrEqual(60);
      } else {
        expectLabelOnly(img, rect, `${box.token} on page ${pageNumber}`);
      }
      checkedBoxes++;
    }
  }
  expect(checkedBoxes).toBeGreaterThanOrEqual(12);

  // Same token and geometry over different text must give identical pixels.
  const refBox = (pageNumber: number) => (boxesByPage.get(pageNumber) ?? []).find((b: StoredBox) => b.token === idToken)!;
  const page5 = await decodeLuma(jpegsByPage.get(5)!);
  const page6 = await decodeLuma(jpegsByPage.get(6)!);
  const r5 = boxPixels(refBox(5), page5.width, page5.height);
  const r6 = boxPixels(refBox(6), page6.width, page6.height);
  expect(r6).toEqual(r5);
  const a = samples(page5, inset(r5, 1), 1);
  const b = samples(page6, inset(r6, 1), 1);
  expect(Math.max(...a.map((v, i) => Math.abs(v - b[i])))).toBeLessThanOrEqual(4);

  const needles = piiNeedles();
  for (const req of requests) {
    const haystacks = [req.url, decodeURIComponent(req.url), req.body.toString("latin1"), req.body.toString("utf8")];
    for (const needle of needles) {
      expect(haystacks.some((h) => h.includes(needle)), `${needle} leaked in ${req.url}`).toBe(false);
    }
  }
  expect(api.assembleBodies).toHaveLength(1);
  expect(violations).toEqual([]);

  await expect(page.getByRole("region", { name: "Details the server noticed" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Rows to double-check" }).getByText(/Massage therapy: 80% up to \$80/).first()).toBeVisible();
  const name = page.getByLabel("Benefit name (check this row)").first();
  await name.fill("Massage therapy (RMT)");
  await page.getByRole("button", { name: "Save plan" }).click();
  await page.waitForURL(/\/plan\/?$/);
  const plans = await readPlans(page);
  expect(plans.map((p) => p.id)).toEqual(["plan-e2e"]);
  const settings = await readSettings(page);
  expect(settings.find((s) => s.key === "activePlanId")?.value).toBe("plan-e2e");
  const saved = await page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        const open = indexedDB.open("benefura");
        open.onsuccess = () => {
          const req = open.result.transaction("plans").objectStore("plans").get("plan-e2e");
          req.onsuccess = () => resolve(req.result.plan.categories[0].benefits[0].name);
        };
      }),
  );
  expect(saved).toBe("Massage therapy (RMT)");
});

test("keeps Continue disabled until the acknowledgment is ticked", async ({ page }) => {
  const requests = recordRequests(page);
  await mockBookletApi(page);
  await uploadTestBooklet(page);
  await fillHideForm(page, { name: FAKE_PII.name });
  await prepareAndAcknowledge(page, 6);
  // Changing the boxes after acknowledging means new images, so the acknowledgment is cleared.
  await page.getByRole("button", { name: "Back to images" }).click();
  await page.getByRole("button", { name: "Back to boxes" }).click();
  await page.getByRole("switch").first().click();
  await page.getByRole("button", { name: "Prepare images" }).click();
  await expect(page.locator("[data-page-image]")).toHaveCount(6);
  await page.getByRole("button", { name: "These look right" }).click();
  const proceed = page.getByRole("button", { name: "Continue", exact: true });
  await expect(proceed).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: /checked every page image/ })).not.toBeChecked();
  expect(requests.filter((r) => r.url.startsWith(`${API_ORIGIN}/api/`))).toHaveLength(0);
});
