import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type FaxChoices, type FaxView, type FormField } from "../api";
import { ErrorMessage, ErrorSummary, type FormError } from "../components/ErrorSummary";
import { Crop, useAuthImage } from "../components/FaxImage";
import { ChevronLeft, CheckCircle, Triangle } from "../components/Icons";
import { useResource, useTitle } from "../hooks";

type Parts = { month: string; day: string; year: string };
const PARTS = [["month", "Month"], ["day", "Day"], ["year", "Year"]] as const;

/** Each field's name for use mid-sentence. */
const NAMED: Record<string, string> = {
  referral: "referral number", patient_name: "patient’s name", birth_date: "date of birth", member_id: "member ID",
  payer: "health plan", clinician: "ordering clinician", service_code: "requested service", diagnosis: "diagnosis",
};

function split(date: string): Parts {
  const [month = "", day = "", year = ""] = date ? date.split("/") : [];
  return { month, day, year };
}

/** The three boxes as one date, MM/DD/YYYY, the way the referral writes it. */
function joined(p: Parts): string {
  if (!p.month.trim() && !p.day.trim() && !p.year.trim()) return "";
  const pad = (s: string) => (/^\d$/.test(s.trim()) ? `0${s.trim()}` : s.trim());
  return `${pad(p.month)}/${pad(p.day)}/${p.year.trim()}`;
}

/** GOV.UK's date messages: what is missing first, then whether it is a real date in the past. */
function dateProblem(p: Parts): [string, string] | null {
  const missing = PARTS.filter(([k]) => !p[k].trim()).map(([k]) => k);
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
function formatProblem(f: FormField, value: string, dob: Parts): [string, string] | null {
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
    case "diagnosis": return /^[A-Z]\d{2}\.\d+ \S/.test(value) ? null
      : ["Enter the diagnosis starting with its ICD-10 code, like M54.50 Low back pain", here];
    default: return null;
  }
}

function Why({ f }: { f: FormField }) {
  return f.problem
    ? <p className="intake__why" id={`why-${f.key}`}><Triangle size={18} />{f.problem}</p>
    : <p className="intake__why intake__why--ok" id={`why-${f.key}`}><CheckCircle size={18} />Read clearly ({Math.round(f.confidence * 100)}%)</p>;
}

function ReadAs({ f }: { f: FormField }) {
  if (!f.read_as) return null;
  const nearest = f.kind === "select" && f.problem && f.initial;
  return <p className="support intake__read-as">Read as “{f.read_as}”.{nearest ? " The list starts on the closest one on file." : ""}</p>;
}

/** The list a field is chosen from: health plans, clinicians (those at the sending clinic first), or services. */
function Choice({ f, choices, value, onChange, describedBy, invalid, clinic }: {
  f: FormField; choices: FaxChoices; value: string; onChange: (v: string) => void; describedBy: string;
  invalid: boolean; clinic: string | null;
}) {
  const [every, setEvery] = useState(false);
  const atClinic = f.at_clinic ?? [];
  const narrowed = f.key === "clinician" && atClinic.length > 0 && !every && (!value || atClinic.includes(value));
  let options: ReactNode;
  if (f.key === "payer") {
    options = choices.payers.map((p) => <option key={p} value={p}>{p}</option>);
  } else if (f.key === "service_code") {
    options = choices.services.map((s) => <option key={s.code} value={s.code}>{s.label}</option>);
  } else if (narrowed) {
    options = choices.clinicians.filter((c) => atClinic.includes(c.name))
      .map((c) => <option key={c.name} value={c.name}>{c.name}, {c.specialty}</option>);
  } else {
    const clinics = [...new Set(choices.clinicians.map((c) => c.clinic ?? "Other clinicians on file"))].sort();
    options = clinics.map((name) => (
      <optgroup key={name} label={name}>
        {choices.clinicians.filter((c) => (c.clinic ?? "Other clinicians on file") === name)
          .map((c) => <option key={c.name} value={c.name}>{c.name}, {c.specialty}</option>)}
      </optgroup>
    ));
  }
  const placeholder = { payer: "Choose a health plan", clinician: "Choose a clinician", service_code: "Choose a service" }[f.key];
  return (
    <>
      <select id={`fax-${f.key}`} className="input select" value={value} aria-describedby={describedBy}
              aria-invalid={invalid || undefined} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options}
      </select>
      <ReadAs f={f} />
      {f.key === "clinician" && (narrowed ? (
        <p className="support">Showing the clinicians at {clinic}, where the fax came from.{" "}
          <button type="button" className="link-button" onClick={() => setEvery(true)}>Show every clinician</button></p>
      ) : !clinic ? <p className="support">The clinic that sent the fax could not be read, so every clinician on file is listed.</p> : null)}
    </>
  );
}

export function IntakePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, error } = useResource<FaxView>(`/api/intake/${id}`);
  const image = useAuthImage(data?.image);
  const [values, setValues] = useState<Record<string, string>>({});
  const [dob, setDob] = useState<Parts>({ month: "", day: "", year: "" });
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<FormError[]>([]);
  const [busy, setBusy] = useState(false);
  useTitle("Check the fax");

  useEffect(() => {
    if (!data?.form) return;
    setValues(Object.fromEntries(data.form.map((f) => [f.key, f.initial])));
    setDob(split(data.form.find((f) => f.key === "birth_date")?.initial ?? ""));
    setTicked(new Set());
  }, [data]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Opening the fax.</p></div>;

  const valueOf = (f: FormField) => (f.kind === "date" ? joined(dob) : values[f.key] ?? "");
  const changed = (f: FormField) => valueOf(f) !== f.initial;
  const isChecked = (f: FormField) => ticked.has(f.key) || changed(f);
  const flagged = data.form.filter((f) => f.problem);
  const done = flagged.filter(isChecked).length;
  const errorFor = (key: string) => errors.find((e) => e.key === key);

  const validate = (): FormError[] => {
    const found: FormError[] = [];
    for (const f of data.form) {
      const p = formatProblem(f, valueOf(f), dob);
      if (p) found.push({ key: f.key, message: p[0], target: p[1] });
      else if (f.problem && !isChecked(f)) {
        found.push({ key: f.key, target: `tick-${f.key}`,
                     message: `Check the ${NAMED[f.key]} against the fax, then tick “Matches the fax”, or correct it` });
      }
    }
    return found;
  };

  const create = async () => {
    const found = validate();
    setErrors(found);
    if (found.length) return;
    setBusy(true);
    try {
      const body = { values: Object.fromEntries(data.form.map((f) => [f.key, valueOf(f)])),
                     checked: flagged.filter((f) => ticked.has(f.key)).map((f) => f.key) };
      const made = await api<{ case_id: string }>(`/api/intake/${data.id}/confirm`, { method: "POST", body: JSON.stringify(body) });
      navigate(`/cases/${made.case_id}`);
    } catch (e) {
      const err = e as ApiError;
      const f = data.form.find((x) => x.key === err.refusal.field);
      const at = f && (err.message.includes("Matches the fax") ? `tick-${f.key}` : f.kind === "date" ? "dob-month" : `fax-${f.key}`);
      setErrors([{ key: f?.key ?? "form", message: err.message, target: at, caseId: err.refusal.case_id }]);
      setBusy(false);
    }
  };

  const toggle = (key: string, on: boolean) => {
    const next = new Set(ticked);
    if (on) next.add(key); else next.delete(key);
    setTicked(next);
  };

  return (
    <div className="page intake">
      <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
      <ErrorSummary errors={errors} />
      <div className="intake__intro">
        <h1 className="page-title">Check the fax</h1>
        <p className="lead">{data.status === "confirmed" ? "This fax is already a case." : flagged.length
          ? `${flagged.length === 1 ? "One field needs" : `${flagged.length} fields need`} your eyes. Each is shown beside the part of the page it came from. Tick each one that matches the fax, or correct it.`
          : "Every field was read clearly. Look them over, then create the case."}</p>
        <p className="support">{data.clinic ? `From ${data.clinic}, received ${data.received}. ` : `Received ${data.received}; the clinic that sent it could not be read. `}
          Read by {data.engine} inside Mesa Verde’s systems; the page never left. Fields below 90% confidence, or that
          do not look right, are sent to you.</p>
      </div>
      {data.status === "confirmed" && data.case_id ? <Link to={`/cases/${data.case_id}`} className="btn btn--primary btn--start">Open the case</Link> : (
        <form className="panel intake__form" noValidate onSubmit={(e) => { e.preventDefault(); void create(); }}>
          {flagged.length > 0 && (
            <p className="intake__counter" role="status"><CheckCircle size={20} />{done} of {flagged.length} checked</p>
          )}
          {data.form.map((f) => {
            const err = errorFor(f.key);
            const errId = `err-${f.key}`;
            const describedBy = [`why-${f.key}`, err && err.target !== `tick-${f.key}` ? errId : ""].filter(Boolean).join(" ");
            const invalid = Boolean(err && err.target !== `tick-${f.key}`);
            const crop = <Crop src={image} size={data.size} box={f.box} label={NAMED[f.key]} />;
            return (
              <div key={f.key} className={`intake__field${f.problem ? " intake__field--check" : ""}${err ? " intake__field--error" : ""}`}>
                {f.kind === "date" ? (
                  <fieldset className="fieldset intake__date" aria-describedby={`hint-dob ${describedBy}`}>
                    <legend className="field-label">{f.label}</legend>
                    <p className="support" id="hint-dob">For example, 03 09 1941</p>
                    <Why f={f} />
                    {f.problem && crop}
                    <ErrorMessage id={errId} message={err?.message} />
                    <div className="date-input">
                      {PARTS.map(([k, label]) => (
                        <div key={k} className="date-input__item">
                          <label className="date-input__label" htmlFor={`dob-${k}`}>{label}</label>
                          <input id={`dob-${k}`} className={`input date-input__${k}`} inputMode="numeric" autoComplete="off"
                                 value={dob[k]} aria-invalid={invalid || undefined}
                                 onChange={(e) => setDob({ ...dob, [k]: e.target.value })} />
                        </div>
                      ))}
                    </div>
                    {f.read_as && joined(split(f.initial)) !== f.read_as && <ReadAs f={f} />}
                  </fieldset>
                ) : (
                  <>
                    <label className="field-label" htmlFor={`fax-${f.key}`}>{f.label}</label>
                    <Why f={f} />
                    {f.problem && crop}
                    <ErrorMessage id={errId} message={err?.message} />
                    {f.kind === "select" ? (
                      <Choice f={f} choices={data.choices} value={values[f.key] ?? ""} describedBy={describedBy}
                              invalid={invalid} clinic={data.clinic} onChange={(v) => setValues({ ...values, [f.key]: v })} />
                    ) : (
                      <input id={`fax-${f.key}`} className="input" value={values[f.key] ?? ""} autoComplete="off" spellCheck={false}
                             aria-describedby={describedBy} aria-invalid={invalid || undefined}
                             onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                    )}
                  </>
                )}
                {f.problem && (
                  <div className="intake__tick">
                    <label className={`tick${ticked.has(f.key) ? " tick--on" : ""}`} htmlFor={`tick-${f.key}`}>
                      <input type="checkbox" id={`tick-${f.key}`} checked={ticked.has(f.key)}
                             aria-describedby={err?.target === `tick-${f.key}` ? errId : undefined}
                             onChange={(e) => toggle(f.key, e.target.checked)} />
                      <span>Matches the fax<span className="visually-hidden">: {NAMED[f.key]}</span></span>
                    </label>
                    {changed(f) && !ticked.has(f.key) && <span className="support">You changed this, so it counts as checked.</span>}
                  </div>
                )}
              </div>
            );
          })}
          <div className="intake__actions">
            <button type="submit" className="btn btn--primary btn--large" disabled={busy}>Create the case</button>
            <span className="support">{flagged.length ? `${done} of ${flagged.length} checked. ` : ""}Caduceus matches
              the patient in the record, then checks the case against the payer’s policy.</span>
          </div>
        </form>
      )}
    </div>
  );
}
