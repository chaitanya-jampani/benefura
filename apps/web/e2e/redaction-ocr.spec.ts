import { createCanvas } from "@napi-rs/canvas";
import { expect, test } from "@playwright/test";
import { PDFDocument } from "pdf-lib";

import { enforceCsp, fillHideForm, mockBookletApi, prepareAndAcknowledge } from "./helpers/app";
import { FAKE_PII } from "./helpers/booklet";

/** Text rendered into a 200 DPI image with no text layer, so OCR has to run. */
async function scannedBooklet(): Promise<Uint8Array> {
  const canvas = createCanvas(1700, 2200);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fbfaf7";
  ctx.fillRect(0, 0, 1700, 2200);
  ctx.fillStyle = "#111111";
  ctx.font = "bold 56px sans-serif";
  ctx.fillText("Group benefits enrolment", 160, 260);
  ctx.font = "44px sans-serif";
  const lines = [
    `Plan member: ${FAKE_PII.name}`,
    `Social insurance number: ${FAKE_PII.sin}`,
    `Phone: ${FAKE_PII.phone}`,
    "Massage therapy: 80% to a maximum of $500 per year",
  ];
  lines.forEach((line, i) => ctx.fillText(line, 160, 420 + i * 90));
  const doc = await PDFDocument.create();
  const image = await doc.embedJpg(await canvas.encode("jpeg", 90));
  const page = doc.addPage([612, 792]);
  page.drawImage(image, { x: 0, y: 0, width: 612, height: 792 });
  return doc.save();
}

test("reads a scanned page with local OCR under the production CSP", async ({ page }) => {
  const violations = await enforceCsp(page);
  await mockBookletApi(page);
  await page.goto("/redact/");
  // The policy is really in force: a CDN fetch is refused and reported.
  const cdn = await page.evaluate(() => fetch("https://cdn.jsdelivr.net/npm/tesseract.js/package.json").then(() => "allowed", () => "blocked"));
  expect(cdn).toBe("blocked");
  await expect.poll(() => violations.some((v) => v.includes("connect-src"))).toBe(true);
  violations.length = 0;
  await page.locator('input[type="file"]').setInputFiles({ name: "scan.pdf", mimeType: "application/pdf", buffer: Buffer.from(await scannedBooklet()) });
  await expect(page.getByText("1 page", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await fillHideForm(page, { name: FAKE_PII.name });

  await expect(page.getByText("This page is a scan.")).toBeVisible();
  const list = page.getByRole("complementary", { name: "Boxes" });
  await expect(list.getByText(FAKE_PII.sin, { exact: true })).toBeVisible();
  await expect(list.getByText("Social insurance number", { exact: false }).first()).toBeVisible();
  await expect(list.getByText(FAKE_PII.name, { exact: true })).toBeVisible();

  const proceed = await prepareAndAcknowledge(page, 1);
  await proceed.click();
  await page.waitForURL(/\/review\/?\?doc=/);
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  expect(violations).toEqual([]);
});
