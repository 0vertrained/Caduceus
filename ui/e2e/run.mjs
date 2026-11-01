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
  // A color mid-transition is not the page's color: let running transitions finish first (not the busy spinner,
  // which never does).
  await page.evaluate(() => Promise.all(document.getAnimations()
    .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
    .map((a) => a.finished.catch(() => {}))));
  await page.evaluate(axeSource);
  const result = await page.evaluate(async () =>
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
// Not on the fax: marked on the field, the request to the clinic is written, and the fax waits for their answer.
await page.getByRole("button", { name: "Not on the fax: ask the clinic for the member ID" }).click();
await page.getByRole("region", { name: "Ask Mesa Verde Orthopedics, Santa Fe" }).waitFor();
await page.getByRole("button", { name: "Ask the clinic for it" }).click();
await page.getByRole("heading", { name: "Waiting on Mesa Verde Orthopedics, Santa Fe" }).waitFor();
if (await page.locator(".asked .paper").getByText(/Shelba|Gulgowski/).count()) report.failures.push("clinic request names the patient");
await shot(page, "02e-fax-asked");
await axe(page, "02e-fax-asked");
// The whole page, larger.
await page.getByRole("button", { name: "View the whole fax" }).click();
await page.getByRole("button", { name: "Zoom in" }).click();
await page.getByRole("region", { name: "The fax page, at 150%" }).waitFor();
await page.getByRole("region", { name: "The fax page, at 150%" }).locator("img").waitFor();
await shot(page, "02f-fax-whole");
await axe(page, "02f-fax-whole");
await page.getByRole("button", { name: "Hide the whole fax" }).click();
await page.getByRole("link", { name: "Back to your queue" }).click();
await page.getByRole("region", { name: "Arrived by fax" }).getByText("Waiting on Mesa Verde Orthopedics, Santa Fe").first().waitFor();
await shot(page, "02g-queue-fax-waiting");
await axe(page, "02g-queue-fax-waiting");
await page.locator('a[href="/intake/FAX-0003"]').click();
await page.getByRole("button", { name: "The clinic sent it" }).click();
await page.getByRole("button", { name: "Create the case" }).waitFor();
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
await shot(page, "02h-fax-chart");
await axe(page, "02h-fax-chart");
await check.getByRole("button", { name: /^Use the chart’s value/ }).click();
await check.getByRole("button", { name: "Yes, this is the patient" }).click();
await check.getByText("Confirmed as the patient").first().waitFor();
await page.getByRole("button", { name: "Create the case" }).click();
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
if (!page.url().endsWith("/cases/2026-1029")) report.failures.push(`fax: expected case 2026-1029, got ${page.url()}`);
// The case says it came by fax, who checked it and what they corrected; the cover sheet is page 1 of its packet.
await page.getByText(/^Came by fax from Mesa Verde Orthopedics, Santa Fe\. Dolores Montoya checked it/).waitFor();
await page.getByRole("button", { name: /^Fax cover sheet/ }).click();
await page.getByRole("heading", { name: "Fax cover sheet" }).waitFor();
await page.getByRole("button", { name: "Show the faxed page" }).click();
await page.locator("#fax-page-image img").waitFor();
await shot(page, "02i-case-from-fax");
await axe(page, "02i-case-from-fax");
await page.goto(base + "/");

// The showcase cases. An expedited request is tagged in the queue.
await page.locator("li.row").filter({ hasText: "Raymond Vigil" }).getByText("Expedited", { exact: true }).waitFor();
// Therapy only an outside summary in the packet records: the record cannot answer, so the case says where to look,
// and the page opens on the passage, which is not evidence.
await page.goto(base + "/cases/2026-1052");
await page.getByText("The packet may answer this").waitFor();
await shot(page, "03b-case-where-to-look");
await axe(page, "03b-case-where-to-look");
await page.getByRole("button", { name: /^Show the therapy notes, page\s\d+$/ }).first().click();
await page.getByText(/is the passage that may answer/).waitFor();
await page.locator("mark").filter({ hasText: "10 weeks of physical therapy" }).waitFor();
await view(page, "03c-case-lead-page");  // the passage scrolled into view on a long page
await axe(page, "03c-case-lead-page");
// Expediting needs the clinical reason: refused next to the field, with focus on it; then marked, and changed back.
await page.getByRole("button", { name: "Ask for an expedited decision…", exact: true }).click();
await page.getByRole("button", { name: "Ask for an expedited decision", exact: true }).click();
await page.locator("#urgency-error").waitFor();
if (!(await page.evaluate(() => document.activeElement?.id === "urgency-reason"))) {
  report.failures.push("urgency: the refusal did not move focus to the reason");
}
await shot(page, "03d-case-urgency-refused");
await axe(page, "03d-case-urgency-refused");
await page.getByLabel("Why can’t this wait the standard time?").fill("New weakness of the right foot over two weeks.");
await page.getByRole("button", { name: "Ask for an expedited decision", exact: true }).click();
await page.getByRole("status").filter({ hasText: "Marked expedited." }).waitFor();
await page.getByRole("button", { name: "Change to standard…", exact: true }).click();
await page.getByRole("button", { name: "Change to standard", exact: true }).click();
await page.getByRole("status").filter({ hasText: "Changed to a standard request." }).waitFor();
// Sent to the ordering clinician, whose questions carry the same passages (signed on the phone, below).
await page.getByRole("button", { name: "Send to Dr. Ana Reyes to attest" }).click();
await page.getByText(/This case comes back to you/).waitFor();
// A request for the right knee when the chart documents the left: stopped, with the reason.
await page.goto(base + "/cases/2026-1051");
await page.getByText(/left knee is charted, but the request is for the right side/).waitFor();
await shot(page, "03e-case-other-knee");
await axe(page, "03e-case-other-knee");
// Expedited by the clinic, with its reason.
await page.goto(base + "/cases/2026-1053");
await page.getByText("Expedited.", { exact: true }).waitFor();
await shot(page, "03f-case-expedited");
await axe(page, "03f-case-expedited");
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
// A nudge: how long it has waited, and a reminder (one every 10 minutes at most) that shows on the clinician's list.
await page.getByRole("button", { name: "Remind Dr. Ana Reyes" }).click();
await page.getByRole("status").filter({ hasText: /Reminded at .*You can send another reminder at/ }).waitFor();
await shot(page, "05-case-sent-for-attestation");
await axe(page, "05-case-sent-for-attestation");
const caseUrl = page.url();

// The clinician, on a phone: another device, at an address of its own, so its requests count toward the demo's
// limits apart from the desk's, as they would.
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  bypassCSP: true, extraHTTPHeaders: { "x-nf-client-connection-ip": "198.51.100.23" } });
await share(phone);
const doc = await phone.newPage();
await signIn(doc, "Dr. Ana Reyes");
await doc.getByText(/^Dolores Montoya reminded you at/).waitFor();
await shot(doc, "06a-attest-list");
await axe(doc, "06a-attest-list");
await doc.getByRole("link").filter({ hasText: /, \d+$/ }).first().click();
await doc.getByLabel("Yes, I confirm this").waitFor();  // the question itself, not the header's Text size group
await shot(doc, "06-attest-phone");
await axe(doc, "06-attest-phone");
await doc.getByLabel("Yes, I confirm this").check();
await doc.getByRole("button", { name: "Sign and return" }).click();
await doc.getByText("Signed. Thank you.").waitFor();
await shot(doc, "07-attest-signed");
await axe(doc, "07-attest-signed");
await doc.goto(base + "/attest/2026-1052");
await doc.getByText("Where the referral packet may answer this").waitFor();
await shot(doc, "07b-attest-where-to-look");
await axe(doc, "07b-attest-where-to-look");

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

// A case returned to the clinic, then resubmitted: Caduceus prepares it again and it is back in review.
await page.goto(base + "/cases/2026-1031");
await page.getByRole("button", { name: "Return for correction" }).click();
await page.getByLabel(/^What needs correcting/).fill("The sleep study report is missing its date.");
await page.getByRole("button", { name: /^Return to / }).click();
await page.getByRole("button", { name: "The clinic resubmitted" }).waitFor();
await shot(page, "09b-case-returned");
await axe(page, "09b-case-returned");
await page.getByRole("button", { name: "The clinic resubmitted" }).click();
await page.getByRole("button", { name: "Return for correction" }).waitFor();

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
await small.goto(base + "/cases/2026-1052");
await small.getByText("The packet may answer this").waitFor();
await shot(small, "11b-case-where-to-look-320px");
await axe(small, "11b-case-where-to-look-320px");
await small.goto(base + "/policies");
await small.getByRole("heading", { name: "Coverage policies" }).waitFor();
await axe(small, "11c-policies-320px");

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

// The policies as anyone on the team reads them: each service's version, its source, its criteria in its words.
await page.goto(base + "/policies");
await page.getByRole("heading", { name: "Total knee arthroplasty" }).waitFor();
await page.getByRole("link", { name: /^LCD L36007/ }).waitFor();
await shot(page, "22-policies");
await axe(page, "22-policies");
// The administrator: how the deployment is set up, and the external model's off switch, then on again.
await page.getByRole("button", { name: "Sign out" }).click();
await signIn(page, "Avery Chen");
await page.getByRole("link", { name: "Administration" }).waitFor();  // only an administrator has the link
await page.goto(base + "/admin");  // not a click: signing in ends by going to the queue, which would race it
await page.getByRole("heading", { name: "Where patient information can go" }).waitFor();
await shot(page, "23-admin");
await axe(page, "23-admin");
await page.getByRole("button", { name: "Turn off the external model" }).click();
await page.getByRole("status").filter({ hasText: "The external model is off." }).waitFor();
await axe(page, "23b-admin-external-off");
await page.getByRole("button", { name: "Turn the external model back on" }).click();
await page.getByRole("status").filter({ hasText: "The external model is on again." }).waitFor();
// Opening Caduceus from an EHR. A fake SMART server plays the EHR (tests/fake_smart.py, started by scripts/e2e.sh)
// and the public launcher's page is answered here, so nothing leaves this machine. This part runs with the site's
// own Content Security Policy enforced, unlike the rest of the walk: the launch needs no change to it, because the
// browser only navigates to the EHR and back and talks to nothing but /api.
const ehrIss = process.env.E2E_SMART_ISS;
if (!ehrIss) report.failures.push("ehr: E2E_SMART_ISS is not set; run the walk through scripts/e2e.sh");
// It comes as a visitor of its own, at the address Netlify would report, in the same copy: the walk as one visitor
// would come close to the demo's 300 API requests per visitor in five minutes, and the last pages would be refused.
const strict = await browser.newContext({ viewport: { width: 1280, height: 900 },
  extraHTTPHeaders: { "x-nf-client-connection-ip": "203.0.113.7" } });
await share(strict);
await strict.addInitScript(() => document.addEventListener("securitypolicyviolation",
  (e) => console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`)));
const asked = [];
await strict.route(/^https?:\/\/(?!127\.0\.0\.1[:/]|localhost[:/])/, async (route) => {
  const url = new URL(route.request().url());
  if (url.host !== "launch.smarthealthit.org") {
    report.failures.push(`ehr: the browser tried to reach ${url.host}`);
    return route.abort();
  }
  // The launcher, answered here: it opens the app's launch URL with its FHIR server and the launch, as it does.
  asked.push(url);
  const opened = `${url.searchParams.get("launch_url")}?${new URLSearchParams({ iss: ehrIss, launch: url.searchParams.get("launch") })}`;
  return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html lang=\"en\"><title>SMART launcher</title>" +
    `<main><h1>SMART App Launcher (stand-in)</h1><a href="${opened.replace(/&/g, "&amp;")}">Launch</a></main></html>` });
});
const ehr = await strict.newPage();
const blocked = [];
ehr.on("console", (m) => { if (m.text().startsWith("CSP violation")) blocked.push(m.text()); });

// The entry on the Help page, before signing in: the launcher, set up for Caduceus.
await ehr.goto(base + "/help#ehr");
const entry = ehr.getByRole("link", { name: "Open the SMART launcher" });
const link = new URL(await entry.getAttribute("href"));
const options = JSON.parse(Buffer.from(link.searchParams.get("launch"), "base64url").toString());
const preset = { host: "launch.smarthealthit.org", launch_url: `${base}/smart/launch`, fhir_version: "r4", launchType: 0,
  simulateEhr: 0, redirect: `${base}/smart/callback`, client: "caduceus-demo", clientType: 0, pkce: 2 };
const linked = { host: link.host, launch_url: link.searchParams.get("launch_url"), fhir_version: link.searchParams.get("fhir_version"),
  launchType: options[0], simulateEhr: options[6], redirect: options[8], client: options[9], clientType: options[14], pkce: options[15] };
if (JSON.stringify(linked) !== JSON.stringify(preset)) report.failures.push(`ehr: launcher link ${JSON.stringify(linked)}`);
await axe(ehr, "22-help-ehr");
await entry.click();
await ehr.getByRole("link", { name: "Launch" }).click();
// Launch, the EHR's authorize page, back with a code: the record the EHR shared, in the visitor's own copy.
await ehr.waitForURL(/\/smart\/record\/ehr-[0-9a-f]{20}/, { timeout: 30_000 });
if (/[?&](code|state)=/.test(ehr.url())) report.failures.push(`ehr: the code stayed in the address ${ehr.url()}`);
await ehr.getByRole("heading", { name: "What the record holds" }).waitFor();
await ehr.getByText("Dolores Montoya", { exact: true }).first().waitFor();  // the demo's PA specialist launched it
const recordUrl = ehr.url();
await shot(ehr, "23-ehr-record");
await axe(ehr, "23-ehr-record");
// Starting with nothing chosen: the summary says what to choose and takes the person there.
await ehr.getByRole("radio", { name: "Total knee replacement" }).check();
await ehr.getByRole("button", { name: "Start the case" }).click();
const problems = ehr.getByRole("alert").filter({ hasText: "There is a problem" });
await problems.getByRole("link", { name: "Choose which knee the request is for" }).waitFor();
await problems.getByRole("link", { name: "Choose the health plan" }).waitFor();
if (!(await ehr.evaluate(() => document.activeElement?.classList.contains("error-summary")))) {
  report.failures.push("ehr: the error summary did not take focus");
}
await shot(ehr, "24-ehr-errors");
await axe(ehr, "24-ehr-errors");
// Before the case opens, how much of the policy the record answers; the choices are in the address. CPAP: the
// record answers only the age, and nothing in it fails a criterion.
await ehr.getByRole("radio", { name: "CPAP device" }).check();
await ehr.getByText(/^The record answers 1 of the 4 criteria\. Dr\.\sPriya Patel will be asked to confirm the other 3\.$/).waitFor();
await ehr.getByLabel("Health plan").selectOption("Medicare");
if (!/service=E0601/.test(ehr.url()) || !/plan=Medicare/.test(ehr.url())) report.failures.push(`ehr: choices not in the address ${ehr.url()}`);
await ehr.getByRole("radio", { name: "CPAP device" }).press("Enter");  // the form submits with Enter
await ehr.getByRole("heading", { name: "Coverage criteria" }).waitFor({ timeout: 40_000 });
await ehr.waitForURL(/\/cases\/EHR-0001$/, { timeout: 5_000 })
  .catch(() => report.failures.push(`ehr: expected case EHR-0001, got ${ehr.url()}`));
await ehr.getByText(/^Opened from the EHR at 127\.0\.0\.1 by a SMART launch\. Dolores Montoya started the case/).waitFor();
await ehr.getByText(/^One of 4 criteria are met\. Dr\.\sPriya Patel needs to confirm 3 items/).waitFor();
await shot(ehr, "25-case-from-ehr");
await axe(ehr, "25-case-from-ehr");
// A sign-in that did not start here, and an EHR Caduceus does not know: plain words, and nowhere to go but back.
await ehr.goto(`${base}/smart/callback?code=made-up&state=${"x".repeat(43)}`);
await ehr.getByRole("alert").filter({ hasText: "This sign-in has already been used, or it did not start here." }).waitFor();
if (ehr.url().includes("code=")) report.failures.push("ehr: a refused code stayed in the address");
await axe(ehr, "26-ehr-callback-refused");
await ehr.goto(`${base}/smart/launch?iss=${encodeURIComponent("https://ehr.invalid/fhir")}&launch=abc`);
await ehr.getByRole("alert").filter({ hasText: "Caduceus is not set up to open from that EHR" }).waitFor();
await axe(ehr, "27-ehr-launch-refused");
// The record again, now with its request listed, at 320 CSS px; then the entry on the queue.
await ehr.setViewportSize({ width: 320, height: 800 });
await ehr.goto(recordUrl);
await ehr.getByRole("heading", { name: "Requests already started" }).waitFor();
await shot(ehr, "28-ehr-record-320px");
await axe(ehr, "28-ehr-record-320px");
await ehr.setViewportSize({ width: 1280, height: 900 });
await ehr.goto(base + "/");
await ehr.getByRole("link", { name: "Open a patient from the SMART launcher" }).waitFor();
if (asked.length !== 1) report.failures.push(`ehr: the launcher page was asked for ${asked.length} times`);
if (blocked.length) report.failures.push(`ehr: Content Security Policy blocked ${blocked.join("; ")}`);
await strict.close();

await browser.close();
console.log(JSON.stringify(report, null, 2));
process.exit(report.failures.length ? 1 : 0);
