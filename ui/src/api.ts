// Thin client for the Caduceus API. Development sign-in sends the chosen roster user in a header;
// the deployed system sends an OIDC bearer token instead (same endpoints).

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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const user = currentUser();
  if (user) headers.set("X-Caduceus-User", user);
  headers.set("X-Caduceus-Sandbox", sandboxId());
  if (init.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let message = "Something went wrong. Try again in a moment.";
    try {
      const body = await res.json();
      message = body.message ?? body.detail ?? message;
    } catch {
      /* keep the default message */
    }
    throw new ApiError(res.status, message);
  }
  if (init.method && init.method !== "GET") window.dispatchEvent(new Event(CHANGED));
  return res.json() as Promise<T>;
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
}

export interface Queue {
  now: string;
  counts: { open: number; due_today: number; approved_today: number; with_clinicians: number };
  groups: { key: "today" | "tomorrow" | "later"; title: string; cases: QueueRow[] }[];
  waiting: { id: string; patient: string; status: Status; since: string }[];
}

export interface Evidence { display: string; date: string | null; document_id: string | null; page: number | null; sentence: string | null }
export interface CriterionResult {
  id: string; text: string; status: "met" | "unmet" | "unknown"; explanation: string; attested_by: string | null;
  policy_sentence: string; question: string | null; evidence: Evidence[];
}

export interface CaseView {
  id: string;
  version?: number;
  state: string;
  status: Status;
  patient: { name: string; birth_date: string; age: number };
  service: { system: string; code: string; display: string; short: string };
  payer: { name: string; member_id_last4: string };
  ordering_clinician: { id: string; name: string; specialty: string };
  received: string;
  due: Due;
  packet: { id: string; page: number; title: string }[];
  criteria: {
    title: string; version: string; source_kind: string; checked_at: string | null;
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
  };
  payer_clock: { applies: boolean; plan_type: string; decided?: boolean; within?: boolean; words: string } | null;
  requirements: { matches: boolean; plan_type: string; checked_at: string } | null;
  appeal: {
    reason: string | null; criterion: string | null; policy_sentence: string | null; record: string | null;
    draft: { text: string; route: string; model: string | null; edited_by?: string; sent?: SentToModel } | null;
  } | null;
  undo: { action: string; until: string; seconds: number } | null;
  returned_reason: string | null;
  payer_response: { decision: { code: string; display: string; question?: string; authorization_number?: string } } | null;
  submission: { kind?: "reply" | "appeal"; sent?: boolean; send_after?: string; approved_by?: string; submission_id?: string } | null;
  reply: PayerReply | null;
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

export interface Page { id: string; title: string; page: number; pages: number; date: string | null; author: string | null; text: string }

export interface AttestationView {
  case_id: string;
  state: string;
  patient: { name: string; birth_date: string };
  service: string;
  ordered: string;
  chart_summary: string;
  questions: { criterion_id: string; question: string; help: string | null }[];
  payer: string;
  requested_by: string;
  undo_until?: string | null;
  answers?: Record<string, string>;
  note_request: { question: string | null; confirmed: { question: string; policy: string; at: string }[]; limit: number } | null;
}

export interface Me { id: string; name: string; role: "pa_specialist" | "clinician" | "admin" | "auditor" }
