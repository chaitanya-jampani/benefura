// AI_MODE=fake scripts the same tool rounds a real agent makes.
import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

const CA_PLAN = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../samples/fixtures/ca-northwind.plan.json"), "utf8"),
) as { id: string };

async function seedPlan(page: Page) {
  await page.goto("/chat/");
  // Dexie has created the database once the composer is on screen.
  await expect(page.getByLabel("Message the assistant")).toBeVisible();
  await page.evaluate(async (plan) => {
    await new Promise<void>((resolve, reject) => {
      const open = indexedDB.open("benefura");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const idb = open.result;
        const tx = idb.transaction(["plans", "settings", "chats", "claims"], "readwrite");
        const now = new Date().toISOString();
        tx.objectStore("plans").put({ id: plan.id, plan, isDemo: true, createdAt: now, updatedAt: now });
        tx.objectStore("settings").put({ key: "activePlanId", value: plan.id });
        tx.objectStore("chats").clear();
        tx.objectStore("claims").clear();
        tx.oncomplete = () => {
          idb.close();
          resolve();
        };
        tx.onerror = () => reject(tx.error);
      };
    });
  }, CA_PLAN);
  await page.reload();
  await expect(page.getByLabel("Message the assistant")).toBeVisible();
}

async function ask(page: Page, text: string) {
  await page.getByLabel("Message the assistant").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
}

async function storedClaims(page: Page): Promise<Array<{ status: string; history: Array<Record<string, unknown>> }>> {
  return page.evaluate(
    () =>
      new Promise((resolve, reject) => {
        const open = indexedDB.open("benefura");
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const idb = open.result;
          const request = idb.transaction("claims").objectStore("claims").getAll();
          request.onsuccess = () => {
            idb.close();
            resolve(request.result);
          };
          request.onerror = () => reject(request.error);
        };
      }),
  );
}

const assistant = (page: Page) => page.getByTestId("assistant-message").last();

test.describe("chat", () => {
  test.beforeEach(async ({ page }) => {
    await seedPlan(page);
  });

  test("approval flow: a draft claim is saved only after approval, with agent provenance", async ({ page }) => {
    await ask(page, "Draft a claim for a $120 massage today");

    const card = page.getByTestId("approval-card");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-state", "approval-requested");
    await expect(card).toContainText("[MEMBER_A]");
    await expect(card).toContainText("Massage therapy");
    await expect(card).toContainText("$120.00");
    expect(await storedClaims(page)).toHaveLength(0);

    await card.getByRole("button", { name: "Approve" }).click();
    await expect(card).toContainText("Approved and saved");
    await expect(assistant(page)).toContainText("I added a draft claim for $120.00");

    const claims = await storedClaims(page);
    expect(claims).toHaveLength(1);
    expect(claims[0].status).toBe("draft");
    expect(claims[0].history.at(-1)).toMatchObject({
      actor: "agent",
      agentName: "benefura-plan-claims",
      agentVersion: expect.any(String),
      traceId: expect.stringMatching(/^[0-9a-f]{32}$/),
    });
  });

  test("a declined approval is acknowledged and not retried", async ({ page }) => {
    await ask(page, "Draft a claim for a $120 massage today");
    const card = page.getByTestId("approval-card");
    await card.getByRole("button", { name: "Decline" }).click();
    await card.getByLabel("What should change? (optional)").fill("The amount was $90");
    await card.getByRole("button", { name: "Decline" }).click();

    await expect(assistant(page)).toContainText("Okay, I won't create that claim");
    await expect(card).toHaveAttribute("data-state", "output-denied");
    await expect(card).toContainText("Declined: The amount was $90");

    await expect(page.getByLabel("Message the assistant")).toBeEnabled();
    await page.waitForTimeout(500);
    await expect(page.getByTestId("approval-card")).toHaveCount(1);
    expect(await storedClaims(page)).toHaveLength(0);
  });

  test("a public reference answer renders citations with licence and attribution", async ({ page }) => {
    await ask(page, "Is massage therapy a medical expense for tax?");

    const citations = assistant(page).getByTestId("citation");
    await expect(citations.first()).toBeVisible();
    await expect(citations.first()).toContainText("Canada Revenue Agency");
    await expect(citations.first()).toContainText("Crown copyright");
    await expect(citations.first()).toContainText("Not an official version");
    const href = await citations.first().getByRole("link").getAttribute("href");
    expect(href).toMatch(/^https:\/\/www\.canada\.ca\//);
    await expect(assistant(page)).toContainText("Knowledge agent");
  });

  test("a mixed question uses both agents with a visible status", async ({ page }) => {
    await ask(page, "Is massage covered and is it a medical expense for tax?");

    await expect(assistant(page).getByTestId("chat-status")).toContainText("Checked public reference material");
    await expect(assistant(page).getByTestId("citation").first()).toBeVisible();
    await expect(assistant(page)).toContainText("On the tax side:");
    await expect(assistant(page)).toContainText("(booklet p. 5)");
  });

  test("a hard-tier identifier is refused before any model sees it", async ({ page }) => {
    await ask(page, "My SIN is 046 454 286, what can I claim?");

    await expect(page.getByTestId("pii-warning")).toContainText("Social insurance number");
    await expect(page.getByTestId("user-message").last()).toContainText("Not sent to the assistant");
    await page.getByRole("button", { name: "Edit message" }).click();
    await expect(page.getByLabel("Message the assistant")).toHaveValue("My SIN is 046 454 286, what can I claim?");
    await expect(page.getByTestId("pii-warning")).toHaveCount(0);
  });

  test("the privacy inspector lists chat requests with their trace ids", async ({ page }) => {
    await ask(page, "Is massage therapy a medical expense for tax?");
    await expect(assistant(page).getByTestId("citation").first()).toBeVisible();

    await page.getByRole("button", { name: /Privacy inspector/ }).click();
    const dialog = page.getByRole("dialog", { name: "Sent from this browser" });
    await expect(dialog).toBeVisible();
    const row = dialog.getByTestId("outbound-row").filter({ hasText: "Chat message" }).first();
    await expect(row).toContainText("POST");
    await expect(row).toContainText("Trace");
    await row.getByText("What was sent").click();
    await expect(row).toContainText("Is massage therapy a medical expense for tax?");
  });
});
