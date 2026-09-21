import { expect, test, type Page } from "@playwright/test";

import { chunkResponse, fillHideForm, mockBookletApi, prepareAndAcknowledge, uploadTestBooklet } from "./helpers/app";
import { FAKE_PII } from "./helpers/booklet";

async function redactAndContinue(page: Page) {
  await uploadTestBooklet(page);
  await fillHideForm(page, { name: FAKE_PII.name });
  await (await prepareAndAcknowledge(page, 6)).click();
  await page.waitForURL(/\/review\/?\?doc=/);
}

test("explains when AI is switched off and offers the demo", async ({ page }) => {
  const api = await mockBookletApi(page, () => ({
    status: 503,
    body: { error: { code: "ai_disabled", message: "AI is disabled", traceId: "t" } },
  }));
  await redactAndContinue(page);
  const alert = page.getByRole("alert").filter({ hasText: "AI features are switched off right now" });
  await expect(alert).toBeVisible();
  await expect(alert.getByRole("link", { name: "Explore the demo plan instead" })).toHaveAttribute("href", "/");
  // The run stops instead of hammering the API with the remaining uploads.
  expect(api.uploads.length).toBeLessThanOrEqual(2);
  await expect(page.getByRole("button", { name: "Save plan" })).toHaveCount(0);
});

test("retries a failed upload and then builds the plan", async ({ page }) => {
  let failed = false;
  const api = await mockBookletApi(page, (upload) => {
    if (!failed && upload.pages[0] === 5) {
      failed = true;
      return { status: 500, body: { error: { code: "upstream_error", message: "Upstream failed" } } };
    }
    return { status: 200, body: chunkResponse(upload.pages) };
  });
  await redactAndContinue(page);
  await expect(page.getByText("Couldn't be read. Nothing was saved on the server.")).toBeVisible();
  await expect(page.getByText("1 of 2 uploads read")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  expect(api.uploads.map((u) => u.pages[0]).sort()).toEqual([1, 5, 5]);

  // Reloading the review keeps finished uploads instead of sending them again.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Check your plan" })).toBeVisible();
  expect(api.uploads).toHaveLength(3);
});

test("uses the sample booklet when this build has it", async ({ page }) => {
  const sample = await page.request.get("/samples/ca-northwind-booklet.pdf");
  await page.goto("/redact/");
  await page.getByRole("button", { name: "Use the sample booklet" }).click();
  if (sample.ok()) {
    await expect(page.getByText("Northwind sample booklet")).toBeVisible();
  } else {
    await expect(page.getByText("The sample booklet isn't available in this build yet.", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue", exact: true })).toBeDisabled();
  }
});
