# Signing in

Caduceus signs people in one of three ways, set by `CADUCEUS_AUTH_MODE`. Before anything else, the UI asks the API
which one (`GET /api/auth-config`).

| Mode | Who can sign in | How the API knows who it is | Used for |
|---|---|---|---|
| `dev` | Anyone, by picking a name from the development roster | The `X-Caduceus-User` header names a roster person | A developer's machine. Compose binds it to 127.0.0.1 |
| `demo` | The same roster, for any visitor | The same header, with the visitor's own copy of the cases | The public demo ([demo-deployment.md](demo-deployment.md)) |
| `oidc`, the default | People with an account at the health system's identity provider and a Caduceus role | A bearer access token, checked against the provider's signing keys | Everywhere else |

## OIDC in the browser

- The UI is a public client: it holds no secret. It uses the Authorization Code flow with PKCE (S256).
- To sign in, the browser goes to the provider and comes back to `/auth/callback`. The code comes back after `#`
  (response mode fragment), so it never reaches a server or proxy log. The UI checks the state and the nonce,
  exchanges the code with its PKCE verifier, and takes the code out of the address. Then it returns the person to
  the page they were opening.
- The API answers who is signed in (`/api/me`) from the access token: the name, the role and, for a clinician, the
  clinician id. The UI never decides a role.
- Tokens stay in the tab's sessionStorage, never localStorage. A reload keeps the person signed in; closing the tab
  forgets the session.
- A minute before the access token expires, the UI renews it with the refresh token. If it cannot, or the API
  refuses the token, the UI goes back to sign-in and says the session ended.
- Sign out clears the tab first, then ends the session at the provider (its `end_session_endpoint`) and comes back
  to `/signed-out`. The request carries the client id, not the ID token, so no token goes into an address; the
  provider asks the person to confirm instead.
- The page's Content Security Policy lets it connect to its own site and to the issuer and the addresses under it,
  and nowhere else.

`make e2e-oidc` (or `scripts/e2e-oidc.sh`) walks all of this in Chromium, with the policy enforced, against the test
identity provider in `tests/mock_idp.py`. That provider is for tests only: it signs in anyone who picks a name.

## Keycloak on your machine

1. In `.env`, set `CADUCEUS_AUTH_MODE=oidc`, and set `CADUCEUS_KEYCLOAK_ADMIN_PASSWORD` and
   `CADUCEUS_KEYCLOAK_USER_PASSWORD`, each 12 characters or more:
   `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. Run `docker compose --profile oidc up --build`.
3. Open http://127.0.0.1:8000 and choose **Sign in with your Mesa Verde account**. Sign in as `dolores`,
   `dr.reyes`, `dr.lee`, `dr.patel`, `avery` or `sam`, with the user password from `.env`.

Keycloak's administration console is at http://localhost:8080/admin, as `admin` with the administrator password.

The realm, `infra/keycloak/mesaverde-realm.json`, sets up:

- The `mesaverde` realm. Keycloak runs at http://localhost:8080, the address in every token's `iss`.
- The `caduceus-ui` client: public, Authorization Code only, PKCE with S256 required. Its redirect URIs are exactly
  `http://127.0.0.1:8000/auth/callback` and `http://localhost:8000/auth/callback`. It returns after sign-out only to
  `/signed-out` on those two addresses, and answers the browser only from them.
- An audience mapper that puts `caduceus-api` in each access token's `aud`, and user-attribute mappers for
  `caduceus_role` and `clinician_id`.
- The six people of the development roster. Each one's user id, and so their `sub`, is their roster id. Only an
  administrator can see or change `caduceus_role` and `clinician_id`.
- Access tokens that last 5 minutes. Refresh tokens rotate on every use, a reused one is refused, and sessions end
  after 30 idle minutes or 10 hours.

No password is written in the repository. Where a password goes, the realm file has
`${CADUCEUS_KEYCLOAK_USER_PASSWORD}`, which Keycloak fills from its environment when it imports the realm. Compose
passes that variable, and the administrator's password, from `.env`, and the container will not start without
both. Keycloak keeps its database inside the container, so to change a password, change `.env` and run
`docker compose --profile oidc up --force-recreate keycloak`: the realm is imported again.

The API reaches Keycloak for its signing keys on an internal network. Only the port the browser needs is published,
and only on 127.0.0.1.

This setup has not been run yet. The image could not be pulled where it was written, so it was checked with
`docker compose config` and `tests/test_keycloak_realm.py`, not by starting Keycloak.

## Connecting the health system's identity provider

### The API's settings

| Setting | Value |
|---|---|
| `CADUCEUS_AUTH_MODE` | `oidc` |
| `CADUCEUS_OIDC_ISSUER` | The issuer, exactly as its tokens' `iss` names it. Must be https |
| `CADUCEUS_OIDC_JWKS_URL` | Where the provider publishes its signing keys (`jwks_uri` in its discovery document) |
| `CADUCEUS_OIDC_AUDIENCE` | The API's audience: what access tokens carry in `aud`. Not the client id |
| `CADUCEUS_OIDC_CLIENT_ID` | The UI's client id |
| `CADUCEUS_OIDC_SCOPE` | `openid profile` unless set. Add `offline_access` only for a provider that issues refresh tokens no other way. Keycloak does not need it, and with it would issue refresh tokens that outlive the session |

In oidc mode the service will not start without the issuer, the signing keys' address, the audience and the client
id, with an issuer that is not an https address, or with the client id as the audience. The last rule means an ID
token, whose audience is the client, can never pass as an access token.

### The client to register

- A public client, or single-page application: no secret.
- The Authorization Code flow, with PKCE required and S256 its only method. No implicit flow, no password grant, no
  client credentials. Response mode fragment allowed.
- Redirect URI exactly `https://<caduceus address>/auth/callback`. Post-logout redirect URI exactly
  `https://<caduceus address>/signed-out`. Allowed web origin (CORS) `https://<caduceus address>`.
- Short access tokens, 5 to 15 minutes. Refresh tokens rotated on every use, a reused one refused, and none
  outliving the session.
- RP-initiated logout, through an `end_session_endpoint`.

The page connects only to the issuer and the addresses under it, so the discovery document and the token endpoint
must be there. Keycloak's are. Where a provider's token endpoint is elsewhere (Microsoft Entra ID's is not under its
`v2.0` issuer, for one), add its address to `security_headers` in `src/caduceus/api/headers.py`.

### The claims in the access token

| Claim | Needed | Value |
|---|---|---|
| `iss` | Yes | The issuer |
| `aud` | Yes | Includes `CADUCEUS_OIDC_AUDIENCE` |
| `exp` | Yes | A token without one is refused |
| `sub` | Yes | The same for a person every time: the audit log records each action by it |
| `caduceus_role` | Yes | One string: `pa_specialist`, `clinician`, `admin` or `auditor`. Map it from the person's groups at the provider |
| `clinician_id` | For clinicians | The practitioner id that referrals name the ordering clinician by, such as `pract-reyes` |
| `name` | Recommended | Shown in the header and on approvals and signatures. Without it, `sub` is shown |

Tokens are signed with RS256 or ES256. A token with any other role is refused, including the agent's own
`agent_runtime`.

### What to know before you switch it on

- Serve Caduceus over https. PKCE needs the browser's cryptography, which it offers only to secure pages: https, or
  localhost on a developer's machine.
- While a tab is open, Caduceus renews its token, so the provider's idle timeout does not end the session. Set the
  provider's longest session to the length of a working shift.
- The case history names people by looking up their `sub` in the development roster. Keycloak's users here have
  roster ids, so their names show. With another provider, the history shows each person's `sub` until names are
  recorded with each event, which is not built yet.
