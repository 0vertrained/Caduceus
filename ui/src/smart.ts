// The public SMART Health IT launcher (launch.smarthealthit.org, smart-on-fhir/smart-launcher-v2) plays the EHR in the
// demo: it opens Caduceus at /smart/launch with ?iss= (its FHIR server) and ?launch= (the launch context), as an EHR
// would. This builds the link that opens the launcher already set up for Caduceus.
import { useEffect, useState } from "react";
import { api, type SmartInfo } from "./api";

export const LAUNCHER = "https://launch.smarthealthit.org/";

// Whether the launch is set up here, asked once per page load: screens that show the entry wait for it rather than
// have it appear under the reader's eyes.
let asked: Promise<SmartInfo> | null = null;
let known: SmartInfo | null = null;

export function smartInfo(): Promise<SmartInfo> {
  asked ??= api<SmartInfo>("/api/smart")
    .catch(() => ({ enabled: false, client_id: null }))
    .then((info) => (known = info));
  return asked;
}

export function useSmartInfo(): SmartInfo | null {
  const [info, setInfo] = useState(known);
  useEffect(() => {
    let current = true;
    void smartInfo().then((v) => current && setInfo(v));
    return () => { current = false; };
  }, []);
  return info;
}

// The launcher reads its launch options from ?launch=, a JSON array in base64url, in the order of its codec
// (src/isomorphic/codec.ts): launch type, patient, provider, encounter, skip login, skip auth, simulate EHR, scope,
// redirect URIs, client id, client secret, auth error, JWKS URL, JWKS, client type, PKCE, FHIR server.
const PROVIDER_EHR_LAUNCH = 0;  // launchTypes[0]: a practitioner opens the app from inside the EHR
const PUBLIC_CLIENT = 0;        // clientTypes[0]: no client secret; PKCE protects the code instead
const PKCE_ALWAYS = 2;          // PKCEValidationTypes[2]: the launcher refuses a request without PKCE

// A sandbox patient and practitioner to start on. Left empty, the launcher asks for them: no sandbox id could be
// checked from where this was built, and a wrong one would break the launch (see docs/smart-launch.md).
export const SANDBOX_PATIENT = "";
export const SANDBOX_PRACTITIONER = "";

export function launchOptions(origin: string, clientId: string): (string | number)[] {
  return [
    PROVIDER_EHR_LAUNCH, SANDBOX_PATIENT, SANDBOX_PRACTITIONER,
    "NONE",                       // no encounter: Caduceus asks for none
    1, 1,                         // skip the login and approval screens the launcher can skip
    0,                            // not inside the launcher's simulated EHR frame: Caduceus refuses to be framed
    "",                           // any scope; Caduceus asks for the least it needs
    `${origin}/smart/callback`,   // the one redirect URI, checked by the launcher as a registered EHR would
    clientId, "", "", "", "",     // the client id it is registered with; no secret, no simulated error, no keys
    PUBLIC_CLIENT, PKCE_ALWAYS,
    "",                           // the launcher's own FHIR R4 server
  ];
}

function base64url(text: string): string {
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The launcher, set up to open this site's /smart/launch as a provider EHR launch. */
export function launcherUrl(origin: string, clientId: string): string {
  const q = new URLSearchParams({
    launch_url: `${origin}/smart/launch`,
    launch: base64url(JSON.stringify(launchOptions(origin, clientId))),
    fhir_version: "r4",
    tab: "0",
    validation: "1",  // the launcher holds the client id and redirect URI to the ones above
  });
  return `${LAUNCHER}?${q}`;
}

/** Only ever send the browser to an http(s) address the API gave: never a javascript: or data: one. */
export function navigable(url: string): boolean {
  try {
    return ["https:", "http:"].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}
