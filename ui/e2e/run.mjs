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

// Faxed referrals: read inside the boundary; the degraded one sends its unsure fields to a person, who corrects
// them against the page before the case exists.
const faxes = JSON.parse(readFileSync(new URL("../../data/faxes/faxes.json", import.meta.url), "utf8"));
await page.getByRole("region", { name: "Arrived by fax" }).waitFor({ timeout: 60_000 });
await page.locator('a[href="/intake/FAX-0003"]').click();
await page.getByRole("heading", { name: "Check the fax" }).waitFor();
await page.locator(".crop").first().waitFor();
await shot(page, "02c-fax-check");
await axe(page, "02c-fax-check");
// Creating it before anything is checked: the error summary takes focus and links each problem to its field.
await page.getByRole("button", { name: "Create the case" }).click();
await page.getByRole("alert").filter({ hasText: "There is a problem" }).waitFor();
if (!(await page.evaluate(() => document.activeElement?.classList.contains("error-summary")))) {
  report.failures.push("fax: the error summary did not take focus");
}
await page.getByRole("alert").getByRole("link", { name: /^Enter the member ID/ }).click();
if (!(await page.evaluate(() => document.activeElement?.id === "fax-member_id"))) {
  report.failures.push("fax: the error link did not move focus to its field");
}
await shot(page, "02d-fax-errors");
await axe(page, "02d-fax-errors");
// Text where the fax has text, lists for the plan, the clinician and the service, three boxes for the date.
const want = faxes["FAX-0003"].expected;
for (const [key, label] of Object.entries({ referral: "Referral number", patient_name: "Patient name",
  member_id: "Member ID", diagnosis: "Diagnosis" })) {
  await page.getByLabel(label, { exact: true }).fill(want[key]);
}
const [month, day, year] = want.birth_date.split("/");
for (const [label, value] of [["Month", month], ["Day", day], ["Year", year]]) {
  await page.getByLabel(label, { exact: true }).fill(value);
}
await page.getByLabel("Health plan", { exact: true }).selectOption(want.payer);
await page.getByLabel("Ordering clinician", { exact: true }).selectOption(want.clinician);
await page.getByLabel("Requested service", { exact: true }).selectOption(want.service_code);
// What the form started on still needs a person's tick, field by field.
for (const box of await page.getByRole("checkbox", { name: /^Matches the fax/ }).all()) {
  if (!(await box.isChecked())) await box.check();
}
await page.getByText(/^(\d+) of \1 checked$/).first().waitFor();
// The patient in the chart: a date of birth that disagrees with the record shows both, and the record's can be taken.
const check = page.getByRole("region", { name: "The patient in the chart" });
await check.getByText(/^This is Shelba Gulgowski/).first().waitFor();
await page.getByLabel("Year", { exact: true }).fill("1942");
await check.getByText("The date of birth differs.").first().waitFor();
await shot(page, "02e-fax-chart");
await axe(page, "02e-fax-chart");
await check.getByRole("button", { name: /^Use the chart’s value/ }).click();
await check.getByRole("button", { name: "Yes, this is the patient" }).click();
await check.getByText("Confirmed as the patient").first().waitFor();
await page.getByRole("button", { name: "Create the case" }).click();
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
if (!page.url().endsWith("/cases/2026-1029")) report.failures.push(`fax: expected case 2026-1029, got ${page.url()}`);
await page.goto(base + "/");

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

// How the draft was written: exactly what the model saw, with the placeholders where identifiers were.
await page.getByRole("button", { name: "Back to the draft" }).click();
await page.getByRole("button", { name: "See what was sent" }).click();
await page.getByRole("heading", { name: "What was sent to the model" }).waitFor();
if (await page.locator(".paper--sent").getByText(/Kristina|Mante/).count()) report.failures.push("sent text holds a name");
await shot(page, "04b-case-sent-to-model");
await axe(page, "04b-case-sent-to-model");
await page.getByRole("button", { name: "Back to the draft" }).click();

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
await doc.getByLabel("Yes, I confirm this").waitFor();  // the question itself, not the header's Text size group
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

// A second case, sent now so the payer's answer is back by the end of the walk. This payer denies it.
await page.goto(base + "/cases/2026-1038");
await page.getByRole("button", { name: "Approve and send" }).click();
await page.getByRole("status").filter({ hasText: "Approved." }).waitFor();

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
await page.goto(base + "/outcomes");
await page.getByRole("heading", { name: "Where patient information went" }).waitFor();
await shot(page, "12b-outcomes");
await axe(page, "12b-outcomes");

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

// The denial names its reason; Caduceus has drafted an appeal from the policy and the record.
await page.goto(base + "/cases/2026-1038");
await page.getByRole("heading", { name: /denied it$/ }).waitFor({ timeout: 60_000 });
await shot(page, "18-case-denied");
await axe(page, "18-case-denied");
await page.getByRole("button", { name: /^Send the appeal to / }).click();
await page.getByRole("status").filter({ hasText: "Sending the appeal" }).waitFor();
await page.getByRole("status").filter({ hasText: /^Approved by / }).waitFor({ timeout: 60_000 });
await axe(page, "19-appeal-approved");
await page.goto(caseUrl);

// The case history, then the tour's last step: the auditor checks that none of it was changed.
await page.getByRole("button", { name: "Show the history" }).click();
await page.getByText("Asked Dr. Ana Reyes for the note the payer wants.").waitFor();
await shot(page, "20-case-history");
await axe(page, "20-case-history");
await tour(page).getByRole("button", { name: "Switch to Sam Whitlock" }).click();
await page.getByRole("status").filter({ hasText: /^Read only\./ }).waitFor();
await page.getByRole("button", { name: "Show the history" }).click();
await page.getByRole("button", { name: "Check that nothing was changed" }).click();
await page.getByText(/^Intact\. All [\d,]+ events/).waitFor();
await axe(page, "21-auditor-verifies");

await browser.close();
console.log(JSON.stringify(report, null, 2));
process.exit(report.failures.length ? 1 : 0);
