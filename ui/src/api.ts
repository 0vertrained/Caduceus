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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const user = currentUser();
  if (user) headers.set("X-Caduceus-User", user);
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
  return res.json() as Promise<T>;
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
  draft: { text: string; route: string; model: string | null; edited_by?: string; out_of_date?: boolean } | null;
  summary: string;
  actions: { can_approve: boolean; approve_blocked_reason: string | null; can_request_attestation: boolean; can_return: boolean };
  undo: { action: string; until: string; seconds: number } | null;
  returned_reason: string | null;
  payer_response: { decision: { code: string; display: string; question?: string; authorization_number?: string } } | null;
  submission: { sent?: boolean; send_after?: string; approved_by?: string } | null;
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
}

export interface Me { id: string; name: string; role: "pa_specialist" | "clinician" | "admin" | "auditor" }
