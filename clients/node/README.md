# @pamfilico/tahidromos

Client and Playwright helpers for [tahidromos](https://github.com/pamfilico/tahidromos),
the development mail server you can reply to.

```sh
npm install --save-dev @pamfilico/tahidromos
```

No dependencies — it uses Node 18's built-in `fetch`.

## Playwright

```js
// fixtures.js
export { test, expect } from "@pamfilico/tahidromos/playwright";
```

```js
import { test, expect } from "./fixtures.js";

test("a new user can sign in from the email", async ({ page, inbox }) => {
  await page.goto("/signup");
  await page.fill("#email", inbox.address);
  await page.click("#register");

  await page.goto(await inbox.waitForLink({ subjectContains: "Confirm" }));
  await expect(page.locator("h1")).toHaveText("Welcome");
});
```

`inbox` is a mailbox created for that test alone and deleted afterwards, so
workers cannot read each other's mail. `waitFor` long-polls the server, so
nothing sleeps.

### Fixtures

| Fixture | What it gives you |
| --- | --- |
| `inbox` | A throwaway mailbox for this test |
| `inboxFactory` | Make several isolated mailboxes in one test |
| `mail` | The `Tahidromos` client, per worker |
| `echoBot` | An address that always replies, threaded correctly |

Set `TAHIDROMOS_KEEP=1` to leave the inboxes behind and inspect them at
<http://localhost:8080>. `TAHIDROMOS_URL` points at a server elsewhere.

### Two flows worth a helper

```js
import { signInWithMagicLink, enterOneTimeCode } from "@pamfilico/tahidromos/playwright";

test("magic link", async ({ page, inbox }) => {
  await page.goto("/login");
  await signInWithMagicLink(page, inbox, { subjectContains: "Sign in" });
  await expect(page).toHaveURL(/dashboard/);
});

test("one-time code", async ({ page, inbox }) => {
  await page.goto("/login");
  await page.fill("#email", inbox.address);
  await page.click("#send-code");
  await enterOneTimeCode(page, inbox);
  await expect(page).toHaveURL(/dashboard/);
});
```

## Cypress, Vitest, or anything else

The client is plain JavaScript; the Playwright part is optional.

```js
import { Tahidromos } from "@pamfilico/tahidromos";

const mail = new Tahidromos();
const inbox = await mail.inbox("checkout");

// ... make your app send something ...

const message = await inbox.waitFor({ subjectContains: "Receipt" });
console.log(message.code, message.link, message.strippedText);

await mail.cleanup();     // delete every inbox this client made
```

In Cypress, wrap the calls in a task (they are Node-side, not browser-side):

```js
// cypress.config.js
import { Tahidromos } from "@pamfilico/tahidromos";
const mail = new Tahidromos();

export default defineConfig({
  e2e: {
    setupNodeEvents(on) {
      on("task", {
        newInbox: (prefix) => mail.inbox(prefix).then((i) => i.address),
        waitForEmail: ({ address, ...filters }) =>
          mail.mailbox(address).waitFor(filters).then((m) => m.data),
      });
    },
  },
});
```

## What a message gives you

| Property | |
| --- | --- |
| `subject` `from` `to` `cc` `text` `html` | the obvious ones |
| `strippedText` | the body without the quoted thread or signature |
| `link` `links` `linkContaining(s)` | the magic link, and the rest |
| `code` | the one-time code, if there is one |
| `messageId` `inReplyTo` `references` `depth` | the thread position |
| `raw` | the full RFC 5322 source |

## Filters for `waitFor`

`subjectContains` · `fromContains` · `toContains` · `textContains` ·
`linkContains` · `hasCode` · `messageId` · `inReplyTo` · `threadRoot` ·
`unseenOnly` · `markSeen` · `mailbox` · `timeout` (seconds)

They combine with AND. On a timeout you get `MessageNotFound` naming the
filters that did not match.

## Also on the client

```js
await mail.template("receipt", inbox.address, { name: "Alice" });  // 8 templates
await mail.scenario("bounce", inbox.address, { seed: 42 });        // 10 awkward shapes
await mail.conversation(["alice", "bob"], { turns: 6 });           // a real thread
await mail.spam({ subject: "WIN NOW!!!", text: "..." });           // score anything
```

## License

MIT
