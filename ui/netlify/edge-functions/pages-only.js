// Netlify runs this at its edge, ahead of the proxy rule in netlify.toml, on API requests that carry no sandbox id.
//
// Every request the demo's pages make carries one (ui/src/api.ts), apart from the first, which asks how to sign in.
// A request without one did not come from a page: it is a crawler trying an address it read in the script, or a
// scanner guessing. The API turns those away itself, but has to be running to do it, and the server behind the demo
// stops when nobody is using it: one stray request starts it and keeps it running, and billed, for about a quarter
// of an hour. Answered here, the request never reaches it.
//
// This is a filter, not a lock. The header's name is in the pages' script for anyone to read; what a request may do
// is still decided by the API, on every request.

const SANDBOX = "x-caduceus-sandbox";
// What a page asks before it has anything to send.
const FIRST_REQUEST = "/api/auth-config";

/** Nothing for a request from one of the demo's pages, which sends it on to the API; a 404 for any other. */
export default function pagesOnly(request) {
  if (request.headers.has(SANDBOX) || new URL(request.url).pathname === FIRST_REQUEST) return undefined;
  return Response.json(
    { message: "The Caduceus demo's API answers only the demo's own pages." },
    { status: 404, headers: { "cache-control": "no-store" } },
  );
}

// The declaration keeps the function away from the pages' own requests, so none of them waits for it. The function
// makes the same test for wherever it runs without the declaration: the end-to-end walk, and its own tests.
export const config = {
  pattern: "^/api(/.*)?$",
  excludedPattern: `^${FIRST_REQUEST}$`,
  header: { [SANDBOX]: false },
};
