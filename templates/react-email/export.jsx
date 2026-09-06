/**
 * Render every email in emails/ to plain HTML and write it where the mail
 * server can serve it, then update the template manifest.
 *
 *   npm run export        write the HTML and update manifest.json
 *   npm run check         fail if the committed HTML is stale
 *
 * The components keep `{{ handlebars }}` placeholders in their text, so the
 * exported HTML is still a server-side template — React Email is the design
 * tool, the mail server does the substitution.
 */
import * as React from "react";
import { render } from "@react-email/render";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const emailsDir = join(here, "emails");
const outDir = resolve(here, "../../tahidromos/templates");
const manifestPath = join(outDir, "manifest.json");
const checkOnly = process.argv.includes("--check");

/** Everything the manifest needs that cannot be inferred from the component. */
const META = {
  invite: {
    name: "invite",
    summary: "Team invitation with a facts table (authored in React Email)",
    subject: "{{ inviter }} added you to {{ team }}",
    from: "team@{{ domain }}",
    defaults: {
      product: "Acme", company: "Acme Inc.", team: "Platform",
      role: "Editor", inviter: "Alice", inviter_email: "alice@{{ domain }}",
      expires_days: 7,
      accept_url: "https://acme.test/invite/accept?token=c81f2a",
      decline_url: "https://acme.test/invite/decline?token=c81f2a",
    },
  },
  verify_email: {
    name: "verify_email",
    summary: "Address confirmation link (authored in React Email)",
    subject: "Confirm your address for {{ product }}",
    from: "noreply@{{ domain }}",
    defaults: {
      product: "Acme", company: "Acme Inc.",
      email: "alice@{{ domain }}", expires_hours: 24,
      verify_url: "https://acme.test/verify?token=7e1b9d4f2a",
    },
  },
};

const slug = (file) =>
  file.replace(/\.[jt]sx?$/, "")
      .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
      .toLowerCase();

const files = (await readdir(emailsDir)).filter((f) => /\.[jt]sx?$/.test(f)).sort();
if (files.length === 0) {
  console.error("no email components in emails/");
  process.exit(1);
}

const rendered = [];
for (const file of files) {
  const key = slug(file);
  const meta = META[key];
  if (!meta) {
    console.error(`  ${file}: no entry in META, skipping`);
    continue;
  }
  const module = await import(join(emailsDir, file));
  const Component = module.default;

  const html = await render(<Component />, { pretty: true });
  const text = await render(<Component />, { plainText: true });

  rendered.push({ key, meta, html, text });
}

// ---- write the HTML and text, and fold the entries into the manifest
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
const handwritten = manifest.templates.filter((t) => t.source !== "react-email");

let stale = false;
for (const { key, meta, html, text } of rendered) {
  const htmlPath = join(outDir, `${key}.html`);
  const textPath = join(outDir, `${key}.txt`);

  const existing = await readFile(htmlPath, "utf8").catch(() => null);
  if (existing !== html) {
    stale = true;
    if (!checkOnly) {
      await writeFile(htmlPath, html, "utf8");
      await writeFile(textPath, text, "utf8");
    }
  }
  console.log(`  ${key.padEnd(14)} ${String(html.length).padStart(6)}b  ${meta.summary}`);
}

const merged = {
  templates: [
    ...handwritten,
    ...rendered.map(({ key, meta }) => ({
      ...meta,
      html: `${key}.html`,
      text: `${key}.txt`,
      source: "react-email",
    })),
  ],
};

const serialised = JSON.stringify(merged, null, 2) + "\n";
if ((await readFile(manifestPath, "utf8")) !== serialised) {
  stale = true;
  if (!checkOnly) await writeFile(manifestPath, serialised, "utf8");
}

if (checkOnly && stale) {
  console.error("\nExported HTML is out of date. Run: npm run export");
  process.exit(1);
}
console.log(`\n${checkOnly ? "up to date" : "exported"}: ${rendered.length} template(s) -> ${outDir}`);
