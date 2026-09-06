/**
 * Playwright fixtures.
 *
 *   // fixtures.js
 *   export { test, expect } from "@pamfilico/tahidromos/playwright";
 *
 *   // signup.spec.js
 *   import { test, expect } from "./fixtures.js";
 *
 *   test("a new user can sign in from the email", async ({ page, inbox }) => {
 *     await page.goto("/signup");
 *     await page.fill("#email", inbox.address);
 *     await page.click("#register");
 *
 *     await page.goto(await inbox.waitForLink({ subjectContains: "Confirm" }));
 *     await expect(page.locator("h1")).toHaveText("Welcome");
 *   });
 *
 * `inbox` is a mailbox for that test alone, so the workers cannot read each
 * other's mail — and it is deleted when the test ends.
 */

import { test as base, expect } from "@playwright/test";
import { Tahidromos } from "./index.js";

export const test = base.extend({
  /** The server, once per worker. */
  mail: [
    async ({}, use, workerInfo) => {
      const client = new Tahidromos({
        url: process.env.TAHIDROMOS_URL,
        runId: `pw-${workerInfo.workerIndex}-${Date.now().toString(16)}`,
      });
      await client.waitUntilReady();
      await use(client);
      if (!process.env.TAHIDROMOS_KEEP) await client.cleanup();
    },
    { scope: "worker" },
  ],

  /** A mailbox used by this test and nothing else. */
  inbox: async ({ mail }, use, testInfo) => {
    const prefix = testInfo.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 32);
    const box = await mail.inbox(prefix || "test");
    await use(box);
    if (!process.env.TAHIDROMOS_KEEP) {
      await box.delete().catch(() => {});
    }
  },

  /** Make as many isolated inboxes as one test needs. */
  inboxFactory: async ({ mail }, use) => {
    const made = [];
    await use(async (prefix = "test") => {
      const box = await mail.inbox(prefix);
      made.push(box);
      return box;
    });
    if (!process.env.TAHIDROMOS_KEEP) {
      await Promise.all(made.map((box) => box.delete().catch(() => {})));
    }
  },

  /** An address that always replies, threaded correctly. */
  echoBot: async ({ mail }, use) => {
    const address = await mail.echoBot();
    test.skip(!address, "no auto-responder mailbox (BOT_ENABLED=false?)");
    await use(address);
  },
});

export { expect };

/**
 * Sign in through a magic link, the flow most apps share.
 * Returns the message, in case you want to assert on it too.
 */
export async function signInWithMagicLink(page, inbox, {
  emailSelector = "#email",
  submitSelector = "button[type=submit]",
  subjectContains,
  linkContains,
  timeout = 30,
} = {}) {
  await page.fill(emailSelector, inbox.address);
  await page.click(submitSelector);

  const message = await inbox.waitFor({ subjectContains, linkContains, timeout });
  const link = linkContains ? message.linkContaining(linkContains) : message.link;
  if (!link) throw new Error(`"${message.subject}" contained no link to follow`);

  await page.goto(link);
  return message;
}

/** Read a one-time code out of the inbox and type it in. */
export async function enterOneTimeCode(page, inbox, {
  codeSelector = "#code",
  submitSelector = "button[type=submit]",
  timeout = 30,
} = {}) {
  const message = await inbox.waitFor({ hasCode: true, timeout });
  await page.fill(codeSelector, message.code);
  await page.click(submitSelector);
  return message.code;
}
