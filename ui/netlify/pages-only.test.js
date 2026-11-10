// What Netlify's edge sends on to the API and what it answers itself (edge-functions/pages-only.js).
//   node --test ui/netlify/pages-only.test.js
// Kept beside the functions' directory, not in it: Netlify would deploy a file in there as a function.
import assert from "node:assert/strict";
import test from "node:test";

import pagesOnly, { config } from "./edge-functions/pages-only.js";

const SITE = "https://caduceusdemo.netlify.app";
const SANDBOX = { "X-Caduceus-Sandbox": "0123456789abcdef0123456789abcdef" };
const ask = (path, init) => pagesOnly(new Request(SITE + path, init));
const refused = (path, init) => ask(path, init)?.status === 404;
const sentOn = (path, init) => ask(path, init) === undefined;

// What a scanner asked for on 10 November 2026, as Cloud Run logged it: 22 addresses read out of the pages' script,
// unfilled placeholders and all, in 18 seconds. The first one started the server. The log keeps no headers, but a
// client that sends "${e.id}" as it stands is not running the script, and the script is what sends the sandbox id.
const SCAN = [
  "/api/smart/records/$%7Be%7D",
  "/api/smart/launch",
  "/api/cases/$%7Be.id%7D/appeal",
  "/api/cases/$%7Be.id%7D/$%7Bn%7D",
  "/api/cases/$%7Be.id%7D/pages/$%7Bt.docId%7D",
  "/api/intake/$%7Be%7D/match",
  "/api/smart",
  "/api/smart/callback",
  "/api/cases/$%7Be%7D",
  "/api/intake/$%7Bn.id%7D/ask-clinic",
  "/api/intake/$%7Bn.id%7D/confirm",
  "/api/attestations/$%7Be%7D",
  "/api/intake/$%7Be%7D",
  "/api/smart/records/$%7Bi.key%7D/case",
  "/api/cases/$%7Br.case_id%7D/undo",
  "/api/attestations",
  "/api/me",
  "/api/cases/$%7Be.case_id%7D/undo",
  "/api/intake",
  "/api/cases/$%7Be.id%7D/urgency",
  "/api/admin/external-route",
  "/api/admin",
];
// What scanners try on any site, and addresses near the one that is open.
const GUESSES = [
  "/api", "/api/", "/api/health", "/api/v1/users", "/api/.env", "/api/swagger.json", "/api/graphql",
  "/api/auth-config/", "/api/auth-config/x", "/api/auth-configs", "/api//auth-config",
];
const FIRST_REQUESTS = ["/api/auth-config", "/api/auth-config?from=a-reload"];
const FROM_A_PAGE = [
  ["/api/demo"], ["/api/me"], ["/api/queue"], ["/api/health"], ["/api/cases/PA-0007/pages/doc-2/image"],
  ["/api/cases/PA-0007/approve", { method: "POST" }], ["/api/cases/PA-0007/draft", { method: "PUT" }],
];

test("answers everything the scan of 10 November asked for, without the API", () => {
  for (const path of SCAN) assert.ok(refused(path), path);
});

test("answers guessed addresses itself, the health check among them", () => {
  for (const path of GUESSES) assert.ok(refused(path), path);
  assert.ok(refused("/api/demo/reset", { method: "POST" }));
  assert.ok(refused("/api/me", { method: "OPTIONS" }));
});

test("sends on the one request a page makes before it has a sandbox id", () => {
  for (const path of FIRST_REQUESTS) assert.ok(sentOn(path), path);
});

test("sends on whatever a page asks", () => {
  for (const [path, init] of FROM_A_PAGE) assert.ok(sentOn(path, { ...init, headers: SANDBOX }), path);
  assert.ok(sentOn("/api/me", { headers: { "x-caduceus-sandbox": SANDBOX["X-Caduceus-Sandbox"] } }), "any spelling");
});

test("says no such address, why, and not to keep the answer", async () => {
  const answer = ask("/api/me");
  assert.equal(answer.status, 404);
  assert.match(answer.headers.get("content-type"), /^application\/json/);
  assert.equal(answer.headers.get("cache-control"), "no-store");
  assert.deepEqual(await answer.json(), { message: "The Caduceus demo's API answers only the demo's own pages." });
});

test("is declared to run for exactly the requests it answers", () => {
  // Netlify's own test, from the declaration: the address matches, is not excluded, and each named header is there
  // or missing as declared. A request it skips never runs the function at all.
  const runs = (path, headers = {}) => {
    const { pathname } = new URL(SITE + path);
    return new RegExp(config.pattern).test(pathname) && !new RegExp(config.excludedPattern).test(pathname)
      && Object.entries(config.header).every(([name, present]) => (name in headers) === present);
  };
  const lower = { "x-caduceus-sandbox": SANDBOX["X-Caduceus-Sandbox"] };
  for (const path of [...SCAN, ...GUESSES]) assert.ok(runs(path), path);
  for (const path of FIRST_REQUESTS) assert.ok(!runs(path), path);
  for (const [path] of FROM_A_PAGE) assert.ok(!runs(path, lower), path);
  for (const path of ["/", "/assets/index.js", "/apiary", "/smart/callback", "/robots.txt"]) assert.ok(!runs(path), path);
});
