// Thin client for the Caduceus API. Development sign-in (and the public demo) sends the chosen roster user in a
// header; with OIDC it sends the access token instead (auth.ts). The endpoints are the same.
import { accessToken, authConfig, endSession } from "./auth";

const USER_KEY = "caduceus.devUser";

export function currentUser(): string | null {
  try {
    return sessionStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

export function setCurrentUser(user: string | null): void {
  try {
    if (user) sessionStorage.setItem(USER_KEY, user);
    else sessionStorage.removeItem(USER_KEY);
  } catch {
    /* storage unavailable: the user signs in again on reload */
  }
}

// The public demo gives each browser its own copy of the cases, named by a random id kept in this browser. A link
// carrying ?sandbox= (the tour's "link for your phone") joins the same copy from another device.
const SANDBOX_KEY = "caduceus.sandbox";
const SANDBOX_ID = /^[0-9a-f]{32}$/;
let sandbox: string | null = null;

function remember(id: string): string {
  sandbox = id;
  try {
    localStorage.setItem(SANDBOX_KEY, id);
  } catch {
    /* storage unavailable: this tab keeps its copy until it is closed */
  }
  return id;
}

export function sandboxId(): string {
  if (sandbox) return sandbox;
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(SANDBOX_KEY);
  } catch {
    /* fall through to a new id */
  }
  if (stored && SANDBOX_ID.test(stored)) return (sandbox = stored);
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return remember(Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""));
}

export function sandboxLink(): string {
  return `${window.location.origin}/?sandbox=${sandboxId()}`;
}

(function joinFromLink() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get("sandbox");
  if (!id) return;
  if (SANDBOX_ID.test(id)) remember(id);
  params.delete("sandbox");
  const rest = params.toString();
  window.history.replaceState(window.history.state, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
})();

/** Something changed on the server: the tour listens for this to move on. */
export const CHANGED = "caduceus:changed";

/** A refusal can name the form field it is about, or a case that already covers what was asked. */
export interface Refusal { field?: string; case_id?: string }

export class ApiError extends Error {
  constructor(public status: number, message: string, public refusal: Refusal = {}) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}, as: "json" | "blob" = "json"): Promise<T> {
  const headers = new Headers(init.headers);
  const oidc = (await authConfig().catch(() => null))?.mode === "oidc";
  const token = oidc ? await accessToken() : null;
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  } else if (!oidc) {
    const user = currentUser();
    if (user) headers.set("X-Caduceus-User", user);
    headers.set("X-Caduceus-Sandbox", sandboxId());
  }
  if (init.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    // The API refused the token, or there was none left to send: it ran out, was revoked, or was never good. The
    // session ends in this tab too. (Signed out, the UI asks the API nothing that needs signing in.)
    if (oidc && res.status === 401) await endSession("ended");
    let message = "Something went wrong. Try again in a moment.";
    let refusal: Refusal = {};
    try {
      const body = await res.json();
      const said = body.message ?? body.detail;
      if (typeof said === "string") message = said;  // a 422's detail is a list for developers, not words
      refusal = { field: body.field, case_id: body.case_id };
    } catch {
      /* keep the default message */
    }
    throw new ApiError(res.status, message, refusal);
  }
  if (init.method && init.method !== "GET") window.dispatchEvent(new Event(CHANGED));
  return (as === "blob" ? res.blob() : res.json()) as Promise<T>;
}

/** Whether this is the public demo, and when its cases go back to the start. */
export type DemoInfo =
  | { demo: false }
  | { demo: true; reset_every_hours: number; last_reset_at: string; next_reset_at: string; tour: Tour | null };

export interface Tour {
  step: number; steps: number; case_id: string;
  actor: string | null; actor_name: string | null; text: string; path: string | null;
  waiting: boolean; ended: boolean; done: boolean;
}

export type StatusKey =
  | "ready" | "needs_clinician" | "not_met" | "preparing" | "waiting_clinician" | "returned"
  | "sending" | "waiting_payer" | "payer_question" | "approved" | "denied";

export interface Status { key: StatusKey; word: string }
export interface Due { words: string; sub: string; urgent: boolean; group: "today" | "tomorrow" | "later" }

export interface QueueRow {
  id: string;
  patient: { name: string; age: number };
  service: string;
  payer: string;
  due: Due;
  status: Status;
  expedited: boolean;
}

export interface Queue {
  now: string;
  counts: { open: number; due_today: number; approved_today: number; with_clinicians: number };
  groups: { key: "today" | "tomorrow" | "later"; title: string; cases: QueueRow[] }[];
  waiting: { id: string; patient: string; status: Status; since: string; next: string | null }[];
}

export interface Evidence { display: string; date: string | null; document_id: string | null; page: number | null; sentence: string | null }
/** A packet passage that may answer a criterion the record could not: where to look, never evidence. */
export interface Lead { document_id: string; page: number; title: string; text: string; start: number; end: number }
export interface CriterionResult {
  id: string; text: string; status: "met" | "unmet" | "unknown"; explanation: string; attested_by: string | null;
  policy_sentence: string; question: string | null; evidence: Evidence[]; leads: Lead[];
}

export interface CaseView {
  id: string;
  version?: number;
  state: string;
  status: Status;
  patient: { name: string; birth_date: string; age: number };
  service: { system: string; code: string; display: string; short: string };
  side: "right" | "left" | null;
  urgency: "standard" | "expedited";
  urgency_reason: string | null;
  payer: { name: string; member_id_last4: string };
  ordering_clinician: { id: string; name: string; specialty: string };
  received: string;
  due: Due;
  packet: { id: string; page: number; title: string }[];
  criteria: {
    title: string; version: string; source_kind: string; checked_at: string | null;
    source: { document: string; title: string; url: string | null; retrieved: string | null } | null;
    counts: { met: number; unmet: number; unknown: number }; total: number; results: CriterionResult[];
  };
  draft: {
    text: string; route: string; model: string | null; edited_by?: string; out_of_date?: boolean;
    fallback_reason?: string; deid_removed?: Record<string, number>; sent?: SentToModel;
  } | null;
  summary: string;
  actions: {
    can_approve: boolean; approve_blocked_reason: string | null; can_request_attestation: boolean; can_return: boolean;
    can_ask_for_note: boolean; can_send_note: boolean; can_send_appeal: boolean; can_edit_appeal: boolean;
    can_recall: boolean; can_resubmit: boolean; can_remind: boolean; can_set_urgency: boolean;
  };
  payer_clock: { applies: boolean; plan_type: string; decided?: boolean; within?: boolean; expedited?: boolean; words: string } | null;
  /** While the payer reviews a request it has: when it said it would decide, in words. */
  payer_review: { expected_by: string; words: string } | null;
  requirements: { matches: boolean; plan_type: string; checked_at: string } | null;
  appeal: {
    reason: string | null; criterion: string | null; policy_sentence: string | null; record: string | null;
    draft: { text: string; route: string; model: string | null; edited_by?: string; sent?: SentToModel } | null;
  } | null;
  undo: { action: string; until: string; seconds: number } | null;
  /** While the case waits on the ordering clinician: for how long, and the reminders sent, in words. */
  waiting: { since: string; for: string; reminders: number; last_reminded: string | null;
             next_reminder_at: string | null; words: string } | null;
  returned_reason: string | null;
  payer_response: { decision: { code: string; display: string; question?: string; authorization_number?: string } } | null;
  submission: { kind?: "reply" | "appeal"; sent?: boolean; send_after?: string; approved_by?: string; submission_id?: string } | null;
  reply: PayerReply | null;
  read_only: boolean;
  /** For a case created from a fax: who checked it against the page, when, and what they corrected. */
  came_by_fax: { from: string | null; by: string; at: string; corrected: string | null; asked_clinic: boolean; words: string } | null;
  /** For a case started from inside an EHR: which EHR, who started it, and when. */
  came_from_ehr: { from: string; by: string; at: string; words: string } | null;
}

/** Whether Caduceus can be opened from an EHR here, and the client id the launcher holds it to. */
export interface SmartInfo { enabled: boolean; client_id: string | null }

/** One service, and how much of its approved criteria the EHR's record answers before any case exists. */
export interface EhrService {
  code: string; label: string; display: string; total: number; met: number; unmet: number; unknown: number;
  clinician: string | null; side: string | null;
  /** "The record answers 1 of the 4 criteria." */
  lead: string;
  /** That, and who will be asked to confirm the rest. */
  answers: string;
}

/** A patient's record as an EHR shared it by SMART launch, in the visitor's own copy of the demo. */
export interface EhrRecordView {
  key: string;
  patient: { name: string; born: string; age: number; sex: string };
  source: { host: string; read: string; by: string };
  holds: { what: string; words: string }[];
  relevant: string[];
  reads: string;
  services: EhrService[];
  cases: { id: string; service: string; status: Status }[];
  payers: string[];
  read_only: boolean;
}

export interface TimelineEntry {
  first_seq: number; last_seq: number; hash: string; who: string; role: string; what: string;
  kind: "agent" | "person" | "view" | "refused"; tools: string[]; at: string; count: number;
}
export interface Timeline { events: number; can_verify: boolean; entries: TimelineEntry[] }

// Exactly what the draft's model saw: de-identified by construction, so it can be kept and shown.
export interface SentToModel {
  text: string; policy: string; text_sha256: string; vendor: string; cached: boolean;
  input_tokens: number; output_tokens: number; tokens_filled_in: number;
}

// A payer's request for more on a pended case, and where its answer stands.
export interface PayerReply {
  question: string | null;
  attested: { text: string; by: string; at: string }[];
  requested_by: string | null;
  note: { text: string; author: string; signed_at: string } | null;
  declined: { by: string; at: string } | null;
}

export interface Page {
  id: string; title: string; page: number; pages: number; date: string | null; author: string | null; text: string;
  image: string | null;  // a faxed page keeps its image; its text is what OCR read
}

/** A request on the clinician's list: how soon it is due, and any reminders. */
export interface AttestationRow {
  case_id: string; patient: { name: string; age: number }; service: string; kind: "attestation" | "note";
  due: Due; reminders: { count: number; by: string; last: string } | null;
}

export interface AttestationView {
  case_id: string;
  state: string;
  patient: { name: string; birth_date: string };
  service: string;
  ordered: string;
  chart_summary: string;
  /** Who wrote the summary: the model inside the network ("phi") or the record's facts, and if the facts stand
   * in for the model, why ("unsupported", "unreadable", "in_vpc_unavailable"). */
  chart_summary_by: { route: string | null; model: string | null; fallback_reason: string | null };
  /** True while the in-network model writes its summary; the page refreshes until it arrives. */
  chart_summary_pending?: boolean;
  questions: { criterion_id: string; question: string; help: string | null;
               leads: { title: string; page: number; text: string }[] }[];
  payer: string;
  requested_by: string;
  undo_until?: string | null;
  answers?: Record<string, string>;
  note_request: { question: string | null; confirmed: { question: string; policy: string; at: string }[]; limit: number } | null;
}

export interface FaxField {
  key: string; label: string; value: string; confidence: number;
  box: [number, number, number, number] | null; problem: string | null;
}
/** One line of the check-the-fax form, in page order, with the value the form starts on. */
export interface FormField {
  key: string; label: string; kind: "text" | "select" | "date"; read_as: string; initial: string;
  problem: string | null; confidence: number; box: [number, number, number, number] | null; at_clinic?: string[];
  /** A list of its own (the side), and the services it applies to; without them, a field always applies. */
  options?: { value: string; label: string }[]; for_services?: string[];
}
export interface FaxChoices {
  payers: string[];
  clinicians: { name: string; specialty: string; clinic: string | null }[];
  services: { code: string; label: string }[];
}
/** A record in the chart, as much as a person needs to know it is the right one; `ref` names it for this fax only. */
export interface ChartCard { ref: string; name: string; born: string; birth_date: string; city: string | null }
export interface PatientMatch {
  status: "matched" | "chosen" | "candidates" | "none";
  patient: ChartCard | null;
  on_file: { payer: string; member_id_last4: string } | null;
  candidates: ChartCard[];
}
/** What a person asked the sending clinic for; shown for them to send, never sent by Caduceus. */
export interface ClinicRequest {
  kind: "missing" | "resend"; missing: string[]; to: string | null; text: string; asked_by: string; asked: string;
  answered: string | null;
}
export interface FaxRow {
  id: string; status: "received" | "read" | "waiting_on_clinic" | "confirmed"; received: string; pages: number;
  referral: string | null; patient: string | null; to_check: number; clinic: string | null; case_id: string | null;
  request: ClinicRequest | null;
}
export interface FaxView extends FaxRow {
  fields: FaxField[]; size: [number, number]; engine: string; image: string;
  form: FormField[]; sender: { read_as: string; box: [number, number, number, number] | null }; choices: FaxChoices;
}

export interface Me { id: string; name: string; role: "pa_specialist" | "clinician" | "admin" | "auditor" }
