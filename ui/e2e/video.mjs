// Records the demo walkthrough as captioned clips, cutting the two 30-second outbox waits.
//   node ui/e2e/video.mjs http://127.0.0.1:8000 <clip-dir>      (scripts/demo_video.sh runs it and joins the clips)
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const base = process.argv[2] ?? "http://127.0.0.1:8000";
const dir = process.argv[3] ?? "ui/test-results/video";
mkdirSync(dir, { recursive: true });
const faxes = JSON.parse(readFileSync(new URL("../../data/faxes/faxes.json", import.meta.url), "utf8"));
const SANDBOX = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
const size = { width: 1280, height: 800 };

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: size, recordVideo: { dir, size }, bypassCSP: true });
await context.addInitScript((id) => {
  localStorage.setItem("caduceus.sandbox", id);
  // Instant scrolling, so a button the walk is about to press is never mid-scroll.
  document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.scrollBehavior = "auto"; });
  // Sign-in lives in tab storage; a new tab (each clip is one) starts as Dolores unless a switch already happened.
  if (!sessionStorage.getItem("caduceus.devUser") && location.pathname !== "/") sessionStorage.setItem("caduceus.devUser", "dolores");
  window.__caption = (text) => {
    let el = document.getElementById("__caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "__caption";
      el.style.cssText = "position:fixed;left:50%;bottom:150px;transform:translateX(-50%);z-index:2147483647;" +
        "max-width:980px;padding:14px 22px;border-radius:10px;background:rgba(13,19,27,.92);color:#fff;pointer-events:none;" +
        "font:500 22px/1.35 'Source Sans 3',system-ui,sans-serif;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.25)";
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.display = text ? "block" : "none";
  };
}, SANDBOX);

const pause = (ms) => new Promise((r) => setTimeout(r, ms));
// With SHOTS_DIR set, clean stills (no caption) are saved at the key moments, for the README.
const shots = process.env.SHOTS_DIR;
if (shots) mkdirSync(shots, { recursive: true });
async function still(page, name) {
  if (!shots) return;
  await page.evaluate(() => window.__caption?.(""));
  await page.screenshot({ path: `${shots}/${name}.png` });
}
async function say(page, text, ms = 3200) {
  await page.evaluate((t) => window.__caption?.(t), text);
  await pause(ms);
}
async function as(page, label) {
  await page.goto(base + "/");
  const signOut = page.getByRole("button", { name: "Sign out" });
  const person = page.getByRole("main").getByRole("button", { name: new RegExp(label) });
  // The page knows who is signed in only once the API has said so: wait for that before choosing to sign out.
  await signOut.or(person).first().waitFor();
  if (await signOut.isVisible()) await signOut.click();
  await person.click();
  await page.waitForLoadState("networkidle");
}
const status = (page, text) => page.getByRole("status").filter({ hasText: text });
// Waits between clips, unrecorded: ask the API, as Dolores in this copy, until the case reaches a state. Every
// 3 seconds, so the waits stay well inside the visitor's 300 requests per five minutes that the pages also use.
async function until(caseId, states, ms = 120_000) {
  const headers = { "X-Caduceus-User": "dolores", "X-Caduceus-Sandbox": SANDBOX };
  for (const start = Date.now(); Date.now() - start < ms; await pause(3000)) {
    const c = await (await fetch(`${base}/api/cases/${caseId}`, { headers })).json();
    if (states.includes(c.state) && !(c.state === "submitted" && !c.submission?.sent)) return c;
  }
  throw new Error(`${caseId} did not reach ${states.join(" or ")}`);
}
// With the in-network model (CADUCEUS_IN_VPC_URL, as the demo runs), the clinician's chart summary is written in the
// background. Wait for it, unrecorded, by the case history, which reading does not add to.
const IN_VPC = Boolean(process.env.CADUCEUS_IN_VPC_URL);
async function summarized(caseId, ms = 240_000) {
  const headers = { "X-Caduceus-User": "dolores", "X-Caduceus-Sandbox": SANDBOX };
  for (const start = Date.now(); Date.now() - start < ms; await pause(3000)) {
    const t = await (await fetch(`${base}/api/cases/${caseId}/timeline`, { headers })).json();
    if (t.entries.some((e) => /^(Summarized the chart|Could not summarize)/.test(e.what))) return;
  }
  throw new Error(`${caseId}: no chart summary`);
}

// Clip 1: the queue, and a faxed referral read inside the boundary.
let page = await context.newPage();
await page.goto(base + "/");
await say(page, "Caduceus prepares prior-authorization requests for a simulated health system. Every patient is synthetic.", 4200);
await as(page, "Dolores Montoya");
await still(page, "queue");
await say(page, "Dolores, a prior-authorization specialist, sees her queue in plain words: who, what, and when it is due.", 4200);
await page.getByRole("region", { name: "Arrived by fax" }).scrollIntoViewIfNeeded();
await say(page, "Three referrals arrived by fax. Caduceus read them with OCR inside the clinic's systems; the pages never left.");
await page.locator('a[href="/intake/FAX-0003"]').click();
await page.getByRole("heading", { name: "Check the fax" }).waitFor();
await page.locator(".crop").first().waitFor();
await page.evaluate(() => document.querySelector(".crop")?.scrollIntoView({ block: "center" }));
await pause(300);
await still(page, "fax-check");
await page.evaluate(() => window.scrollTo(0, 0));
await say(page, "This one is a poor copy. Every field Caduceus was unsure of comes to a person, beside the part of the page it came from.", 4800);
await page.mouse.wheel(0, 500);
await pause(1500);
const want = faxes["FAX-0003"].expected;
for (const [key, label] of Object.entries({ member_id: "Member ID", diagnosis: "Diagnosis" })) {
  await page.getByLabel(label, { exact: true }).fill(want[key]);
}
await page.getByLabel("Ordering clinician", { exact: true }).selectOption(want.clinician);
await page.getByLabel("Requested service", { exact: true }).selectOption(want.service_code);
for (const box of await page.getByRole("checkbox", { name: /^Matches the fax/ }).all()) {
  if (!(await box.isChecked())) await box.check();
}
await page.getByRole("button", { name: "Yes, this is the patient" }).click();
await say(page, "She corrects the misreads, ticks each unsure field against the page, and confirms the patient in the chart. Only then is the case created and checked.", 3800);
await page.getByRole("button", { name: "Create the case" }).click();
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
await say(page, "Caduceus matched the patient and checked the policy: her back pain began three weeks ago, short of six, and it says so.", 4600);
await page.close();

// Clip 2: evidence, the clinician's attestation, and approval. A second case is sent too; its payer will deny it.
page = await context.newPage();
await page.goto(base + "/cases/2026-1038");
await page.getByRole("button", { name: "Approve and send" }).click();
await status(page, "Approved.").waitFor();
await page.goto(base + "/cases/2026-1043");
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
await say(page, "Kristina Mante needs a lumbar MRI. Three of four criteria are met, each from a sentence in her chart.", 4000);
await page.getByRole("button", { name: /^Show the visit note/ }).first().click();
await page.locator("mark").first().waitFor();
await still(page, "case-evidence");
await say(page, "Every “met” opens the page and highlights the sentence that supports it.", 3600);
await page.getByRole("button", { name: "Back to the draft" }).click();
const sent = page.getByRole("button", { name: "See what was sent" });
if (await sent.isVisible().catch(() => false)) {
  await sent.click();
  await page.getByRole("heading", { name: "What was sent to the model" }).waitFor();
  await still(page, "sent-to-model");
  await say(page, "A model wrote the draft from her facts with every identifier taken out. This is exactly what it saw.", 4400);
  await page.getByRole("button", { name: "Back to the draft" }).click();
}
await say(page, "Nothing in her record says red-flag symptoms were ruled out, so nothing is assumed: the ordering clinician is asked.", 4200);
await page.getByRole("button", { name: /to attest$/ }).click();
await status(page, "This case comes back to you").waitFor();
if (IN_VPC) {  // cut the half minute the model takes, as the outbox waits are cut
  await page.close();
  await summarized("2026-1043");
  page = await context.newPage();
  await page.goto(base + "/cases/2026-1043");
  await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
}
await page.getByRole("region", { name: "Guided tour" }).getByRole("button", { name: /^Switch to Dr\. Ana Reyes/ }).click();
await page.getByLabel("Yes, I confirm this").waitFor();
await still(page, "attest");
await say(page, "Dr. Reyes answers one question on one screen, usually from her phone, and signs with her own name.", 3800);
if (await page.getByText(/^Summarized by .+, inside Mesa Verde’s network/).isVisible().catch(() => false)) {
  await say(page, "The summary was written by the small model inside the network, the only one allowed to see who " +
    "she is, then checked sentence by sentence against her record.", 5000);
}
await page.getByLabel("Yes, I confirm this").check();
await page.getByRole("button", { name: "Sign and return" }).click();
await page.getByText("Signed. Thank you.").waitFor();
await pause(1200);
await page.getByRole("region", { name: "Guided tour" }).getByRole("button", { name: /^Switch to Dolores/ }).click();
await page.getByRole("button", { name: "Approve and send" }).waitFor();
await say(page, "The draft was rewritten to match her answer. Nothing goes to the payer until Dolores approves it.", 3600);
await page.getByRole("button", { name: "Approve and send" }).click();
await status(page, "Approved.").waitFor();
await say(page, "For 30 seconds she can still undo it. Then the agent sends it, through a tool that refuses anything a person did not approve.", 4600);
await page.close();

// Clip 2b, while the outbox waits: three cases built to show one thing each, and the policies behind them.
page = await context.newPage();
await as(page, "Dolores Montoya");  // the queue is "/", where a new tab is not signed in yet
await page.locator("li.row").filter({ hasText: "Raymond Vigil" }).scrollIntoViewIfNeeded();
await say(page, "Some requests can't wait. The sleep clinic marked Raymond Vigil's CPAP request expedited.", 3600);
await page.goto(base + "/cases/2026-1053");
await page.getByText("Expedited.", { exact: true }).waitFor();
await still(page, "expedited");
await say(page, "Its reason is on the case, and the payer owes a decision in 72 hours, not 7 days. A specialist can change it until approval.", 4600);
await page.goto(base + "/cases/2026-1052");
await page.getByText("The packet may answer this").waitFor();
await say(page, "Esther Maestas's therapy isn't in her chart, so Caduceus can't count it. But it searched her 9-page packet.", 4200);
await page.getByRole("button", { name: /^Show the therapy notes, page\s\d+$/ }).first().click();
await page.locator("mark").filter({ hasText: "10 weeks of physical therapy" }).waitFor();
await still(page, "where-to-look");
await say(page, "It opens the outside therapy summary on the passage. That's where to look, not evidence: only Dr. Reyes can confirm it.", 4800);
await page.goto(base + "/cases/2026-1051");
const why = page.getByText(/left knee is charted, but the request is for the right side/);
await why.waitFor();
await why.evaluate((el) => el.scrollIntoView({ block: "center" }));  // the reason on screen, not just the case
await pause(300);
await still(page, "other-knee");
await say(page, "Gilbert Romero's referral asks for the right knee. His chart documents the left. Caduceus stops it and says why.", 4600);
await page.goto(base + "/policies");
await page.getByRole("heading", { name: "Total knee arthroplasty" }).waitFor();
await still(page, "policies");
await say(page, "Those criteria are compiled from Medicare's own words, quoted, approved by a reviewer and locked by hash.", 4400);
await page.close();

// Clip 3: the payer reviews it, asks a question; the clinician writes the note; it goes back.
await until("2026-1043", ["submitted", "pended"]);
page = await context.newPage();
await page.goto(base + "/cases/2026-1043");
await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();  // loaded, so the check below is not early
if (await status(page, "Its decision is expected by").isVisible().catch(() => false)) {
  await say(page, "Humana is reviewing it, and said when it would decide. Caduceus asks again only then.", 3800);
  await page.close();
  await until("2026-1043", ["pended"]);
  page = await context.newPage();
  await page.goto(base + "/cases/2026-1043");
}
await page.getByRole("heading", { name: /asked a question$/ }).waitFor({ timeout: 90_000 });
await still(page, "payer-question");
await say(page, "Humana pended it: it wants the clinical note behind Dr. Reyes's answer. Caduceus never writes that note for her.", 4400);
await page.getByRole("button", { name: /^Ask Dr\. Ana Reyes for the note$/ }).click();
await status(page, "comes back to you when they sign it").waitFor();
await page.getByRole("region", { name: "Guided tour" }).getByRole("button", { name: /^Switch to Dr\. Ana Reyes/ }).click();
await page.getByLabel("Your note").waitFor();
await page.getByLabel("Your note").fill("Addendum to the visit of October 27. Red-flag symptoms were asked about and ruled out: no fever, " +
  "weight loss, history of cancer, saddle anesthesia, or bowel or bladder change.");
await say(page, "She writes it in her own words. It joins the chart as an addendum dated today, never backdated.", 3800);
await page.getByRole("button", { name: "Sign the note and return" }).click();
await page.getByText("Signed. Thank you.").waitFor();
await page.getByRole("region", { name: "Guided tour" }).getByRole("button", { name: /^Switch to Dolores/ }).click();
await page.getByRole("button", { name: /^Send the note to / }).waitFor();
await say(page, "Dolores reads the note and sends it, with the same 30 seconds to undo.", 3200);
await page.getByRole("button", { name: /^Send the note to / }).click();
await status(page, "Sending the note").waitFor();
await page.close();
await until("2026-1043", ["approved"], 180_000);

// Clip 4: approval, the denial and its appeal, the audit trail, and the outcomes.
page = await context.newPage();
await page.goto(base + "/cases/2026-1043");
await status(page, /^Approved by /).waitFor({ timeout: 60_000 });
await say(page, "Humana approved it, within the 7-day limit the CMS prior authorization rule sets.", 3400);
await page.goto(base + "/cases/2026-1038");
await page.getByRole("heading", { name: /denied it$/ }).waitFor({ timeout: 60_000 });
await page.mouse.wheel(0, 250);
await still(page, "denial");
await page.mouse.wheel(0, -250);
await say(page, "UnitedHealthcare denied a CPAP request: AHI 9 is below 15. But its own policy covers 5 to 14 with hypertension.", 4400);
await page.mouse.wheel(0, 350);
await say(page, "Caduceus drafted the appeal from the payer's reason, the policy sentence and the record. A person sends it.", 4200);
await page.getByRole("button", { name: /^Send the appeal to / }).click();
await status(page, "Sending the appeal").waitFor();
await page.goto(base + "/cases/2026-1043");
await page.getByRole("button", { name: "Show the history" }).click();
await page.getByText("Asked Dr. Ana Reyes for the note the payer wants.").waitFor();
// The history's heading at the top of the screen, so the steps under it fill the rest.
await page.getByRole("heading", { name: "Case history" }).evaluate((el) => {
  el.scrollIntoView({ block: "start" });
  window.scrollBy(0, -24);
});
await pause(300);
await still(page, "history");
await say(page, "Every step, who took it and when, sealed to the one before. An auditor can check that none of it was changed.", 4400);
await as(page, "Avery Chen");
await page.goto(base + "/admin");
await page.getByRole("heading", { name: "Where patient information can go" }).waitFor();
await still(page, "admin");
await say(page, "An administrator sees where patient information can go, and can switch the outside model off at once.", 4400);
await as(page, "Dolores Montoya");
await page.goto(base + "/outcomes");
await page.getByRole("heading", { name: "Where patient information went" }).waitFor();
await still(page, "outcomes");
await say(page, "The outcomes page is counted from that log: nothing estimated. Try it yourself at caduceusdemo.netlify.app.", 5000);
await page.close();

await context.close();
await browser.close();
console.log(`clips in ${dir}`);
