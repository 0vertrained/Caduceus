// End-to-end walk through the real screens with axe-core checks on each.
//   node ui/e2e/run.mjs http://127.0.0.1:8000 <screenshot-dir>
// Uses the Playwright library and Chromium installed on the machine (CI installs them first).
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const axeSource = readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");

const base = process.argv[2] ?? "http://127.0.0.1:8000";
const out = process.argv[3] ?? "ui/test-results";
mkdirSync(out, { recursive: true });

const report = { screens: [], failures: [] };
const shot = async (page, name) => page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
// What a person actually sees: the viewport, with the sticky decision bar where it really sits.
const view = async (page, name) => page.screenshot({ path: path.join(out, `${name}-viewport.png`) });

async function axe(page, name) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(async () =>
    // eslint-disable-next-line no-undef
    await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
  const violations = result.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
    targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
  const noScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  report.screens.push({ name, violations, horizontalScroll: !noScroll });
  if (violations.length) report.failures.push(`${name}: ${violations.map((v) => v.id).join(", ")}`);
  if (!noScroll) report.failures.push(`${name}: horizontal scroll`);
}

async function signIn(page, label) {
  await page.goto(base + "/");
  await page.getByRole("main").getByRole("button", { name: new RegExp(label) }).click();
  await page.waitForLoadState("networkidle");
}

// The desk and the phone share one copy of the demo's cases, as a visitor does with the tour's phone link.
const SANDBOX = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
const share = (context) => context.addInitScript((id) => localStorage.setItem("caduceus.sandbox", id), SANDBOX);
const tour = (page) => page.getByRole("region", { name: "Guided tour" });

const browser = await chromium.launch();
const desk = await browser.newContext({ viewport: { width: 1280, height: 900 }, bypassCSP: true });
await share(desk);
const page = await desk.newPage();

await page.goto(base + "/");
await tour(page).getByText("Tour, step 1 of 8").waitFor();
await axe(page, "01-sign-in");
await signIn(page, "Dolores Montoya");
await page.getByRole("heading", { level: 1 }).waitFor();
if (!(await page.getByRole("region", { name: "About this demo" }).isVisible())) report.failures.push("demo banner missing");
await shot(page, "02-queue");
await view(page, "02-queue");
await axe(page, "02-queue");

// Patient search: results appear, and the name never enters the URL (it would reach server and proxy logs).
const firstLabel = await page.getByRole("link", { name: /^Open case for/ }).first().getAttribute("aria-label");
const surname = firstLabel.replace(/^Open case for /, "").split(" ").pop();
await page.getByLabel("Find a patient").fill(surname);
await page.getByLabel("Find a patient").press("Enter");
await page.getByRole("heading", { name: /^Results for/ }).waitFor();
if (page.url().toLowerCase().includes(surname.toLowerCase())) report.failures.push(`search: patient name in URL ${page.url()}`);
await axe(page, "02b-search-results");
await page.getByRole("link", { name: /^Caduceus/ }).click();
await page.waitForFunction(() => document.querySelector("#find-patient")?.value === "", null, { timeout: 3000 })
  .catch(() => report.failures.push("search: box kept the term after leaving results"));

await page.getByRole("link", { name: /^Open case for/ }).first().click();
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
await shot(page, "03-case");
await axe(page, "03-case");

await page.getByRole("button", { name: /^Show the visit note/ }).first().click();
await page.locator("mark").first().waitFor();
await shot(page, "04-case-evidence");
await page.evaluate(() => {
  document.documentElement.style.scrollBehavior = "auto";
  document.querySelector("mark")?.scrollIntoView({ block: "center" });
});
await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
await view(page, "04-case-evidence");
await page.evaluate(() => { document.documentElement.style.scrollBehavior = ""; window.scrollTo(0, 0); });
await axe(page, "04-case-evidence");

await page.getByRole("button", { name: /to attest$/ }).click();
await page.getByText(/This case comes back to you/).waitFor();
await shot(page, "05-case-sent-for-attestation");
const caseUrl = page.url();

// The clinician, on a phone.
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, bypassCSP: true });
await share(phone);
const doc = await phone.newPage();
await signIn(doc, "Dr. Ana Reyes");
await doc.getByRole("link").filter({ hasText: /, \d+$/ }).first().click();
await doc.getByRole("group").last().waitFor();
await shot(doc, "06-attest-phone");
await axe(doc, "06-attest-phone");
await doc.getByLabel("Yes, I confirm this").check();
await doc.getByRole("button", { name: "Sign and return" }).click();
await doc.getByText("Signed. Thank you.").waitFor();
await shot(doc, "07-attest-signed");
await axe(doc, "07-attest-signed");

// Back to the specialist: now approvable.
await page.goto(caseUrl);
await page.getByRole("button", { name: "Approve and send" }).waitFor();
await shot(page, "08-case-ready");
await page.getByRole("button", { name: "Approve and send" }).click();
await page.getByRole("status").filter({ hasText: "Approved." }).waitFor();
await shot(page, "09-case-sending");
await view(page, "09-case-sending");
await axe(page, "09-case-sending");

// Larger text and reflow at 320 CSS px.
await page.goto(base + "/");
await page.getByRole("button", { name: "Larger" }).click();
await shot(page, "10-queue-larger-text");
await axe(page, "10-queue-larger-text");
const narrow = await browser.newContext({ viewport: { width: 320, height: 800 }, bypassCSP: true });
const small = await narrow.newPage();
await signIn(small, "Dolores Montoya");
await small.getByRole("link", { name: /^Open case for/ }).first().click();
await small.getByRole("heading", { name: "Coverage criteria" }).waitFor();
await shot(small, "11-case-320px");
await axe(small, "11-case-320px");

await page.goto(base + "/help");
await axe(page, "12-help");

// The request approved above leaves the outbox after its undo window, and this payer pends it: it wants the
// note behind the attestation. The specialist asks for it, the clinician writes and signs it, the specialist sends
// it, and the payer approves. The case page refreshes itself while something is waiting to be sent.
await page.goto(caseUrl);
await page.getByRole("heading", { name: /asked a question$/ }).waitFor({ timeout: 60_000 });
await shot(page, "13-case-payer-question");
await axe(page, "13-case-payer-question");
await page.getByRole("button", { name: /^Ask Dr\. Ana Reyes for the note$/ }).click();
await page.getByText(/comes back to you when they sign it/).waitFor();

await doc.goto(base + "/");
await doc.getByRole("link").filter({ hasText: /, \d+$/ }).first().click();
await doc.getByLabel("Your note").waitFor();
await shot(doc, "14-note-phone");
await axe(doc, "14-note-phone");
await doc.getByRole("button", { name: "Sign the note and return" }).click();
await doc.getByRole("alert").filter({ hasText: "Write the note" }).waitFor();  // an empty note is never signed
await doc.getByLabel("Your note").fill("Addendum to the visit of October 27. Red-flag symptoms were asked about " +
  "and ruled out: no fever, weight loss, history of cancer, saddle anesthesia, or bowel or bladder change.");
await doc.getByRole("button", { name: "Sign the note and return" }).click();
await doc.getByText("Signed. Thank you.").waitFor();
await axe(doc, "15-note-signed");

await page.goto(caseUrl);
await page.getByRole("button", { name: /^Send the note to / }).waitFor();
await shot(page, "16-case-note-ready");
await axe(page, "16-case-note-ready");
await page.getByRole("button", { name: /^Send the note to / }).click();
await page.getByRole("status").filter({ hasText: "Sending the note" }).waitFor();
await page.getByRole("status").filter({ hasText: /^Approved by / }).waitFor({ timeout: 60_000 });
await tour(page).getByText("Tour complete").waitFor({ timeout: 10_000 });
await shot(page, "17-case-approved");
await axe(page, "17-case-approved");

await browser.close();
console.log(JSON.stringify(report, null, 2));
process.exit(report.failures.length ? 1 : 0);
