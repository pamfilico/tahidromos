/**
 * Exercised against a running server:
 *   docker compose up -d && node --test clients/node/test/*.test.js
 */
import assert from "node:assert/strict";
import { after, describe, it } from "node:test";
import { MessageNotFound, Tahidromos } from "../src/index.js";

const mail = new Tahidromos({ runId: `nodetest-${Date.now().toString(16)}` });

// Top-level await, so the skip reason is known before describe() runs — a
// before() hook fires too late to decide whether a test is skipped.
const up = await mail.isUp();
if (!up) {
  console.error(`tahidromos is not running at ${mail.url}; skipping. Start it with:`);
  console.error("  docker run -p 1025:25 -p 1587:587 -p 1143:143 -p 8080:8080 ghcr.io/pamfilico/tahidromos");
}

after(async () => { if (up) await mail.cleanup(); });

const maybe = (name, fn) => it(name, { skip: up ? false : "server not running" }, fn);

describe("tahidromos client", () => {
  maybe("creates an isolated inbox", async () => {
    const a = await mail.inbox("alpha");
    const b = await mail.inbox("beta");
    assert.notEqual(a.address, b.address);
    assert.match(a.address, /^alpha-[0-9a-f]+@/);
  });

  maybe("waits for a message without sleeping", async () => {
    const inbox = await mail.inbox("waiting");
    await mail.send("noreply@tahidromos.test", inbox.address, "Hello there", "Body text.");
    const message = await inbox.waitFor({ subjectContains: "Hello there", timeout: 20 });
    assert.equal(message.from, "noreply@tahidromos.test");
    assert.match(message.text, /Body text/);
  });

  maybe("throws MessageNotFound on timeout", async () => {
    const inbox = await mail.inbox("timeout");
    await assert.rejects(
      () => inbox.waitFor({ subjectContains: "never arrives", timeout: 2 }),
      MessageNotFound,
    );
  });

  maybe("extracts a one-time code and a magic link", async () => {
    const inbox = await mail.inbox("otp");
    await mail.scenario("otp", inbox.address, { seed: 21 });

    const code = await inbox.waitForCode({ timeout: 20 });
    assert.match(code, /^\d{6}$/);

    const link = await inbox.waitForLink({ linkContains: "magic", timeout: 20 });
    assert.match(link, /magic\?token=/);
  });

  maybe("strips a quoted reply", async () => {
    const inbox = await mail.inbox("quoted");
    await mail.scenario("deep_reply", inbox.address, { seed: 3 });
    const message = await inbox.waitFor({ subjectContains: "order", timeout: 20 });
    assert.equal(message.strippedText, "Yes, please cancel it.");
    assert.ok(message.text.includes(">"), "the full body should still be there");
  });

  maybe("renders and delivers a template", async () => {
    const inbox = await mail.inbox("template");
    await mail.template("receipt", inbox.address, { name: "Zaphod" });
    const message = await inbox.waitFor({ subjectContains: "receipt", timeout: 20 });
    assert.ok(message.html.includes("Zaphod"));
    assert.ok(message.text.length > 0, "a plain-text alternative should be present");
  });

  maybe("gets a threaded reply from the echo bot", async () => {
    const bot = await mail.echoBot();
    assert.ok(bot, "no bot configured");
    const inbox = await mail.inbox("bot");

    const sent = await inbox.send(bot, "Ticket #42", "Is this fixed?");
    const reply = await inbox.waitFor({ inReplyTo: sent.message_id, timeout: 45 });

    assert.equal(reply.subject, "Re: Ticket #42");
    assert.deepEqual(reply.references, [sent.message_id]);
  });

  maybe("replies to a reply and deepens the thread", async () => {
    const bot = await mail.echoBot();
    const inbox = await mail.inbox("chain");
    const sent = await inbox.send(bot, "Ticket #43", "turn one");
    const first = await inbox.waitFor({ inReplyTo: sent.message_id, timeout: 45 });

    await inbox.reply(first, "turn three");
    const second = await inbox.waitFor({
      subjectContains: "Ticket #43", fromContains: bot, unseenOnly: true, timeout: 45,
    });
    assert.ok(second.depth > first.depth, `${second.depth} should exceed ${first.depth}`);
  });

  maybe("forwards a message and keeps its attachments", async () => {
    const forwarder = await mail.inbox("fwd-from");
    const recipient = await mail.inbox("fwd-to");
    await mail.scenario("attachment", forwarder.address, { seed: 5 });
    const original = await forwarder.waitFor({ subjectContains: "Invoice", timeout: 20 });

    const forwarded = await forwarder.forward(original, recipient.address, "Passing this on.");
    const arrived = await recipient.waitFor({ messageId: forwarded.message_id, timeout: 20 });

    assert.ok(arrived.subject.startsWith("Fwd:"));
    assert.equal(arrived.inReplyTo, null, "a forward is not a reply");
    assert.equal(arrived.forwardedFrom, original.messageId);
    assert.ok(arrived.text.includes("Forwarded message"));
    const names = arrived.attachments.map((a) => a.filename);
    assert.ok(names.includes("invoice-10432.pdf"), `attachments lost: ${names}`);
  });

  maybe("replies to a forward and threads correctly", async () => {
    const forwarder = await mail.inbox("chain-from");
    const recipient = await mail.inbox("chain-to");
    await mail.send("alice@tahidromos.test", forwarder.address, "Quarterly numbers", "See attached.");
    const original = await forwarder.waitFor({ subjectContains: "Quarterly", timeout: 20 });

    const forwarded = await forwarder.forward(original, recipient.address);
    const arrived = await recipient.waitFor({ messageId: forwarded.message_id, timeout: 20 });

    await recipient.reply(arrived, "Thanks, looks right.");
    const back = await forwarder.waitFor({ subjectContains: "Quarterly", unseenOnly: true, timeout: 20 });

    assert.equal(back.subject, "Re: Fwd: Quarterly numbers");
    assert.equal(back.inReplyTo, forwarded.message_id);
    assert.ok(back.references.includes(original.messageId), "should reach back to the original");
  });

  maybe("scores spam", async () => {
    const result = await mail.spam({
      subject: "WIN FREE MONEY NOW!!!",
      text: "ACT NOW! YOU HAVE WON THE LOTTERY.",
    });
    assert.equal(result.verdict, "spam");
    assert.ok(result.hits.length > 0);
  });

  maybe("cleanup removes only this run's inboxes", async () => {
    const box = await mail.inbox("temporary");
    const removed = await mail.cleanup();
    assert.ok(removed.includes(box.address));
    assert.ok(await mail.isUp(), "the server should still be fine");
  });
});
