import { expect, test, type Page } from "@playwright/test";

async function startDemo(page: Page, name: "Try the Canadian demo" | "Try the Australian demo") {
  await page.goto("/");
  await page.getByRole("button", { name }).click();
  await expect(page).toHaveURL(/\/plan\/?$/);
  await expect(page.getByRole("heading", { name: "Left to claim this benefit year" })).toBeVisible();
}

// The demo must work with the API unreachable, so only the static app itself may load.
test.beforeEach(async ({ page, baseURL }) => {
  const origin = new URL(baseURL ?? "http://localhost:3000").origin;
  await page.route("**/*", (route) => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));
});

test("Canadian demo dashboard renders in under 2 seconds with the API down", async ({ page }) => {
  await startDemo(page, "Try the Canadian demo");
  await expect(page.getByText("Fictional data only.")).toBeVisible();

  const started = Date.now();
  await page.goto("/plan/");
  await expect(page.getByRole("heading", { name: "Left to claim this benefit year" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Benefits" }).getByText("Massage therapy", { exact: true })).toBeVisible();
  expect(Date.now() - started).toBeLessThan(2000);

  const benefits = page.getByRole("region", { name: "Benefits" });
  const massageItem = benefits.locator("details").filter({ has: page.locator("summary", { hasText: "Massage therapy" }) });
  const massage = massageItem.locator("summary");
  await expect(massage).toContainText("$100");
  await expect(massage.getByRole("meter")).toBeVisible();

  await page.getByRole("switch", { name: "Count submitted claims" }).click();
  await expect(massage).toContainText("$20");

  await massage.click();
  await expect(massageItem.getByText("Booklet page 5")).toBeVisible();
  await expect(massageItem.getByRole("link", { name: "Ask about this" })).toHaveAttribute("href", /\/chat\/?\?q=/);

  await page.getByRole("radio", { name: /\[MEMBER_B\]/ }).click();
  await expect(benefits.getByText(/in use for \[MEMBER_B\]/)).toBeVisible();
  await expect(benefits.locator("summary", { hasText: "Glasses and contact lenses" })).toContainText("$40");
  await expect(page.getByRole("region", { name: "Deductible" })).toContainText("Met");
});

test("Australian demo shows schedules, the therapies pool, hospital cover and a deadline", async ({ page }) => {
  await startDemo(page, "Try the Australian demo");
  await expect(page.getByRole("heading", { name: "Silver Plus Hospital and Mid Extras" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Shared maximums" })).toContainText("Therapies combined limit");
  await expect(page.getByRole("region", { name: "Claim deadlines" })).toContainText("18 days left");
  await expect(page.getByRole("region", { name: "Silver Plus hospital" })).toContainText("Pregnancy and birth");
});

test("a new claim gets a live estimate, then moves through submitted to paid", async ({ page }) => {
  await startDemo(page, "Try the Canadian demo");
  await page.goto("/claims/?id=new");
  await expect(page.getByRole("heading", { name: "New claim" })).toBeVisible();

  await page.getByRole("combobox").fill("RMT");
  await page.getByRole("option", { name: /Massage therapy/ }).click();
  await page.getByLabel("Provider").fill("Harbour Massage Therapy");
  await page.getByLabel("Amount charged").fill("120");

  const estimate = page.getByRole("complementary", { name: "Estimated payback" });
  await expect(estimate).toContainText("80% of $120");
  await expect(estimate).toContainText("Capped at $80 a visit");
  await expect(estimate).toContainText("You pay $40.00 of $120.00 charged.");

  await page.getByRole("button", { name: "Save and mark as submitted" }).click();
  await expect(page).toHaveURL(/id=clm-/);
  await expect(page.getByText("Submitted", { exact: true }).first()).toBeVisible();

  await page.getByLabel("Amount the plan paid").fill("80");
  await page.getByRole("button", { name: "Save outcome" }).click();
  await expect(page.getByRole("heading", { name: "Plan paid" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Claim history" })).toContainText("Paid");
});

test("settings reset the demo and delete everything after confirming", async ({ page }) => {
  await startDemo(page, "Try the Canadian demo");
  await page.goto("/settings/");
  await page.getByRole("button", { name: "Reset demo" }).last().click();
  await expect(page.getByText("Demo reset.")).toBeVisible();

  await page.getByRole("button", { name: "Delete all data" }).click();
  await page.getByRole("button", { name: "Yes, delete everything" }).click();
  await expect(page.getByText("All data deleted.")).toBeVisible();

  await page.goto("/plan/");
  await expect(page.getByRole("heading", { name: "No plan in this browser yet" })).toBeVisible();
});
