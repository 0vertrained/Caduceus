import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type ChartCard, type FaxChoices, type FaxView, type FormField, type PatientMatch } from "../api";
import { ErrorMessage, ErrorSummary, type FormError } from "../components/ErrorSummary";
import { Crop, FaxViewer, useAuthImage, WholePage } from "../components/FaxImage";
import { ChevronLeft, CheckCircle, Triangle } from "../components/Icons";
import { glue, midSentence, useResource, useTitle } from "../hooks";

type Parts = { month: string; day: string; year: string };
const PARTS = [["month", "Month"], ["day", "Day"], ["year", "Year"]] as const;

/** Each field's name for use mid-sentence. */
const NAMED: Record<string, string> = {
  referral: "referral number", patient_name: "patient’s name", birth_date: "date of birth", member_id: "member ID",
  payer: "health plan", clinician: "ordering clinician", service_code: "requested service", side: "side",
  diagnosis: "diagnosis",
};

/** "a", "a and b", "a, b and c" */
function andList(items: string[]): string {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

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
    case "side": return value ? null : ["Choose the side the request is for", here];
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
  if (f.options) {
    options = f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>);
  } else if (f.key === "payer") {
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
  const placeholder = { payer: "Choose a health plan", clinician: "Choose a clinician", service_code: "Choose a service",
                        side: "Choose a side" }[f.key];
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

const sameName = (a: string, b: string) => a.trim().toLowerCase().replace(/\s+/g, " ") === b.trim().toLowerCase().replace(/\s+/g, " ");

/** Where the fax and the record disagree on something the record holds: both, and a way to take the record's. */
function Differs({ label, fax, record, source, use, onUse }: {
  label: string; fax: string; record: string; source: string; use?: string; onUse?: () => void;
}) {
  return (
    <div className="differs">
      <p><strong>{label} differs.</strong> On the fax: {fax || "nothing"}. {source}: {record}.</p>
      {onUse && <button type="button" className="btn btn--secondary btn--start" onClick={onUse}>
        {use}<span className="visually-hidden"> for the {label.toLowerCase()}</span></button>}
    </div>
  );
}

/** The patient in the chart: the one record with this name and date of birth, confirmed by a person, or the closest
 *  few to choose from. Only what the record holds is compared. */
function PatientCheck({ match, looking, failure, name, born, confirmed, onLook, onConfirm, onUseName, onUseBorn }: {
  match: PatientMatch | null; looking: boolean; failure: string | null; name: string; born: string; confirmed: boolean;
  onLook: (ref?: string) => void; onConfirm: (ref: string | null) => void; onUseName: (c: ChartCard) => void;
  onUseBorn: (c: ChartCard) => void;
}) {
  const chart = match?.patient ?? null;
  const nameDiffers = chart ? !sameName(name, chart.name) : false;
  const bornDiffers = chart ? born !== chart.birth_date : false;
  const where = (c: ChartCard) => `born ${c.born}${c.city ? `, ${c.city}` : ""}`;
  let body: ReactNode;
  if (looking) {
    body = <p role="status" className="muted">Looking in the chart.</p>;
  } else if (!match) {
    body = (
      <>
        <p className="support">Look the patient up by the name and date of birth above, so the case is for the right person.</p>
        <button type="button" className="btn btn--secondary btn--start" onClick={() => onLook()}>Find the patient in the chart</button>
      </>
    );
  } else if (chart) {
    body = (
      <>
        <p className="match__who">{nameDiffers || bornDiffers ? "Is this the patient? " : "This is "}<strong>{chart.name}</strong>, {where(chart)}.</p>
        {nameDiffers && <Differs label="The name" fax={name} record={chart.name} source="In the chart"
                                 use="Use the chart’s value" onUse={() => onUseName(chart)} />}
        {bornDiffers && <Differs label="The date of birth" fax={born} record={chart.born} source="In the chart"
                                 use="Use the chart’s value" onUse={() => onUseBorn(chart)} />}
        {!nameDiffers && !bornDiffers && (confirmed ? (
          <p className="match__done"><span className="status status--seal"><CheckCircle size={20} /><span>Confirmed as the patient</span></span>{" "}
            <button type="button" className="link-button" onClick={() => onConfirm(null)}>Not the right person?</button></p>
        ) : (
          <button type="button" className="btn btn--secondary btn--start" onClick={() => onConfirm(chart.ref)}>Yes, this is the patient</button>
        ))}
        {!confirmed && <button type="button" className="link-button link-button--start" onClick={() => onLook()}>Look up again</button>}
      </>
    );
  } else if (match.candidates.length) {
    body = (
      <>
        <p>No one in the chart has exactly this name and date of birth. {match.candidates.length === 1
          ? "This is the closest. Is it the patient?" : "These are the closest. Is it one of them?"}</p>
        <ul className="match__list">
          {match.candidates.map((c) => (
            <li key={c.ref} className="match__item">
              <span><strong>{c.name}</strong>, {where(c)}</span>
              <button type="button" className="btn btn--secondary" onClick={() => onLook(c.ref)}>
                This is the patient<span className="visually-hidden">: {c.name}, {where(c)}</span></button>
            </li>
          ))}
        </ul>
        <button type="button" className="link-button link-button--start" onClick={() => onLook()}>Look up again</button>
      </>
    );
  } else {
    body = (
      <>
        <p>No one in the chart is close to this name and date of birth. Check both against the fax. If the fax does not
          give them clearly, ask the clinic.</p>
        <button type="button" className="btn btn--secondary btn--start" onClick={() => onLook()}>Look up again</button>
      </>
    );
  }
  return (
    <section className="match" id="patient-check" tabIndex={-1} aria-labelledby="match-title">
      <h2 id="match-title" className="match__title">The patient in the chart</h2>
      {body}
      {failure && <p role="alert" className="alert">{failure}</p>}
    </section>
  );
}

export function IntakePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, setData, error } = useResource<FaxView>(`/api/intake/${id}`);
  const image = useAuthImage(data?.image);
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [askFailure, setAskFailure] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const started = useRef<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [dob, setDob] = useState<Parts>({ month: "", day: "", year: "" });
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [raised, setRaised] = useState<FormError[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [match, setMatch] = useState<PatientMatch | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [lookFailure, setLookFailure] = useState<string | null>(null);
  const lookedUp = useRef<string | null>(null);
  useTitle("Check the fax");

  const lookUp = async (name: string, born: string, ref?: string) => {
    setLooking(true);
    setLookFailure(null);
    try {
      // The name goes in the request body, never the address: addresses reach logs.
      setMatch(await api<PatientMatch>(`/api/intake/${id}/match`, { method: "POST", body: JSON.stringify({ name, birth_date: born, ref }) }));
      setConfirmed(null);
    } catch (e) {
      setLookFailure((e as ApiError).message);
    } finally {
      setLooking(false);
    }
  };

  useEffect(() => {
    // The form starts once per fax; coming back from waiting on the clinic keeps what the person already did.
    if (!data?.form || started.current === data.id) return;
    started.current = data.id;
    const start = Object.fromEntries(data.form.map((f) => [f.key, f.initial]));
    setValues(start);
    setDob(split(start.birth_date ?? ""));
    setTicked(new Set());
  }, [data]);

  useEffect(() => {
    // Look the patient up once the fax can be checked (now, or when the clinic has answered), as the form has them,
    // when there is both a name and a whole date.
    if (!data?.form || data.status !== "read" || lookedUp.current === data.id) return;
    const start = Object.fromEntries(data.form.map((f) => [f.key, f.initial]));
    const name = values.patient_name ?? start.patient_name;
    const born = values.patient_name === undefined ? start.birth_date ?? "" : joined(dob);
    if (!name || !/^\d\d\/\d\d\/\d{4}$/.test(born)) return;
    lookedUp.current = data.id;
    void lookUp(name, born);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Opening the fax.</p></div>;

  const valueOf = (f: FormField) => (f.kind === "date" ? joined(dob) : values[f.key] ?? "");
  const changed = (f: FormField) => valueOf(f) !== f.initial;
  const isChecked = (f: FormField) => ticked.has(f.key) || changed(f);
  // A field for some services only (the side, for a knee replacement) applies once one of them is chosen.
  const form = data.form.filter((f) => !f.for_services || f.for_services.includes(values.service_code ?? ""));
  const flagged = form.filter((f) => f.problem);
  const done = flagged.filter(isChecked).length;
  const chart = match?.patient ?? null;
  const exact = chart !== null && sameName(values.patient_name ?? "", chart.name) && joined(dob) === chart.birth_date;
  const patientConfirmed = exact && confirmed === chart?.ref;
  const onFile = chart ? match?.on_file ?? null : null;

  const validate = (): FormError[] => {
    const found: FormError[] = [];
    if (missing.size) {
      const them = missing.size === 1 ? "it" : "them";
      found.push({ key: "missing", target: "ask-clinic", message: `You marked ${missing.size === 1 ? "a field" : `${missing.size} fields`} as not on the fax. Ask the clinic for ${them}, or say ${them === "it" ? "it is" : "they are"} on the fax` });
    }
    for (const f of form) {
      if (missing.has(f.key)) continue;
      const p = formatProblem(f, valueOf(f), dob);
      if (p) found.push({ key: f.key, message: p[0], target: p[1] });
      else if (f.problem && !isChecked(f)) {
        found.push({ key: f.key, target: `tick-${f.key}`,
                     message: `Check the ${NAMED[f.key]} against the fax, then tick “Matches the fax”, or correct it` });
      }
      if (f.key === "birth_date" && !patientConfirmed) {
        found.push({ key: "patient", target: "patient-check", message: !chart ? "Find the patient in the chart, and confirm it is them"
          : exact ? "Confirm the patient in the chart" : "Make the name and date of birth match the chart, or check them against the fax" });
      }
    }
    return found;
  };
  // What the last attempt found, less anything fixed since. A server's refusal stays until the next attempt.
  const stillWrong = validate();
  const errors = raised.filter((e) => e.server || stillWrong.some((w) => w.key === e.key));
  const errorFor = (key: string) => errors.find((e) => e.key === key);

  const create = async () => {
    const found = validate();
    setRaised(found);
    setAttempt((n) => n + 1);
    if (found.length) return;
    setBusy(true);
    try {
      const body = { values: Object.fromEntries(form.map((f) => [f.key, valueOf(f)])),
                     checked: flagged.filter((f) => ticked.has(f.key)).map((f) => f.key) };
      const made = await api<{ case_id: string }>(`/api/intake/${data.id}/confirm`, { method: "POST", body: JSON.stringify(body) });
      navigate(`/cases/${made.case_id}`);
    } catch (e) {
      const err = e as ApiError;
      const f = data.form.find((x) => x.key === err.refusal.field);
      const at = f && (err.message.includes("Matches the fax") ? `tick-${f.key}` : f.kind === "date" ? "dob-month" : `fax-${f.key}`);
      setRaised([{ key: f?.key ?? "form", message: err.message, target: at, caseId: err.refusal.case_id, server: true }]);
      setAttempt((n) => n + 1);
      setBusy(false);
    }
  };

  const toggle = (key: string, on: boolean) => {
    const next = new Set(ticked);
    if (on) next.add(key); else next.delete(key);
    setTicked(next);
  };
  const markMissing = (key: string, on: boolean) => {
    const next = new Set(missing);
    if (on) next.add(key); else next.delete(key);
    setMissing(next);
  };

  // Ask the clinic that sent the fax for what it does not give clearly, or to send it all again. Caduceus writes the
  // request and holds the fax; a person sends the request.
  const askClinic = async (resend: boolean) => {
    setAskFailure(null);
    const referral = values.referral && /^\d{4}-\d{4}$/.test(values.referral) ? values.referral : undefined;
    try {
      const view = await api<FaxView>(`/api/intake/${data.id}/ask-clinic`, { method: "POST",
        body: JSON.stringify({ missing: resend ? [] : data.form.filter((f) => missing.has(f.key)).map((f) => f.key), resend, referral }) });
      setMissing(new Set());
      setRaised([]);
      setData(view);
      window.scrollTo(0, 0);
    } catch (e) {
      setAskFailure((e as ApiError).message);
    }
  };
  const clinicSent = async () => {
    setAskFailure(null);
    try {
      setData(await api<FaxView>(`/api/intake/${data.id}/clinic-sent`, { method: "POST" }));
    } catch (e) {
      setAskFailure((e as ApiError).message);
    }
  };
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied("Copied. Paste it into a fax cover sheet or an email to the clinic.");
    } catch {
      setCopied("This browser would not copy it. Select the text and copy it instead.");
    }
  };
  const asking = data.form.filter((f) => missing.has(f.key));
  const request = data.status === "waiting_on_clinic" ? data.request : null;

  return (
    <div className="page intake">
      <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
      <ErrorSummary errors={errors} attempt={attempt} />
      <div className="intake__intro">
        <h1 className="page-title">Check the fax</h1>
        <p className="lead">{data.status === "confirmed" ? "This fax is already a case." : request
          ? "This fax is waiting on the clinic that sent it."
          : flagged.length
          ? `${flagged.length === 1 ? "One field needs" : `${flagged.length} fields need`} your eyes. Each is shown beside the part of the page it came from. Tick each one that matches the fax, or correct it.`
          : "Every field was read clearly. Look them over, then create the case."}</p>
        <p className="support">{data.clinic ? `From ${data.clinic}, received ${data.received}. ` : `Received ${data.received}; the clinic that sent it could not be read. `}
          Read by {data.engine} inside Mesa Verde’s systems; the page never left. Fields below 90% confidence, or that
          do not look right, are sent to you.</p>
      </div>
      <FaxViewer src={image} alt={`The faxed cover sheet, page 1 of ${data.pages}`} />
      {data.status === "confirmed" && data.case_id ? <Link to={`/cases/${data.case_id}`} className="btn btn--primary btn--start">Open the case</Link> : request ? (
        <section className="panel callout asked" aria-labelledby="asked-title">
          <h2 id="asked-title" className="section-title">Waiting on {request.to ?? "the clinic"}</h2>
          <p>{request.asked_by} asked {glue(request.asked)} {request.kind === "resend"
            ? "for the whole referral again, because the copy was too poor to read."
            : `for ${andList(request.missing.map((m) => `the ${midSentence(m)}`))}.`}</p>
          <figure className="asked__request">
            <figcaption className="support">The request, ready to send by fax or phone. Caduceus has not sent it
              anywhere; it is recorded in the fax’s history.</figcaption>
            <div className="paper">{request.text}</div>
          </figure>
          <p>When the clinic answers, choose “The clinic sent it”, then finish checking the fax here.</p>
          <div className="asked__actions">
            <button type="button" className="btn btn--secondary" onClick={() => void copy(request.text)}>Copy the request</button>
            <button type="button" className="btn btn--primary" onClick={() => void clinicSent()}>The clinic sent it</button>
          </div>
          {copied && <p role="status" className="support">{copied}</p>}
          {askFailure && <p role="alert" className="alert">{askFailure}</p>}
        </section>
      ) : (
        <form className="panel intake__form" noValidate onSubmit={(e) => { e.preventDefault(); void create(); }}>
          {flagged.length > 0 && (
            <p className="intake__counter" role="status"><CheckCircle size={20} />{done} of {flagged.length} checked</p>
          )}
          {form.map((f) => {
            const err = errorFor(f.key);
            const errId = `err-${f.key}`;
            const describedBy = [`why-${f.key}`, err && err.target !== `tick-${f.key}` ? errId : ""].filter(Boolean).join(" ");
            const invalid = Boolean(err && err.target !== `tick-${f.key}`);
            // The line it was read from, or the whole page when the line could not be found at all.
            const named = NAMED[f.key === "side" ? "diagnosis" : f.key];  // the side is read from the diagnosis
            const crop = f.box ? <Crop src={image} size={data.size} box={f.box} label={named} />
              : <WholePage src={image} label={named} />;
            const memberId = values.member_id ?? "";
            const absent = missing.has(f.key);
            return (
              <Fragment key={f.key}>
              <div className={`intake__field${f.problem ? " intake__field--check" : ""}${err ? " intake__field--error" : ""}`}>
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
                {f.key === "payer" && onFile && values.payer && values.payer !== onFile.payer && (
                  <Differs label="The health plan" fax={values.payer} record={onFile.payer} source="On file for this patient"
                           use="Use the plan on file" onUse={() => setValues({ ...values, payer: onFile.payer })} />
                )}
                {f.key === "member_id" && onFile && /^[A-Z]{2}\d{9}$/.test(memberId) && memberId.slice(-4) !== onFile.member_id_last4 && (
                  <div className="differs"><p><strong>The member ID differs.</strong> This one ends in {memberId.slice(-4)}; the
                    one on file for this patient ends in {onFile.member_id_last4}. Check it against the fax.</p></div>
                )}
                {f.problem && (absent ? (
                  <p className="intake__absent"><Triangle size={18} /><span>Not on the fax. The clinic will be asked for
                    it below. <button type="button" className="link-button" onClick={() => markMissing(f.key, false)}>It
                    is on the fax<span className="visually-hidden">: the {NAMED[f.key]}</span></button></span></p>
                ) : (
                  <div className="intake__tick">
                    <label className={`tick${ticked.has(f.key) ? " tick--on" : ""}`} htmlFor={`tick-${f.key}`}>
                      <input type="checkbox" id={`tick-${f.key}`} checked={ticked.has(f.key)}
                             aria-describedby={err?.target === `tick-${f.key}` ? errId : undefined}
                             onChange={(e) => toggle(f.key, e.target.checked)} />
                      <span>Matches the fax<span className="visually-hidden">: {NAMED[f.key]}</span></span>
                    </label>
                    {changed(f) && !ticked.has(f.key) ? <span className="support">You changed this, so it counts as checked.</span>
                      : !ticked.has(f.key) && (
                        <button type="button" className="link-button" onClick={() => markMissing(f.key, true)}>
                          Not on the fax: ask the clinic<span className="visually-hidden"> for the {NAMED[f.key]}</span></button>
                      )}
                  </div>
                ))}
              </div>
              {f.key === "birth_date" && (
                <PatientCheck match={match} looking={looking} failure={lookFailure} name={values.patient_name ?? ""}
                              born={joined(dob)} confirmed={patientConfirmed}
                              onLook={(ref) => void lookUp(values.patient_name ?? "", joined(dob), ref)}
                              onConfirm={setConfirmed}
                              onUseName={(c) => setValues({ ...values, patient_name: c.name })}
                              onUseBorn={(c) => setDob(split(c.birth_date))} />
              )}
              </Fragment>
            );
          })}
          {asking.length > 0 && (
            <section className="ask" id="ask-clinic" tabIndex={-1} aria-labelledby="ask-title">
              <h2 id="ask-title" className="match__title">Ask {data.clinic ?? "the clinic that sent it"}</h2>
              <p>The fax does not clearly give:</p>
              <ul className="ask__list">{asking.map((f) => <li key={f.key}>{f.label}</li>)}</ul>
              <p className="support">Caduceus writes the request, naming the referral and {asking.length === 1 ? "this field" : "these fields"} but
                not the patient. You send it by fax or phone; nothing is sent for you. The fax waits here until the clinic answers.</p>
              <button type="button" className="btn btn--primary btn--start" onClick={() => void askClinic(false)}>
                Ask the clinic for {asking.length === 1 ? "it" : `these ${asking.length}`}</button>
            </section>
          )}
          {askFailure && <p role="alert" className="alert">{askFailure}</p>}
          <div className="intake__actions">
            {/* One filled button per view: asking the clinic takes it while fields are marked as not on the fax. */}
            <button type="submit" className={`btn ${asking.length ? "btn--secondary" : "btn--primary"} btn--large`} disabled={busy}>Create the case</button>
            <span className="support">{flagged.length ? `${done} of ${flagged.length} checked. ` : ""}Caduceus matches
              the patient in the record, then checks the case against the payer’s policy.</span>
          </div>
          <p className="intake__resend">
            <button type="button" className="btn--text" onClick={() => void askClinic(true)}>Too poor to read: ask the clinic to resend</button>
          </p>
        </form>
      )}
    </div>
  );
}
