// Signing in, in the mode the API names (GET /api/auth-config): the development roster (dev, and the public demo;
// see api.ts), or the health system's identity provider over OpenID Connect, as a public client with PKCE.
// oidc-client-ts runs the protocol; this module decides where its results are kept, and when they renew and end.
//
// Tokens live in sessionStorage: through a reload and the trip to the provider, in this tab only, and gone with it;
// localStorage would keep them for every tab after the person has left. Script in the page could read them there as
// in memory: the Content Security Policy is what keeps such script out, and the tokens from going anywhere else.
// They renew by refresh token, which a browser may hold only when the provider rotates it on every use (OAuth 2.0
// for Browser-Based Apps), never by hidden iframe: browsers withhold the provider's cookies from a cross-site frame,
// and the policy allows no frames.

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
  // An API without this endpoint answers 404 or with the page itself (the demo's does while its front end deploys
  // ahead of it), and takes the roster's sign-in.
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
    // Renewal is renew(), by refresh token only; with no silent_redirect_uri it can never fall back to an iframe.
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

/** The access token for an API request, renewed first when about to run out; null when signed out or run out. */
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

/** Off to the provider, back at `returnTo`. Resolves only if the person comes back without signing in; throws a
 *  SignInProblem when the provider cannot be reached or is not the issuer the API trusts. */
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

/** Finishes sign-in on the callback page, once even if asked twice, and resolves to where the person was going. */
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

/** Signs out here first, so no ID token goes into the provider's address (docs/sign-in.md), then at the provider.
 *  Throws if it cannot be reached; resolves if the person comes back without confirming there. */
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
