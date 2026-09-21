import { expect, test, type Page } from "@playwright/test";

import { chunkResponse, fillHideForm, mockBookletApi, prepareAndAcknowledge, readPages, uploadTestBooklet, type ApiMock } from "./helpers/app";
import { FAKE_PII } from "./helpers/booklet";
import { boxPixels, decodeLuma, inset, pageJpegs, samples } from "./helpers/pdf-inspect";

const CATEGORY = "CAPersonalHealthIdentification";

async function mockWith422(page: Page, polygon: number[]): Promise<ApiMock> {
  let stopped = false;
  return mockBookletApi(page, (upload) => {
    if (!stopped && upload.pages.includes(2)) {
      stopped = true;
      return {
        status: 422,
        body: { error: { code: "pii_detected", message: "Personal identifier found", details: [{ page: 2, category: CATEGORY, polygon }], traceId: "t-422" } },
      };
    }
    return { status: 200, body: chunkResponse(upload.pages) };
  });
}

async function expectSuggestionBurnedIn(page: Page, api: ApiMock) {
  const resent = api.uploads.filter((u) => u.pages.includes(2));
  expect(resent).toHaveLength(2);
  const stored = await readPages(page);
  const page2 = stored.find((p) => p.pageIndex === 1)!;
  const suggestion = page2.boxes.find((b) => b.source === "server_suggestion" && b.enabled);
  expect(suggestion).toBeTruthy();
  const jpeg = (await pageJpegs(new Uint8Array(resent[1].pdf)))[resent[1].pages.indexOf(2)];
  const img = await decodeLuma(jpeg);
  expect(Math.max(...samples(img, inset(boxPixels(suggestion!, img.width, img.height), 3)))).toBeLessThanOrEqual(60);
  // The first attempt showed the reference uncovered; the resend doesn't.
  const firstJpeg = (await pageJpegs(new Uint8Array(resent[0].pdf)))[resent[0].pages.indexOf(2)];
  const first = await decodeLuma(firstJpeg);
  expect(Math.min(...samples(first, boxPixels(suggestion!, first.width, first.height), 1))).toBeLessThan(100);
}

test("422 pii_detected: fix the page from memory, re-acknowledge and resend", async ({ page }) => {
  const booklet = await uploadTestBooklet(page);
  const api = await mockWith422(page, booklet.referenceBoxInches);
  await fillHideForm(page, { name: FAKE_PII.name });
  const proceed = await prepareAndAcknowledge(page, 6);
  await proceed.click();

  await page.waitForURL(/\/review\/?\?doc=/);
  const stoppedRow = page.getByText("Stopped: page 2 still shows a personal health number");
  await expect(stoppedRow).toBeVisible();
  await expect(page.getByText("1 of 2 uploads read")).toBeVisible();
  await page.getByRole("link", { name: "Fix page 2" }).click();

  await page.waitForURL(/\/redact\/?\?doc=.*fix=2/);
  await expect(page.getByText("The server's check found a personal health number on page 2")).toBeVisible();
  await expect(page.getByLabel("Page", { exact: true })).toHaveValue("1");
  const list = page.getByRole("complementary", { name: "Boxes" });
  await expect(list.getByRole("button", { name: /Suggested by the server check/, pressed: true })).toBeVisible();
  // The original is still in memory, so detector boxes remain editable.
  await expect(list.getByRole("switch").first()).toBeEnabled();

  const again = await prepareAndAcknowledge(page, 6);
  await again.click();
  await page.waitForURL(/\/review\/?\?doc=/);
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  await expect(page.getByText("2 of 2 uploads read")).toBeVisible();
  // Only the upload with the fixed page was sent again.
  expect(api.uploads.map((u) => u.pages.join(","))).toEqual(expect.arrayContaining(["1,2,3,4,5", "5,6"]));
  expect(api.uploads).toHaveLength(3);
  await expectSuggestionBurnedIn(page, api);
});

test("422 pii_detected after a reload: fix on the saved redacted image", async ({ page }) => {
  const booklet = await uploadTestBooklet(page);
  const api = await mockWith422(page, booklet.referenceBoxInches);
  await fillHideForm(page, { name: FAKE_PII.name });
  await (await prepareAndAcknowledge(page, 6)).click();
  await page.waitForURL(/\/review\/?\?doc=/);
  const fixLink = page.getByRole("link", { name: "Fix page 2" });
  await expect(fixLink).toBeVisible();

  // A full page load drops the in-memory original; the fix works from the stored images.
  const href = (await fixLink.getAttribute("href"))!;
  const url = new URL(href, page.url());
  await page.goto(`/redact/${url.search}`);
  await expect(page.getByText("The server's check found a personal health number on page 2")).toBeVisible();
  await expect(page.getByText("This is the image that was already sent.")).toBeVisible();
  const list = page.getByRole("complementary", { name: "Boxes" });
  await expect(list.getByRole("button", { name: /Suggested by the server check/ })).toBeVisible();

  await page.getByLabel("Page", { exact: true }).selectOption("0");
  // Boxes already burned into the image can't be switched off.
  await expect(list.getByRole("switch").first()).toBeDisabled();

  await (await prepareAndAcknowledge(page, 6)).click();
  await page.waitForURL(/\/review\/?\?doc=/);
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  expect(api.uploads).toHaveLength(3);
  await expectSuggestionBurnedIn(page, api);
});
