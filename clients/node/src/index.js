/**
 * A client for a tahidromos server.
 *
 *   import { Tahidromos } from "@pamfilico/tahidromos";
 *
 *   const mail = new Tahidromos();
 *   const inbox = await mail.inbox("signup");
 *
 *   await page.fill("#email", inbox.address);
 *   await page.click("#register");
 *
 *   const message = await inbox.waitFor({ subjectContains: "Confirm" });
 *   await page.goto(message.link);
 *
 * Every wait is a long poll on the server, so nothing here sleeps.
 * No dependencies — Node 18's built-in fetch is all it uses.
 */

const DEFAULT_URL = process.env.TAHIDROMOS_URL || "http://localhost:8080";

export class TahidromosError extends Error {}

/** Thrown when a wait times out. Reads as a failed expectation, not a crash. */
export class MessageNotFound extends TahidromosError {}

/** camelCase in, snake_case out — the API speaks Python. */
const FILTER_KEYS = {
  subjectContains: "subject_contains",
  fromContains: "from_contains",
  toContains: "to_contains",
  textContains: "text_contains",
  linkContains: "link_contains",
  hasCode: "has_code",
  messageId: "message_id",
  inReplyTo: "in_reply_to",
  threadRoot: "thread_root",
  unseenOnly: "unseen_only",
  markSeen: "mark_seen",
  mailbox: "mailbox",
  timeout: "timeout",
  interval: "interval",
};

function toFilters(options = {}) {
  const out = {};
  for (const [key, value] of Object.entries(options)) {
    if (value === undefined) continue;
    out[FILTER_KEYS[key] ?? key] = value;
  }
  return out;
}

export class Message {
  constructor(data) {
    this.data = data;
  }
  get uid() { return this.data.uid; }
  get subject() { return this.data.subject ?? ""; }
  get from() { return this.data.from ?? ""; }
  get to() { return this.data.to ?? []; }
  get cc() { return this.data.cc ?? []; }
  get text() { return this.data.text ?? ""; }
  get html() { return this.data.html ?? null; }
  /** The body without the quoted thread or signature beneath it. */
  get strippedText() { return this.data.stripped_text ?? ""; }
  get links() { return this.data.links ?? []; }
  /** The first link — usually the magic link or confirmation URL. */
  get link() { return this.data.link ?? null; }
  /** The one-time code, if the message has one. */
  get code() { return this.data.code ?? null; }
  get messageId() { return this.data.message_id ?? ""; }
  get inReplyTo() { return this.data.in_reply_to ?? null; }
  get references() { return this.data.references ?? []; }
  get threadRoot() { return this.data.thread_root ?? ""; }
  get depth() { return this.data.depth ?? 0; }
  get seen() { return Boolean(this.data.seen); }
  /** Filename, content type and size for each attached part. */
  get attachments() { return this.data.attachments ?? []; }
  /** The Message-ID this was forwarded from, if it is a forward. */
  get forwardedFrom() { return this.data.forwarded_from ?? null; }
  get forwardCount() { return this.data.forward_count ?? 0; }
  get raw() { return this.data.raw ?? ""; }

  linkContaining(needle) {
    const found = this.links.find((l) => l.toLowerCase().includes(needle.toLowerCase()));
    if (!found) {
      throw new MessageNotFound(
        `no link containing "${needle}" in "${this.subject}"; found ${JSON.stringify(this.links)}`);
    }
    return found;
  }

  toString() {
    return `<Message ${this.from} -> ${this.to.join(", ")}: "${this.subject}">`;
  }
}

export class Inbox {
  constructor(client, address, password) {
    this.client = client;
    this.address = address;
    this.password = password;
  }

  /**
   * Block until a message matching every filter arrives.
   * @param {object} options subjectContains, fromContains, textContains,
   *   linkContains, hasCode, messageId, inReplyTo, unseenOnly, markSeen, timeout
   */
  async waitFor(options = {}) {
    const timeout = options.timeout ?? 30;
    const response = await this.client._fetch("/wait", {
      method: "POST",
      body: { user: this.address, ...toFilters({ ...options, timeout }) },
      timeoutMs: (timeout + 15) * 1000,
    });
    if (response.status === 408) {
      throw new MessageNotFound(
        `${this.address}: nothing matched ${JSON.stringify(options)} within ${timeout}s`);
    }
    return new Message(await this.client._json(response));
  }

  /** Wait for a message with a one-time code and return just the code. */
  async waitForCode(options = {}) {
    return (await this.waitFor({ hasCode: true, ...options })).code;
  }

  /** Wait for a message with a link and return just the link. */
  async waitForLink(options = {}) {
    const message = await this.waitFor({ ...options });
    if (!message.link) {
      throw new MessageNotFound(`"${message.subject}" contains no link`);
    }
    return options.linkContains ? message.linkContaining(options.linkContains) : message.link;
  }

  async messages({ mailbox = "INBOX", unseen = false, limit = 100 } = {}) {
    const query = new URLSearchParams({ mailbox, unseen: String(unseen), limit: String(limit) });
    const data = await this.client._get(
      `/messages/${encodeURIComponent(this.address)}?${query}`);
    return data.messages.map((m) => new Message(m));
  }

  async message(uid, mailbox = "INBOX") {
    return new Message(await this.client._get(
      `/messages/${encodeURIComponent(this.address)}/${uid}?mailbox=${mailbox}`));
  }

  async threads() {
    return (await this.client._get(`/threads/${encodeURIComponent(this.address)}`)).threads;
  }

  async send(to, subject = "", text = "", extra = {}) {
    return this.client.send(this.address, to, subject, text, extra);
  }

  /** Reply to a message; the server builds the threading headers. */
  async reply(message, text, extra = {}) {
    const uid = message instanceof Message ? message.uid : message;
    return this.client._post("/reply", { user: this.address, uid, text, ...extra });
  }

  /**
   * Forward a message on. The Fwd: prefix, the forwarded-header block and the
   * attachments are handled by the server.
   * @param {object} extra e.g. { mode: "attachment", cc: [...], note: "..." }
   */
  async forward(message, to, note = "", extra = {}) {
    const uid = message instanceof Message ? message.uid : message;
    return this.client._post("/forward", {
      user: this.address, uid, note,
      to: Array.isArray(to) ? to.map(String) : [String(to)],
      ...extra,
    });
  }

  async markRead(message, seen = true) {
    const uid = message instanceof Message ? message.uid : message;
    return this.client._patch(
      `/messages/${encodeURIComponent(this.address)}/${uid}`, { seen });
  }

  async purge(mailbox = "INBOX") {
    const result = await this.client._delete(
      `/messages/${encodeURIComponent(this.address)}?mailbox=${mailbox}`);
    return result.deleted;
  }

  async delete() {
    return (await this.client._delete(`/inboxes/${encodeURIComponent(this.address)}`)).deleted;
  }

  toString() { return this.address; }
}

export class Tahidromos {
  constructor({ url = DEFAULT_URL, runId } = {}) {
    this.url = url.replace(/\/$/, "");
    this.runId = runId || process.env.TAHIDROMOS_RUN_ID ||
      `node-${Math.random().toString(16).slice(2, 10)}`;
  }

  // -- plumbing --------------------------------------------------------

  async _fetch(path, { method = "GET", body, timeoutMs = 30000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(`${this.url}${path}`, {
        method,
        headers: body ? { "content-type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (error) {
      throw new TahidromosError(`cannot reach tahidromos at ${this.url}: ${error.message}`);
    } finally {
      clearTimeout(timer);
    }
  }

  async _json(response) {
    if (!response.ok) {
      throw new TahidromosError(`${response.status} ${(await response.text()).slice(0, 400)}`);
    }
    return response.json();
  }

  async _get(path) { return this._json(await this._fetch(path)); }
  async _post(path, body) { return this._json(await this._fetch(path, { method: "POST", body })); }
  async _patch(path, body) { return this._json(await this._fetch(path, { method: "PATCH", body })); }
  async _delete(path) { return this._json(await this._fetch(path, { method: "DELETE" })); }

  // -- server ----------------------------------------------------------

  async health() { return this._get("/health"); }

  async isUp() {
    try { return (await this.health()).status === "ok"; } catch { return false; }
  }

  /** Poll /health until the server answers — for CI startup. */
  async waitUntilReady(timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.isUp()) return;
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new TahidromosError(
      `tahidromos at ${this.url} was not ready within ${timeoutMs}ms. Start it with:\n` +
      `  docker run -p 1025:25 -p 1587:587 -p 1143:143 -p 8080:8080 ghcr.io/pamfilico/tahidromos`);
  }

  async overview() { return this._get("/overview"); }
  async apps() { return this._get("/apps"); }

  // -- inboxes ---------------------------------------------------------

  /** A brand-new mailbox with a unique address, tagged with this run. */
  async inbox(prefix = "test", domain) {
    const data = await this._post("/inboxes", { prefix, domain, run_id: this.runId });
    return new Inbox(this, data.address, data.password);
  }

  /** An existing mailbox, such as a seeded one like `alice`. */
  mailbox(address, password = "password") {
    return new Inbox(this, address, password);
  }

  /** Delete every inbox this client created. */
  async cleanup() {
    const query = new URLSearchParams({ run_id: this.runId });
    return (await this._delete(`/inboxes?${query}`)).deleted;
  }

  /** The auto-responder's address — something that always replies. */
  async echoBot() {
    const overview = await this.overview();
    for (const app of overview.apps) {
      for (const box of app.mailboxes) if (box.is_bot) return box.address;
    }
    return null;
  }

  // -- sending ---------------------------------------------------------

  async send(from, to, subject = "", text = "", extra = {}) {
    return this._post("/send", {
      from: String(from), to: Array.isArray(to) ? to.map(String) : [String(to)],
      subject, text, ...extra,
    });
  }

  async template(name, to, context = {}) {
    return this._post("/templates", { name, to: String(to), context });
  }

  async templates() { return (await this._get("/templates")).templates; }

  async scenario(name, to, options = {}) {
    return this._post("/scenarios", { name, to: String(to), ...options });
  }

  async scenarios() { return (await this._get("/scenarios")).scenarios; }

  /** Mailboxes that forward everything on, from the app config. */
  async forwardRules() { return this._get("/forwards"); }

  async conversation(participants, options = {}) {
    return this._post("/conversation", {
      participants: participants.map(String), ...options,
    });
  }

  async spam(payload) { return this._post("/spam", payload); }
  async parse(text = "", html = "") { return this._post("/parse", { text, html }); }
}

export default Tahidromos;
