// Sign-in through OpenID Connect, end to end, against the test identity provider (tests/mock_idp.py), with each page's
// Content Security Policy enforced (no bypassCSP), so the walk also shows that the policy lets the page reach the
// provider. axe-core checks the sign-in and callback screens, and the ones a person sees after them.
//   node ui/e2e/oidc.mjs <app-origin> <issuer> <screenshot-dir>
// Uses the Playwright library and Chromium installed on the machine, as run.mjs does. scripts/e2e-oidc.sh starts
// the provider and the API, then runs this.
import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const axeSource = readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");

const [base, issuer, out = "ui/test-results/oidc"] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const tokenEndpoint = `${issuer}/protocol/openid-connect/token`;
const SIGN_IN = "Sign in with your Mesa Verde account";

const report = { screens: [], checks: [], failures: [] };
const check = (ok, what) => {
  report.checks.push({ what, ok: Boolean(ok) });
  if (!ok) report.failures.push(what);
};
const shot = (page, name) => page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });

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

const sessionKeys = (page) => page.evaluate(() => Object.keys(sessionStorage).filter((k) => k.startsWith("caduceus.session.")));
const policyViolations = [];
const watch = (page) => page.on("console", (m) => {
  if (/Content Security Policy/i.test(m.text())) policyViolations.push(m.text());
});

const browser = await chromium.launch();
const desk = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await desk.newPage();
watch(page);
const toApi = [];
page.on("request", (r) => {
  if (r.url().startsWith(`${base}/api/`)) {
    toApi.push({ url: r.url(), auth: r.headers().authorization ?? null, dev: r.headers()["x-caduceus-user"] ?? null });
  }
});
let current = page;

try {
  // A link to a case, opened signed out: one action, and a policy that lets the page reach the provider, no wider.
  const first = await page.goto(`${base}/cases/2026-1043`);
  const policy = first.headers()["content-security-policy"] ?? "";
  check(policy.includes(`connect-src 'self' ${issuer}/`), "the page may connect to the issuer");
  check(!/connect-src[^;]*(\*|\bhttps?:(?!\/\/))/.test(policy), "connect-src has no wildcard or bare scheme");
  await page.getByRole("button", { name: SIGN_IN }).waitFor();
  check(await page.getByRole("button", { name: /Dolores Montoya/ }).count() === 0, "no development roster");
  await shot(page, "01-sign-in");
  await axe(page, "01-sign-in");

  // To the provider: the UI's public client, the code flow with PKCE (S256), a state and a nonce, the registered
  // redirect URI, and the code to come back in the fragment.
  await page.getByRole("button", { name: SIGN_IN }).click();
  await page.waitForURL((u) => u.href.startsWith(`${issuer}/protocol/openid-connect/auth`));
  const asked = new URL(page.url()).searchParams;
  check(asked.get("client_id") === "caduceus-ui" && asked.get("response_type") === "code", "the code flow, as caduceus-ui");
  check(asked.get("code_challenge_method") === "S256" && /^[\w-]{43}$/.test(asked.get("code_challenge") ?? ""),
    "PKCE with S256");
  check(asked.get("state") && asked.get("nonce"), "a state and a nonce");
  check(asked.get("redirect_uri") === `${base}/auth/callback`, "the registered redirect URI");
  check(asked.get("response_mode") === "fragment", "the code asked for in the fragment");

  // Back on the callback screen, held there while axe looks, then on to the case the link was for.
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route(tokenEndpoint, async (route) => { await held; await route.continue(); });
  await page.getByRole("button", { name: /^Dolores Montoya/ }).click();
  await page.getByRole("heading", { name: "Signing you in…" }).waitFor();
  const callback = new URL(page.url());
  check(callback.pathname === "/auth/callback" && !callback.search && callback.hash.includes("code="),
    "the code came back in the fragment, never the query");
  await shot(page, "02-callback");
  await axe(page, "02-callback");
  release();
  await page.waitForURL(`${base}/cases/2026-1043`);
  await page.unroute(tokenEndpoint);
  await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
  check(!page.url().includes("#") && !page.url().includes("code="), "the code is gone from the address");
  check((await page.locator(".header__name").textContent()) === "Dolores Montoya", "signed in as Dolores Montoya");

  // Where the session is kept, and what the API was sent.
  const local = await page.evaluate(() => Object.entries(localStorage));
  check(local.every(([k, v]) => !/token|eyJ/i.test(`${k} ${v}`)), "no tokens in localStorage");
  check((await sessionKeys(page)).length === 1, "the session is in this tab's sessionStorage");
  const authed = toApi.filter((r) => !r.url.endsWith("/api/auth-config"));
  check(authed.length && authed.every((r) => /^Bearer [\w-]+\.[\w-]+\.[\w-]+$/.test(r.auth ?? "")),
    "every API request carries the access token");
  check(toApi.every((r) => r.dev === null), "no request carries the development header");

  // The queue, and a case opened from it.
  await page.getByRole("link", { name: /^Caduceus/ }).click();
  await page.getByRole("link", { name: /^Open case for/ }).first().waitFor();
  await shot(page, "03-queue");
  await axe(page, "03-queue");
  await page.getByRole("link", { name: /^Open case for/ }).first().click();
  await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();
  await axe(page, "04-case");

  // Renewal with the refresh token before the access token runs out (the provider issues it for 70 seconds here).
  const before = toApi.at(-1).auth;
  const renewed = (r) => r.url() === tokenEndpoint && (r.request().postData() ?? "").includes("grant_type=refresh_token");
  const renewal = await page.waitForResponse(renewed, { timeout: 45_000 });
  const sent = new URLSearchParams(renewal.request().postData());
  check(renewal.ok(), "the access token was renewed with the refresh token");
  check(sent.get("client_id") === "caduceus-ui" && !sent.has("client_secret"), "as a public client: its id, no secret");
  await page.getByRole("link", { name: /^Caduceus/ }).click();
  await page.getByRole("link", { name: /^Open case for/ }).first().waitFor();
  check(toApi.at(-1).auth !== before, "requests after it carry the new access token");

  // A session the API refuses, and the provider will not renew: back to sign-in at the same address, saying so.
  // (Spoiled just after a renewal is stored, so the next one is seconds away.)
  const next = await page.waitForResponse(renewed, { timeout: 45_000 });
  const fresh = (await next.json()).access_token;
  await page.waitForFunction((t) => Object.keys(sessionStorage).some((k) => sessionStorage.getItem(k).includes(t)), fresh);
  await page.evaluate(() => {
    const key = Object.keys(sessionStorage).find((k) => k.startsWith("caduceus.session."));
    const user = JSON.parse(sessionStorage.getItem(key));
    sessionStorage.setItem(key, JSON.stringify({ ...user, access_token: "not.a-real.token", refresh_token: "spent" }));
  });
  const link = page.getByRole("link", { name: /^Open case for/ }).first();
  const where = new URL(await link.getAttribute("href"), base).href;
  await link.click();
  await page.getByText(/^Your session ended/).waitFor();
  check(page.url() === where, "it stays at the address it was going to");
  check((await sessionKeys(page)).length === 0, "and forgets the session");
  await page.getByRole("button", { name: SIGN_IN }).waitFor();
  await shot(page, "05-session-ended");
  await axe(page, "05-session-ended");

  // Signing in again needs no choosing while the provider's session lasts, and comes back to the same case.
  await page.getByRole("button", { name: SIGN_IN }).click();
  await page.waitForURL(where);
  await page.getByRole("heading", { name: "Coverage criteria" }).waitFor();

  // Sign out: the tab's session goes first, then the provider's, which asks, since it is sent no ID token.
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL((u) => u.href.startsWith(`${issuer}/protocol/openid-connect/logout`));
  const bye = new URL(page.url()).searchParams;
  check(!bye.has("id_token_hint") && ![...bye.values()].some((v) => v.startsWith("eyJ")), "no token in the sign-out address");
  check(bye.get("client_id") === "caduceus-ui" && bye.get("post_logout_redirect_uri") === `${base}/signed-out`,
    "the client and its registered return address");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(`${base}/signed-out`);
  await page.getByText("You are signed out of Caduceus and of your Mesa Verde account.").waitFor();
  check((await sessionKeys(page)).length === 0, "nothing of the session is left in the tab");
  await shot(page, "06-signed-out");
  await axe(page, "06-signed-out");

  // Signed out at the provider too: it asks who, again. A clinician this time, matched by the clinician_id claim.
  await page.getByRole("button", { name: SIGN_IN }).click();
  await page.getByRole("button", { name: /^Dr\. Ana Reyes/ }).click();
  await page.waitForURL(`${base}/`);
  await page.getByRole("heading", { level: 1, name: /signature/ }).waitFor();
  check((await page.locator(".header__name").textContent()) === "Dr. Ana Reyes", "signed in as Dr. Ana Reyes");
  await axe(page, "07-clinician");

  // A callback this tab did not start, as a forged or replayed code would arrive: refused, and no one signed in.
  // At 320 px, so the sign-in screen's reflow is checked too.
  const phone = await browser.newContext({ viewport: { width: 320, height: 800 } });
  current = await phone.newPage();
  watch(current);
  await current.goto(`${base}/auth/callback#code=forged&state=forged`);
  await current.getByText(/^Sign-in did not finish/).waitFor();
  check(new URL(current.url()).pathname === "/" && !current.url().includes("code="), "back at the start, the code gone");
  check((await sessionKeys(current)).length === 0, "no one is signed in");
  await shot(current, "08-callback-refused-320px");
  await axe(current, "08-callback-refused-320px");
} catch (e) {
  report.failures.push(`the walk stopped: ${String(e?.message ?? e).split("\n")[0]}`);
  await shot(current, "99-stopped").catch(() => {});
}

check(policyViolations.length === 0, `no Content Security Policy violations ${policyViolations.slice(0, 2).join(" | ")}`);
await browser.close();
console.log(JSON.stringify(report, null, 2));
process.exit(report.failures.length ? 1 : 0);
