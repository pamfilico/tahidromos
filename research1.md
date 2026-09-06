# Email Testing & Dev Mail Servers: Developer Pain Points, Competitive Gaps, and Feature Recommendations for tahidromos

> ## Annotation — 6 September 2026
>
> Reviewed against the code and acted on. Notation used below:
>
> * ~~struck through~~ = **done**, with a note saying how
> * **[CORRECTION]** = the research described tahidromos as it was that
>   morning; it was rewritten from scratch since, so some facts changed
> * **[OPEN]** = agreed, not built yet
> * **[SKIPPED]** = deliberately not doing, with the reason
>
> The suite went from 55 to **166 pytest tests plus 12 JavaScript ones** in
> the process, all running in CI. The one rule I held
> to throughout: the zero-configuration `docker run` had to keep working with
> no flags, so everything new is either free or behind a single environment
> variable that announces itself when it is off.

## TL;DR
- The single biggest structural gap in the entire dev-mail-server category is that the popular tools (MailHog, Mailpit, MailCatcher, Maildev, smtp4dev) are **sinks** — they capture outbound mail but cannot receive a reply or hold a conversation. tahidromos's real-mailbox + reply + threading + echo-bot model directly answers a request that has sat open in MailHog since 2016 (issue #111) and is exactly what inbound-mail / ticketing / AI-agent apps need to test. This is a genuine differentiator, not a duplicate.
- The loudest recurring complaints are: MailHog is abandoned (last release August 2020); SaaS inbox tools (Mailtrap, MailSlurp, Mailosaur) are priced painfully for small teams and OSS; flaky email tests from `sleep()`/polling in CI; and no easy way to test **inbound** email, threading/In-Reply-To/References, or IMAP locally. tahidromos's `/wait` long-poll, real IMAP, and thread grouping hit these squarely.
- To go from "nobody knows about it" to adoption, prioritize (1) a killer README positioning ("the dev mail server that can reply"), (2) language/framework test helpers (pytest fixtures, a Testcontainers module, a GitHub Actions service snippet), (3) an MCP server so AI agents get a sandbox inbox — a hot, underserved niche — and (4) disciplined distribution via Show HN, r/selfhosted, awesome-lists, and direct comments on the exact open issues where people ask for this.

## Key Findings

1. **"Sinks can't reply" is a real, documented gap.** MailHog issue #111 ("Ability to reply to emails sent by application server," opened Sept 8 2016, still open, labelled `enhancement`) asks for precisely tahidromos's core feature. MailHog also has an abandoned experimental IMAP sub-project (mailhog/MailHog-IMAP, 3 stars, 1 open issue, never finished). Mailpit, Maildev, smtp4dev, and FakeSMTP are all outbound-capture-only; smtp4dev added IMAP *read* access but you still cannot reply and have the app receive it.

2. **MailHog is effectively dead and everyone knows it.** Last release v1.0.1 (August 2020), no meaningful commits since ~2022. Mailpit's own README states it "was originally inspired by MailHog which is no longer maintained and hasn't seen active development or security updates for a few years now"; Jeff Geerling's 2026 write-up "Local Email Debugging with Mailpit" concurs: "it seems like Mailhog has not been maintained for four years now… Mailpit is even easier to deploy, feels faster in my testing, and has all the features of Mailhog I grew to rely on (and then some)." Mailpit is the de-facto successor (drop-in ports 1025/8025, compatible API, single static Go binary or multi-arch Docker image). This means the "capture outbound" niche is *saturated and well-served* — tahidromos should not try to out-Mailpit Mailpit; it should own "receive + reply + threads."

3. **SaaS pricing genuinely pushes people to self-host.** Mailosaur has no permanent free tier — only a 14-day trial; its cheapest "Personal" plan is "From $20 per month, billed annually" (1 inbox with unlimited addresses, from 15,000 inbound emails/month based on a 500/day limit), and its team "Core" plan is "From $50 per month" for 5 seats / 75,000 inbound emails/month. MailSlurp's free tier can't send externally ("Free sending stays inside MailSlurp") and caps at 500 inbound/month, with the cheapest paid "Pro" plan at ~$49.99/mo. Mailtrap's Email Testing sandbox free tier caps at 50 test emails/month (1 user, 1 sandbox, 10 emails per sandbox), cheapest paid "Basic" $17/mo for 500 test emails — note this is a *separate product* from Mailtrap's Sending API, which has a 4,000-email free tier. testmail.app's free tier is 100 emails/month, cheapest "Essential" $9/mo billed annually. For indie devs and OSS CI, these caps and prices are the stated reason for seeking free/self-hosted alternatives.

4. **Flaky email tests are a top-tier CI pain.** The dominant anti-pattern is `sleep(5)` then "check latest unread," which breaks under CI parallelism and shared-inbox collisions. The fix everyone converges on is an explicit wait/poll primitive plus per-test inbox isolation — which tahidromos already ships as `POST /wait` (long-poll until a matching message arrives).

5. **Inbound email testing is painful and under-tooled.** Testing apps that *receive* mail — help desks, email-to-ticket, newsletter reply handling, Postmark/SendGrid/Mailgun inbound webhooks — is hard because the mainstream local tools don't receive. Developers resort to real disposable-inbox SaaS, ngrok tunnels to public webhook endpoints, or hand-rolled Postfix/Dovecot Docker images (docker-mail-devel, deltachat/mail-server-tester). Reply-parsing (stripping quotes/signatures) is a notorious problem — see GitLab's "Revamp reply emails parsing" meta-issue and libraries like Crisp's email-reply-parser and email_reply_trimmer.

6. **Threading/Gmail grouping is fiddly and repeatedly breaks.** Multiple real projects (OpenStreetMap PR #2462, GitLab #60081, Discourse) document Gmail changing conversation-grouping behaviour and In-Reply-To/References/subject interactions breaking threads. A dev tool that lets you generate and assert on correctly-threaded conversations is valuable.

7. **The AI-agent inbox niche is emerging fast and is directly adjacent.** A wave of MCP servers and startups now give LLM agents real inboxes (AgentMail — YC S25, $6M seed March 2026; AgenticMail; Shipmail; Agent Mailbox on AWS Marketplace; several "email MCP server" repos). None of these is a *local, disposable, offline test harness* for agent email workflows — which is exactly what tahidromos could be.

## Details

### Theme A — "It's a sink; I can't reply / test conversations"
- **MailHog #111** (opened 8 Sep 2016, still open): "Users can interact with the application by sending emails to it. You can also just reply directly to an email notification and it will update the thread... I'd like to be able to test this kind of functionality using the UI in Mailhog." This is the canonical statement of tahidromos's value prop, sitting unanswered in the most-starred tool in the category (~16.1k stars, 220 open issues).
- MailHog even started an experimental IMAP server (mailhog/MailHog-IMAP, mailhog/imap) that was never finished — evidence of demand and of how hard "real mailbox + IMAP" is to bolt onto a sink.
- Competitor reality check: a community guide (Pi Stack, May 2026) bluntly notes "smtp4dev, MailDev, and FakeSMTP are SMTP-only servers. They do not provide IMAP or POP3 access. If you need full mail server testing including IMAP, consider deploying a complete mail server like Stalwart or Mailu." tahidromos *is* that complete-mail-server-for-testing, but pre-wired.

### Theme B — MailHog abandonment / Mailpit dominance
- Mailpit's own README: "originally inspired by MailHog which is no longer maintained and hasn't seen active development or security updates for a few years now." Documented MailHog issues include memory problems on large attachments and MIME parser crashes.
- Practical takeaway: outbound-capture is a solved, crowded problem (Mailpit, MailCatcher, Maildev, smtp4dev, Inbucket, plus dozens of SaaS). tahidromos already delegates catch-all/outbound to Mailpit inside its own compose — smart, because it means tahidromos doesn't compete there and inherits a best-in-class UI for free.

> **[CORRECTION]** No longer true. Mailpit (and Roundcube) were removed; tahidromos was rewritten from scratch on the Python standard library — its own SMTP server, its own IMAP4rev1 server, its own SQLite store, its own UI. It is now **one container with no third-party mail software in it**. Outbound mail is captured into a local `captured@` mailbox rather than relayed to Mailpit, so the safety net survived the change but the dependency did not.


### Theme C — SaaS pricing pain (drives self-hosting)
- **Mailosaur:** no permanent free tier; 14-day trial; cheapest "Personal" plan "From $20 per month, billed annually" (1 inbox, unlimited addresses, from 15,000 inbound emails/month at a 500/day limit); team "Core" from $50/mo for 5 seats / 75,000 emails/month (vendor pricing page).
- **MailSlurp:** permanent free tier but "Free sending stays inside MailSlurp" (no external send), 500 inbound/month; cheapest paid "Pro" ~$49.99/mo (vendor pricing page). (Aggregators quoting a "$19 Starter" tier appear stale.)
- **Mailtrap Email Testing/Sandbox:** free tier 50 test emails/month (1 user, 1 sandbox, 10 emails per sandbox); cheapest paid "Basic" $17/mo for 500 test emails, 3 users, 100 forwarded emails/month. (Mailtrap's *sending* product is a separate 4,000-email free tier — do not conflate.)
- **testmail.app:** free tier 100 emails/month; cheapest "Essential" $9/mo billed annually (10,000 emails).
- A representative framing (MoeMail's honest comparison, June 2026): people leave Mailosaur/MailSlurp for one of three reasons — "cost... a need to self-host / own the data, or wanting something open-source."

### Theme D — Flaky tests, sleep(), CI isolation
- MailSlurp's own CI guide: "Most flaky email tests fail for one of four reasons: Multiple test runs send to the same mailbox... The test sleeps for 5 or 10 seconds and assumes the email will arrive in time. The suite asks for the latest unread message instead of the message that belongs to this exact test run." Recommended fixes: "Use one inbox per test or per worker, and replace sleep-based waiting with explicit API waits."
- The broader engineering consensus (e.g. OpenSearch issue #20004 "Forbid the use of Thread.sleep in tests"; OpenStreetMap rails-dev flaky-test thread Jan 2026: "as soon as `sleep` in tests becomes acceptable, they multiply like rabbits") validates that a first-class wait primitive is the right design. tahidromos's `POST /wait` is directly on-target; the missing piece is per-test **isolation/namespacing**.

### Theme E — Inbound email + reply parsing
- Postmark, SendGrid, and Mailgun all offer inbound-parse webhooks; testing them locally is awkward. Django's `django-inbound-email` README states the core problem plainly: "it's really very hard to test inbound emails without having real data, and that requires a public endpoint that you can use to hook up your preferred email provider's webhooks."
- Reply/quote/signature stripping is a well-known swamp: GitLab's "Revamp reply emails parsing" meta-issue catalogues HTML-email, inline-reply, signature and locale problems; reference implementations include github/email_reply_parser, discourse/email_reply_trimmer, Crisp's email-reply-parser ("used at Crisp everyday with around 1 million inbound emails"), and Python's mail-parser-reply.
- Note the Postmark "Inbox Innovators" challenge (2025) produced dozens of email-first apps (email-to-task, email CMS, AI email assistants) — a live pool of developers who need exactly tahidromos's inbound/threading test capability.

### Theme F — Threading / Gmail grouping breakage
- OpenStreetMap PR #2462 (Dec 2019–Jan 2020) documents that after adding In-Reply-To/References, "Google Mail now starts merging emails that share the same subject, although the References header differs," and the team experimenting with `X-Entity-Ref-ID` to control Gmail grouping.
- GitLab #60081: "Previously, Gmail groups emails by subject. It seems that they have stopped doing this and now depend on In-Reply-To... This causes these emails to be threaded separately." Discourse meta has a long-running "incorrectly threaded" bug. A tool that generates known-good threaded conversations and lets you assert on References/In-Reply-To is genuinely useful.

### Theme G — AI-agent inboxes (adjacent, fast-growing)
- MCP servers giving agents inboxes: **AgentMail MCP** (dedicated inboxes for agents to send/receive/query), **Shipmail MCP**, **UseJunior/email-agent-mcp**, **gaddobenedetti/mcp_email** (POP3+SMTP), **darinkishore/Inbox-MCP** (via Nylas), **AgenticMail** (self-hosted Stalwart + 75+ REST endpoints). GitHub topic `agent-email` aggregates more.
- Startups: **AgentMail** (agentmail.to, YC Summer 2025) — founded 2025 by Haakam Aujla, Michael Kim, and Adi Singh; per TechCrunch (March 10 2026, "AgentMail raises $6M to build an email service for AI agents") it "raised $6 million in a seed funding round led by General Catalyst, with participation from Y Combinator, Phosphor Capital, and angel investors Paul Graham, Dharmesh Shah… Karim Atiyeh," and has attracted "tens of thousands of human users, and hundreds of thousands" of agent accounts. AgentMail ships "a native MCP server so agents (and tools that speak MCP) can manage email directly" and a free tier of 3 inboxes / 3,000 emails per month with no card. Also **Agent Mailbox** (AWS Marketplace, by Use Neuron — "production-ready email infrastructure for AI agents") and **Shipmail**.
- **Gap:** all of these are *production* inbox infrastructure or cloud services. None is a **local, offline, disposable test harness** where you can spin up agent mailboxes + deterministic auto-responders (echo bots!) in CI. tahidromos's echo bots (echo/mirror/ack/counter, depth-capped volleys) are unusually well-suited to simulating a counterparty for agent-email tests.

### Competitive landscape

| Tool | Real mailboxes | IMAP | Reply / receive | Threading view | Inbound webhook sim | Per-test isolation | API assertions | Wait/long-poll | Auto-responder | Maint. status |
|---|---|---|---|---|---|---|---|---|---|---|
| **tahidromos** *(as researched)* | ✅ (maddy, 10 seeded) | ✅ (1143/1993) | ✅ core feature | ✅ `/threads` by References | ⚠️ not yet | ⚠️ partial (accounts) | ✅ REST harness | ✅ `/wait` | ✅ echo bots | new (2026) |
| **tahidromos** *(now)* | ✅ own server, no maddy | ✅ own IMAP4rev1 | ✅ core feature | ✅ `/threads` + UI grouping | ✅ **Postmark/SendGrid/Mailgun shapes** | ✅ **`POST /inboxes` per test + run teardown** | ✅ REST + **pytest fixtures** | ✅ `/wait` with code/link filters | ✅ echo bots | ✅ |
| **Mailpit** | ❌ (sink) | ❌ | ❌ | partial (Msg-ID view) | ❌ | ❌ | ✅ REST API | ❌ | ❌ | ✅ very active |
| **MailHog** | ❌ | ❌ (abandoned exp.) | ❌ (#111 open since 2016) | ❌ | ❌ | ❌ | ✅ JSON API | ❌ | ❌ | ❌ dead (2020) |
| **MailCatcher** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | limited | ❌ | ❌ | low |
| **smtp4dev** | ❌ | ✅ read-only | ❌ | ❌ | ❌ | ❌ | ✅ OpenAPI | ❌ | ❌ | ✅ active |
| **Maildev** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ REST | ❌ | ❌ | low/minimal |
| **Inbucket** | catch-all pseudo | ❌ (POP3) | ❌ | ❌ | ❌ | pseudo (any addr) | ✅ REST | ❌ | ❌ | moderate |
| **Greenmail** | ✅ (embedded) | ✅ | ✅ (via IMAP) | ❌ | ❌ | ✅ (per-JUnit) | ✅ (Java API) | ✅ (non-poll wait) | ❌ | ✅ active (Java-only) |
| **Mailtrap** (SaaS) | virtual inboxes | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | commercial |
| **MailSlurp** (SaaS) | ✅ real | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | commercial |
| **Mailosaur** (SaaS) | ✅ real | ✅ (POP3/IMAP) | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | commercial |
| **Ethereal** | ✅ (throwaway) | ✅ | ❌ (never delivered) | ❌ | ❌ | ✅ | limited | ❌ | ❌ | Nodemailer |

Key reading of the table: tahidromos's closest functional peer is **Greenmail** (real mailboxes, IMAP, non-polling wait) but Greenmail is Java-embedded and has no reply/threading/auto-responder/web-reply story; and the **SaaS trio** (MailSlurp/Mailosaur) which have real inboxes but cost money, need the internet, and offer no local threading/echo-bot simulation. tahidromos is the only *self-hosted, language-agnostic, one-command* option that does real mailboxes + reply + threading + auto-responders together.

## Recommendations

### Tier 1 — Quick wins (days, high leverage for adoption)
1. ~~**README/positioning rewrite around one line: "the development mail server that can reply."**~~ **DONE.** The README opens with "A development mail server that can hold a conversation" and the second paragraph is the sink contrast. Screenshots of the inbox, the reply composer, thread grouping, device preview and the capture mailbox are in it.
2. ~~**pytest plugin / fixtures**~~ **DONE.** `clients/python/` ships `tahidromos-client`, registered via the `pytest11` entry point so `pip install` is the whole setup — no conftest wiring. Fixtures: `inbox` (a mailbox per test, deleted after), `inbox_factory`, `mail`, `echo_bot`, `captured_inbox`. It **skips rather than errors** when the server is down, and prints the `docker run` line. Options `--tahidromos-url` / `--tahidromos-keep`. Eleven tests in `tests/test_client_fixtures.py` exercise the fixtures themselves.
3. ~~**GitHub Actions service-container snippet + a documented healthcheck**~~ **DONE.** The README has an "In CI" section with a copy-paste `services:` block using `--health-cmd` against `/health`, so the service is healthy before the first test runs. The image already carries a `HEALTHCHECK`.
4. ~~**Per-test namespacing helper.**~~ **DONE.** `POST /inboxes {prefix, run_id}` mints `signup-a1b2c3@…`; `DELETE /inboxes?run_id=…` tears down a whole CI run in one call. The `inbox` fixture names the mailbox after the test, so a leftover inbox tells you where it came from. Both causes named in the MailSlurp guide — shared mailboxes and `sleep()` — are now structurally addressed.

### Tier 2 — High-impact (weeks)
5. ~~**Inbound-webhook simulator.**~~ **DONE.** `INBOUND_WEBHOOK_URL` turns it on; `INBOUND_WEBHOOK_FORMAT` picks `postmark` · `sendgrid` · `mailgun` · `raw`. Implemented as a delivery listener in the router, so it fires for **every** delivery path — SMTP, submission, the API, the bots. Postmark payloads include `MailboxHash` (plus-address tag) and `StrippedTextReply`; Mailgun gets `stripped-text`. Retries with backoff; `GET /webhook` reports sent/failed/last error. Verified end-to-end against a real receiver in all four shapes.
6. **Testcontainers module** — **[OPEN]**. Agreed, and cheap now that the image is a single container with a `HEALTHCHECK` and a `/health` endpoint. Not built.
7. ~~**Snapshot/assertion helpers** … plus built-in quoted-reply and signature stripping~~ **MOSTLY DONE.** `tahidromos/extract.py` strips quoted replies (English, German, French, Spanish, Greek attribution lines, Outlook header blocks, RFC 3676 signature separators) and is exposed both as `POST /parse` and as `stripped_text` on every message. `POST /scenarios` with a `seed` gives byte-identical fixtures, which is the snapshot half. **[OPEN]:** a dedicated normalise-and-diff assertion helper.
8. ~~**JS/TS client + Playwright/Cypress helpers**~~ **DONE.** `clients/node/` ships `@pamfilico/tahidromos` with no dependencies (Node 18 `fetch`). Playwright fixtures mirror the pytest ones — `inbox`, `inboxFactory`, `mail`, `echoBot` — plus `signInWithMagicLink` and `enterOneTimeCode` for the two flows nearly every app shares. `waitForCode` and `waitForLink` are one call each. Cypress and Vitest are covered too; the Playwright import is a separate entry point. TypeScript declarations included. Ten tests, run in CI.

### Tier 3 — Longer-term / strategic differentiators
9. ~~**MCP server ("tahidromos-mcp"): a local, disposable inbox for AI agents.**~~ **DONE.** `clients/mcp/` ships `tahidromos-mcp`: sixteen tools over stdio — `create_inbox`, `wait_for_email` (blocks server-side, so an agent waits instead of burning tokens polling), `send_email`, `reply_to_email`, `forward_email`, `echo_bot_address`, `send_test_scenario`, `check_spam_score`, `cleanup`. The research's read of the gap was right: every existing agent-email MCP is production or cloud infrastructure, and none of them is a **local, offline, disposable test harness**. This one needs no account, no network and no money, and the echo bots give an agent a counterparty to rehearse a multi-turn exchange against. Ten tests drive it through `call_tool`, plus a stdio round trip.
10. **Framework adapters** for Rails ActionMailer, Laravel, Django/Nest — one-line config + assertion helpers. Effort: medium, incremental per-framework.
11. ~~**Optional single-binary / lighter mode.**~~ **DONE, and it is not optional — it is the only mode.** There is one container, one process, four Python dependencies, and no Mailpit or Roundcube to make optional. `docker run -p 1025:25 -p 1143:143 -p 8080:8080 ghcr.io/pamfilico/tahidromos` is the whole install.
12. ~~**Deterministic/seeded fixtures**~~ **DONE.** `POST /conversation` takes a `seed`. `POST /scenarios` ships ten: `bounce` (a genuine RFC 3464 multipart/report, not a text imitation), `newsletter` (List-Unsubscribe + One-Click), `html_only`, `otp`, `deep_reply`, `attachment`, `unicode`, `auto_reply`, `signed` (DKIM/SPF/DMARC headers), `large`. Seeded builds pin the MIME boundaries and `Date` too, so the bytes really are identical run to run.

### Probably skip (don't duplicate saturated/solved areas)
- ~~Building a fancier outbound-capture web UI — Mailpit already wins and you embed it.~~ **[CORRECTION]** Mailpit is no longer embedded, so a UI had to be built. It is not competing on capture: the sidebar is organised around *mailboxes and apps with unread badges*, which is a different job from a single catch-all list.
- Full deliverability/spam-score/HTML-client-rendering — **PARTLY OVERRIDDEN, on request.** Litmus-style rendering across real clients is still out of scope. What was built instead is cheap and useful: a **device preview** (13 presets, phone/tablet/desktop plus the Outlook 600 / Gmail 640 / Apple Mail 700 widths) with client-side PNG capture and `.eml` / HTML download; and a **spam scorer** — built-in explainable heuristics by default, with optional Rspamd (`RSPAMD_URL`) for a production-grade verdict. Rspamd was chosen over SpamAssassin on maintenance: it had commits the same day this was written.
- ~~Production hardening (SPF/DMARC/real TLS)~~ **AGREED, SKIPPED.** The README keeps an explicit "Not for production" section listing exactly which choices make it unsafe and why each was deliberate.

### Benchmarks that would change the priorities
- If early adopters are **QA/e2e** heavy → prioritize #8 (Playwright/Cypress, OTP helpers) and #6 (Testcontainers) first.
- If they're **backend/inbound-app** devs (help desks, email-to-ticket) → prioritize #5 (inbound webhook sim) and #7 (reply parsing).
- If **AI-agent** interest shows up in stars/issues → jump #9 (MCP) up to Tier 1.

## Distribution & Discoverability

### Reddit (respect the 90/10 rule and per-sub rules; participate before posting)
- **r/selfhosted** — most receptive to self-hosted Docker projects; post when genuinely relevant, ideally answering someone's question. High fit.
- **r/webdev** — personal projects must go in the weekly **"Showoff Saturday"** megathread, not standalone posts. Follow that rule or get removed.
- **r/SideProject, r/IndieHackers, r/SaaS** — explicitly permit "I built this" posts (r/SaaS has Saturday self-promo threads); good for the launch story.
- **r/QualityAssurance, r/softwaretesting** — the flaky-email-test and OTP-testing pain lives here; lead with the `/wait` + isolation angle.
- **r/docker, r/homelab** — for the compose/one-command angle.
- Language subs (**r/django, r/rails, r/laravel, r/node, r/golang, r/PHP, r/dotnet, r/Nestjs_framework, r/nextjs**) — most allow self-promo only in designated threads or when framed as a helpful answer; ship the framework adapter first, then post in that community.
- **r/programming** — removes most commercial/self-promo content regardless of framing; skip, or only via a genuinely technical write-up that earns organic traction.
- Reddit mechanics to respect: 90/10 participation ratio ("it's fine to be a redditor with a website, it's not fine to be a website with a Reddit account"); many subs enforce account-age/karma minimums via AutoModerator; wait 7–14 days between promo posts; use text posts summarizing value with the link at the bottom.

### Hacker News
- **Show HN**: title format "Show HN: tahidromos – a dev mail server that can hold a conversation." Link to the repo, first comment explains the MailHog-#111 gap and the reply/threading/echo-bot design. Post ~8–10am ET on a weekday; be present to answer. The "sinks can't reply" framing is exactly the kind of crisp technical insight HN rewards.

### Awesome-lists (submit PRs)
- **awesome-selfhosted** (Communication → Email, or Software Development), **awesome-docker**, **awesome-testing / awesome-test-automation**, **awesome-python** (testing), and framework awesome-lists (awesome-django, awesome-laravel, etc.). Each is a concrete PR opportunity.

### Direct issue/thread interventions (highest-intent audiences)
- Comment on **MailHog #111** (reply-to-emails) and the MailHog-IMAP repo — people literally subscribed to that thread want tahidromos.
- **testcontainers-go #3616** (Mailpit module) and the Mailpit discussions — mention the real-mailbox use case; consider contributing a tahidromos Testcontainers module.
- **django-helpdesk #224** (Message-ID/In-Reply-To/References handling) and **django-inbound-email** — teams testing inbound/threading are the perfect users.
- Answer Stack Overflow / DEV questions about "test IMAP locally," "test inbound email webhook locally," "reply threading test." The Postmark "Inbox Innovators" alumni (email-first app builders) are a warm audience.

### Other channels
- **Dev.to / Hashnode** technical post: "MailHog is dead and every alternative is a sink — so I built one that replies." **Lobsters** (needs invite; strong for infra/testing). **Product Hunt** for a broader launch once the README and a demo GIF are polished.
- A 30-second **demo GIF / asciinema** in the README (send → auto-reply arrives threaded) will do more for conversion than any prose.

## What was added that the research did not ask for

Two things came from the user during the work rather than from the research,
and both turned out to fit the same gap:

* **Email templates** — eight production-shaped templates (`welcome`, `otp`,
  `password_reset`, `receipt`, `digest`, `alert`, `invite`, `verify_email`)
  with table layouts, inline styles, preheaders and a text alternative each.
  Six are handwritten HTML; two are authored in **React Email** and exported
  by `templates/react-email/`, which demonstrates the workflow without putting
  Node anywhere near the runtime. This matters more than it first looks: it
  gives the device preview and the spam scorer something real to work on, and
  it means the false-positive test ("no template we ship is ever flagged") has
  teeth.

* **Device preview and screenshots** — see the note on the skip list above.

## Distribution — not started

Everything in the section below is **[OPEN]**. The repository is published at
`github.com/pamfilico/tahidromos` with CI green and the image on GHCR, and it
is listed in the Open source section of pamfili.co. No Show HN, no subreddit
posts, no awesome-list PRs, no comments on MailHog #111. That is deliberate
sequencing — the README, the two client libraries and the demo screenshots are
the things those posts would link to, and they only just landed.

Of what remains on the build side, two minor items are open: a
**Testcontainers module** (#6) and a **normalise-and-diff snapshot helper**
(#7). Everything else across all three tiers is shipped.

The one piece of homework worth doing before any of it: **verify the pricing
figures again**. The Caveats section is right that they move, and quoting a
stale number in a Show HN comment is the kind of thing that derails a thread.

## Caveats
- Most primary evidence of the "sinks can't reply / can't test inbound" pain comes from GitHub issues, project READMEs, vendor CI guides, and framework mailing lists rather than verbatim Reddit threads; direct Reddit search surfaced mostly tutorials and tool-recommendation posts rather than deep complaint threads, so some Reddit-specific pain is inferred from adjacent sources. Treat the subreddit list as *where to engage*, validated against each sub's current rules before posting.
- SaaS pricing changes frequently; figures are vendor-pricing-page values as of Sept 6 2026 and third-party aggregators frequently disagree (often stale). Re-verify before quoting publicly.
- AI-agent-email funding/traction stats (AgentMail's $6M seed, March 2026; user counts) come from TechCrunch and other tech press; confirm against a primary AgentMail announcement before citing in public materials.
- GitHub star counts and maintenance statuses in the table are approximate and drawn from mixed-date sources; verify current numbers before publishing comparisons.
- tahidromos's feature checkmarks reflect the project description provided, not independent testing; "inbound webhook sim" and "per-test isolation" are marked partial/absent based on that description.