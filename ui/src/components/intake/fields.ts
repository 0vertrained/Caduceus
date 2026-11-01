import type { FormField } from "../../api";

export type DateParts = { month: string; day: string; year: string };
export const DATE_PARTS = [["month", "Month"], ["day", "Day"], ["year", "Year"]] as const;

/** Each field's name for use mid-sentence. */
export const NAMED: Record<string, string> = {
  referral: "referral number", patient_name: "patient’s name", birth_date: "date of birth", member_id: "member ID",
  payer: "health plan", clinician: "ordering clinician", service_code: "requested service", side: "side",
  diagnosis: "diagnosis",
};

export function splitDate(date: string): DateParts {
  const [month = "", day = "", year = ""] = date ? date.split("/") : [];
  return { month, day, year };
}

/** The three boxes as one date, MM/DD/YYYY, the way the referral writes it. */
export function joinDate(p: DateParts): string {
  if (!p.month.trim() && !p.day.trim() && !p.year.trim()) return "";
  const pad = (s: string) => (/^\d$/.test(s.trim()) ? `0${s.trim()}` : s.trim());
  return `${pad(p.month)}/${pad(p.day)}/${p.year.trim()}`;
}

/** GOV.UK's date messages: what is missing first, then whether it is a real date in the past. */
function dateProblem(p: DateParts): [string, string] | null {
  const missing = DATE_PARTS.filter(([k]) => !p[k].trim()).map(([k]) => k);
  if (missing.length === 3) return ["Enter the date of birth", "dob-month"];
  if (missing.length) return [`Date of birth must include a ${missing.join(" and a ")}`, `dob-${missing[0]}`];
  const [m, d, y] = [p.month.trim(), p.day.trim(), p.year.trim()];
  const when = new Date(Number(y), Number(m) - 1, Number(d));
  if (!/^\d{1,2}$/.test(m) || !/^\d{1,2}$/.test(d) || !/^\d{4}$/.test(y)
      || when.getMonth() !== Number(m) - 1 || when.getDate() !== Number(d)) {
    return ["Date of birth must be a real date", "dob-month"];
  }
  if (when > new Date()) return ["Date of birth must be in the past", "dob-year"];
  return null;
}

/** What is wrong with a value's form, in the words the server's checks use. */
export function formatProblem(f: FormField, value: string, dob: DateParts): [string, string] | null {
  const here = `fax-${f.key}`;
  switch (f.key) {
    case "referral": return /^\d{4}-\d{4}$/.test(value) ? null : ["Enter the referral number as the fax gives it, like 2026-1029", here];
    case "patient_name": return value ? null : ["Enter the patient’s name", here];
    case "birth_date": return dateProblem(dob);
    case "member_id": return /^[A-Z]{2}\d{9}$/.test(value) ? null
      : ["Enter the member ID as 2 capital letters and 9 digits, like MV084522654", here];
    case "payer": return value ? null : ["Choose the health plan", here];
    case "clinician": return value ? null : ["Choose the ordering clinician", here];
    case "service_code": return value ? null : ["Choose the requested service", here];
    case "side": return value ? null : ["Choose the side the request is for", here];
    case "diagnosis": return /^[A-Z]\d{2}\.\d+ \S/.test(value) ? null
      : ["Enter the diagnosis starting with its ICD-10 code, like M54.50 Low back pain", here];
    default: return null;
  }
}

export const sameName = (a: string, b: string) => a.trim().toLowerCase().replace(/\s+/g, " ") === b.trim().toLowerCase().replace(/\s+/g, " ");
