import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type FaxView, type FormField } from "../api";
import { ErrorSummary, type FormError } from "../components/ErrorSummary";
import { FaxViewer, useAuthImage } from "../components/FaxImage";
import { ChevronLeft, CheckCircle } from "../components/Icons";
import { AskClinic } from "../components/intake/AskClinic";
import { FaxField } from "../components/intake/FaxField";
import { formatProblem, joinDate, NAMED, sameName, splitDate, type DateParts } from "../components/intake/fields";
import { PatientCheck } from "../components/intake/PatientCheck";
import { usePatientMatch } from "../components/intake/usePatientMatch";
import { WaitingOnClinic } from "../components/intake/WaitingOnClinic";
import { useResource, useTitle } from "../hooks";

/** A copy of the set, with the key added or taken out. */
function toggled(set: Set<string>, key: string, on: boolean): Set<string> {
  const next = new Set(set);
  if (on) next.add(key); else next.delete(key);
  return next;
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
  const [dob, setDob] = useState<DateParts>({ month: "", day: "", year: "" });
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [raised, setRaised] = useState<FormError[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const { match, confirmed, setConfirmed, looking, failure: lookFailure, lookUp } = usePatientMatch(id);
  const lookedUp = useRef<string | null>(null);
  useTitle("Check the fax");

  useEffect(() => {
    // The form starts once per fax; coming back from waiting on the clinic keeps what the person already did.
    if (!data?.form || started.current === data.id) return;
    started.current = data.id;
    const start = Object.fromEntries(data.form.map((f) => [f.key, f.initial]));
    setValues(start);
    setDob(splitDate(start.birth_date ?? ""));
    setTicked(new Set());
  }, [data]);

  useEffect(() => {
    // Look the patient up once the fax can be checked (now, or when the clinic has answered), as the form has them,
    // when there is both a name and a whole date.
    if (!data?.form || data.status !== "read" || lookedUp.current === data.id) return;
    const start = Object.fromEntries(data.form.map((f) => [f.key, f.initial]));
    const name = values.patient_name ?? start.patient_name;
    const born = values.patient_name === undefined ? start.birth_date ?? "" : joinDate(dob);
    if (!name || !/^\d\d\/\d\d\/\d{4}$/.test(born)) return;
    lookedUp.current = data.id;
    void lookUp(name, born);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Opening the fax.</p></div>;

  const valueOf = (f: FormField) => (f.kind === "date" ? joinDate(dob) : values[f.key] ?? "");
  const changed = (f: FormField) => valueOf(f) !== f.initial;
  const isChecked = (f: FormField) => ticked.has(f.key) || changed(f);
  // A field for some services only (the side, for a knee replacement) applies once one of them is chosen.
  const form = data.form.filter((f) => !f.for_services || f.for_services.includes(values.service_code ?? ""));
  const flagged = form.filter((f) => f.problem);
  const done = flagged.filter(isChecked).length;
  const chart = match?.patient ?? null;
  const exact = chart !== null && sameName(values.patient_name ?? "", chart.name) && joinDate(dob) === chart.birth_date;
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
        <WaitingOnClinic request={request} copied={copied} failure={askFailure}
                         onCopy={() => void copy(request.text)} onAnswered={() => void clinicSent()} />
      ) : (
        <form className="panel intake__form" noValidate onSubmit={(e) => { e.preventDefault(); void create(); }}>
          {flagged.length > 0 && (
            <p className="intake__counter" role="status"><CheckCircle size={20} />{done} of {flagged.length} checked</p>
          )}
          {form.map((f) => (
            <Fragment key={f.key}>
              <FaxField f={f} fax={data} image={image} error={errorFor(f.key)} onFile={onFile}
                        value={values[f.key] ?? ""} onValue={(v) => setValues({ ...values, [f.key]: v })}
                        dob={dob} onDob={setDob} ticked={ticked.has(f.key)} changed={changed(f)}
                        onTick={(on) => setTicked(toggled(ticked, f.key, on))}
                        absent={missing.has(f.key)} onAbsent={(on) => setMissing(toggled(missing, f.key, on))} />
              {f.key === "birth_date" && (
                <PatientCheck match={match} looking={looking} failure={lookFailure} name={values.patient_name ?? ""}
                              born={joinDate(dob)} confirmed={patientConfirmed}
                              onLook={(ref) => void lookUp(values.patient_name ?? "", joinDate(dob), ref)}
                              onConfirm={setConfirmed}
                              onUseName={(c) => setValues({ ...values, patient_name: c.name })}
                              onUseBorn={(c) => setDob(splitDate(c.birth_date))} />
              )}
            </Fragment>
          ))}
          {asking.length > 0 && <AskClinic asking={asking} clinic={data.clinic} onAsk={() => void askClinic(false)} />}
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
