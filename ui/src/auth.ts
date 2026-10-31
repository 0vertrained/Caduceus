// How a person signs in, as the API says (GET /api/auth-config): with the development roster (dev, and the public
// demo, which sign in as they always have: see api.ts), or with the health system's identity provider over OpenID
// Connect (oidc). OIDC here is the Authorization Code flow with PKCE (S256), the one meant for a public client like
// this page, with the state and a nonce checked on the way back. oidc-client-ts does the protocol; this module
// decides where its results are kept, when they are renewed, and when they end.
//
// Storage: sessionStorage, never localStorage. The tokens last as long as this tab, through a reload and the round
// trip to the identity provider; they are not shared with other tabs; and they are gone when the tab is closed.
// localStorage would keep them on disk, for every tab, after the person has left. Script running in the page could
// read them in sessionStorage, as it could in memory: what keeps such script out, and the tokens from being sent
// anywhere else, is the Content Security Policy (scripts from this site only; connections to this site and the
// issuer only).
//
// Renewal: with the refresh token, from this page to the provider's token endpoint, a minute before the access
// token expires, or sooner when a request finds it about to. Not with a hidden iframe ("silent renew"): browsers now
// withhold the provider's cookies from a frame on another site, and this site's policy allows no frames at all. A
// public client may hold a refresh token when the provider rotates it on every use and ends it with the session
// (OAuth 2.0 for Browser-Based Apps); the compose Keycloak realm does both. Without a refresh token, a session lasts
// as long as its access token, and then the person signs in again.

import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

export type AuthConfig =
  | { mode: "dev" | "demo" }
  | { mode: "oidc"; issuer: string; client_id: string; scope: string };
type OidcConfig = Extract<AuthConfig, { mode: "oidc" }>;

export const CALLBACK_PATH = "/auth/callback";
export const SIGNED_OUT_PATH = "/signed-out";

/** Why the sign-in page is showing, when it has something to say. */
export type SignInNotice =
  | "signed-out" | "signed-out-here" | "signing-out" | "ended" | "failed" | "refused" | "unreachable"
  | "misconfigured";

/** Raised when a session ends without the person asking: the API refused its token, or it ran out. */
export const SESSION_ENDED = "caduceus:session-ended";

export class SignInProblem extends Error {
  constructor(public notice: SignInNotice) {
    super(notice);
  }
}

let config: Promise<AuthConfig> | null = null;

/** The sign-in mode, asked for once. A failure is not kept, so trying again asks again. */
export function authConfig(): Promise<AuthConfig> {
  config ??= readConfig().catch((e: unknown) => {
    config = null;
    throw e;
  });
  return config;
}

async function readConfig(): Promise<AuthConfig> {
  const res = await fetch("/api/auth-config", { cache: "no-store" });
  // An API from before this endpoint answers with the page itself, or not at all: the demo's does, for the minutes
  // its front end is deployed ahead of it. It signs in with the roster, as it always did.
  const json = (res.headers.get("content-type") ?? "").includes("application/json");
  if (res.status === 404 || (res.ok && !json)) return { mode: "dev" };
  if (!res.ok) throw new Error(`The sign-in settings could not be read (${res.status}).`);
  const body = (await res.json()) as Record<string, unknown>;
  if (body.mode === "dev" || body.mode === "demo") return { mode: body.mode };
  const { issuer, client_id, scope } = body;
  if (body.mode === "oidc" && typeof issuer === "string" && typeof client_id === "string" && typeof scope === "string") {
    return { mode: "oidc", issuer, client_id, scope };
  }
  throw new Error("The sign-in settings are not ones this page knows.");
}

let manager: UserManager | null = null;

/** The OIDC client, made once; null outside OIDC. */
async function oidc(): Promise<UserManager | null> {
  const c = await authConfig().catch(() => null);
  if (c?.mode !== "oidc") return null;
  manager ??= client(c);
  return manager;
}

function client(c: OidcConfig): UserManager {
  const tab = window.sessionStorage;
  const m = new UserManager({
    authority: c.issuer,
    client_id: c.client_id,
    scope: c.scope,
    response_type: "code",
    redirect_uri: window.location.origin + CALLBACK_PATH,
    post_logout_redirect_uri: window.location.origin + SIGNED_OUT_PATH,
    // The code and state come back after "#", which a browser never sends to a server: out of server and proxy logs.
    response_mode: "fragment",
    userStore: new WebStorageStateStore({ store: tab, prefix: "caduceus.session." }),
    // The PKCE verifier, state and nonce while the person is at the provider (the library's default is localStorage).
    stateStore: new WebStorageStateStore({ store: tab, prefix: "caduceus.signin." }),
    // Renewal is below, with the refresh token only. No silent_redirect_uri, so it can never fall back to an iframe.
    automaticSilentRenew: false,
    silent_redirect_uri: "",
    monitorSession: false,  // that needs a frame at the provider, too
    loadUserInfo: false,  // who this is comes from the API (/api/me), which checks the access token itself
    requestTimeoutInSeconds: 20,
    accessTokenExpiringNotificationTimeInSeconds: 60,
  });
  m.events.addAccessTokenExpiring(() => void renew());
  m.events.addAccessTokenExpired(() => {
    void renew().then((user) => {
      if (!user) void endSession("ended");
    });
  });
  return m;
}

let renewing: Promise<User | null> | null = null;

/** One renewal at a time: with rotation, a second one in flight would spend a refresh token the first replaced. */
function renew(): Promise<User | null> {
  renewing ??= (async () => {
    const m = await oidc();
    const user = await m?.getUser();
    if (!m || !user?.refresh_token) return null;
    try {
      return await m.signinSilent();  // with a refresh token, oidc-client-ts makes the refresh_token grant
    } catch {
      return null;  // refused or unreachable: the session ends when the access token does
    }
  })().finally(() => {
    renewing = null;
  });
  return renewing;
}

/** The access token for a request to the API, renewed first if it is about to run out (a token that could not be
 *  renewed serves until it does); null when signed out, or when it has run out. */
export async function accessToken(): Promise<string | null> {
  const m = await oidc();
  let user = m && (await m.getUser());
  if (user?.expires_in !== undefined && user.expires_in <= 30) user = (await renew()) ?? user;
  return user && user.expired !== true ? user.access_token : null;
}

/** Whether this tab holds a session that can still be used. */
export async function hasSession(): Promise<boolean> {
  return Boolean(await accessToken());
}

/** Forget this tab's session, without a word: the caller says what happened. */
export async function clearSession(): Promise<void> {
  await (await oidc())?.removeUser();
}

/** A session ended without the person asking: forget it, and tell the app, which returns to sign-in and says so. */
export async function endSession(notice: SignInNotice = "ended"): Promise<void> {
  await clearSession();
  window.dispatchEvent(new CustomEvent<SignInNotice>(SESSION_ENDED, { detail: notice }));
}

/** Off to the identity provider, to come back to `returnTo` once signed in. Resolves only if the person comes back
 *  without signing in (the back button); throws a SignInProblem if the provider cannot be reached, or is not the one
 *  the API trusts. */
export async function startSignIn(returnTo: string): Promise<void> {
  const [m, c] = await Promise.all([oidc(), authConfig()]);
  if (!m || c.mode !== "oidc") throw new SignInProblem("failed");
  let issuer: string;
  try {
    issuer = await m.metadataService.getIssuer();
  } catch {
    throw new SignInProblem("unreachable");
  }
  // The provider must name itself as the API expects its tokens to (OpenID Connect Discovery 1.0, section 4.3).
  if (issuer !== c.issuer) throw new SignInProblem("misconfigured");
  try {
    await m.signinRedirect({ state: { returnTo: safePath(returnTo) }, nonce: randomValue() });
  } catch {
    throw new SignInProblem("failed");
  }
}

let finishing: Promise<string> | null = null;

/** Finishes sign-in on the callback page, once even if asked twice: oidc-client-ts finds the state this tab stored,
 *  exchanges the code with its PKCE verifier, and checks the ID token's nonce. Returns where the person was going. */
export function finishSignIn(): Promise<string> {
  finishing ??= (async () => {
    const [m, c] = await Promise.all([oidc(), authConfig()]);
    if (!m || c.mode !== "oidc") throw new SignInProblem("failed");
    let user: User;
    try {
      user = await m.signinRedirectCallback(window.location.href);
    } catch {
      throw new SignInProblem("failed");
    }
    // The library checks the state, the nonce and that there is a subject; the issuer and the audience are ours to.
    const { iss, aud } = user.profile;
    if (iss !== c.issuer || !(Array.isArray(aud) ? aud : [aud]).includes(c.client_id)) {
      await m.removeUser();
      throw new SignInProblem("failed");
    }
    return safePath((user.state as { returnTo?: unknown } | undefined)?.returnTo);
  })();
  return finishing;
}

/** Sign out: this tab's session first, whatever happens next, then the provider's at its end_session_endpoint, which
 *  comes back to /signed-out. With the session already gone the request carries the client id, not the ID token, so
 *  no token goes into an address; the provider asks the person to confirm instead. Throws if the provider cannot be
 *  reached, and resolves if the person comes back from it without confirming: signed out here either way. */
export async function signOutEverywhere(): Promise<void> {
  const m = await oidc();
  if (!m) return;
  await m.removeUser();
  await m.signoutRedirect();
}

/** A path on this site to return to after signing in: never another site, and never the sign-in pages themselves. */
export function safePath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return "/";
  }
  const url = new URL(value, window.location.origin);
  if (url.origin !== window.location.origin || [CALLBACK_PATH, SIGNED_OUT_PATH].includes(url.pathname)) return "/";
  return url.pathname + url.search;
}

function randomValue(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
