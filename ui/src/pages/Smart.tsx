import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, type EhrRecordView, type EhrService } from "../api";
import { ErrorMessage, ErrorSummary, outstanding, type FormError } from "../components/ErrorSummary";
import { CheckCircle, ChevronLeft, Clock, CrossCircle, Triangle } from "../components/Icons";
import { StatusWord } from "../components/Status";
import { glue, useResource, useTitle } from "../hooks";
import { navigable } from "../smart";

// A launch from the EHR, in three screens: /smart/launch (the EHR opened Caduceus), /smart/callback (the EHR sent the
// browser back), and /smart/record/:key (the record it shared, and a case started from it). The browser only ever
// talks to Caduceus's own /api; the EHR's sign-in page and Caduceus are reached by navigating.

const UNREACHABLE = "Caduceus could not be reached. Check your connection, then open Caduceus from the EHR again.";

// Each step is taken once per page load. React runs effects twice while developing, and a launch's state is good
// for one use only.
const taken = new Map<string, Promise<unknown>>();
function once<T>(key: string, step: () => Promise<T>): Promise<T> {
  if (!taken.has(key)) taken.set(key, step());
  return taken.get(key) as Promise<T>;
}

/** What went wrong, then what to do about it. */
function Problem({ message }: { message: string }) {
  return (
    <div className="panel callout ehr__problem" role="alert">
      <p className="ehr__problem-text">{message}</p>
      <p>To start again, <Link to="/help#ehr">open the SMART launcher from the Help page</Link>. Or go{" "}
        <Link to="/">back to your queue</Link>.</p>
    </div>
  );
}

function Working({ children }: { children: string }) {
  return <p className="lead ehr__working" role="status"><Clock size={26} /><span>{children}</span></p>;
}

/** The EHR opened Caduceus with ?iss= and ?launch=. The API checks the EHR, discovers its sign-in endpoints and
 *  returns where the browser goes next: the EHR's own page, which sends it back to /smart/callback. */
export function SmartLaunchPage() {
  useTitle("Opening from the EHR");
  const [params] = useSearchParams();
  const iss = params.get("iss") ?? "", launch = params.get("launch") ?? "";
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!iss || !launch) return;
    const body = JSON.stringify({ iss, launch, redirect_uri: `${window.location.origin}/smart/callback` });
    once(`launch ${iss} ${launch}`, () => api<{ authorize_url: string }>("/api/smart/launch", { method: "POST", body }))
      .then(({ authorize_url }) => {
        if (!navigable(authorize_url)) throw new ApiError(0, "Caduceus could not connect to the EHR. Open it from the EHR again.");
        window.location.assign(authorize_url);
      })
      .catch((e) => setProblem(e instanceof ApiError ? e.message : UNREACHABLE));
  }, [iss, launch]);

  return (
    <div className="page ehr ehr--step">
      <h1 className="page-title">{problem ? "Caduceus did not connect to the EHR" : "Opening Caduceus from the EHR"}</h1>
      {!iss || !launch ? (
        <p className="lead">This page opens when an EHR launches Caduceus for a patient. To try it,{" "}
          <Link to="/help#ehr">open the SMART launcher from the Help page</Link>.</p>
      ) : problem ? <Problem message={problem} /> : (
        <Working>Connecting to the EHR. Your browser goes to the EHR to confirm the sign-in, then comes back here.</Working>
      )}
    </div>
  );
}

/** The EHR sent the browser back with ?code= and ?state=. The code leaves the address bar at once (it would stay
 *  in the browser's history otherwise) and goes to the API, which exchanges it and reads the record. */
export function SmartCallbackPage() {
  useTitle("Reading the record from the EHR");
  const navigate = useNavigate();
  const location = useLocation();
  const [sent] = useState(() => {
    const q = new URLSearchParams(location.search);
    return { code: q.get("code") ?? "", state: q.get("state") ?? "", error: q.get("error") };
  });
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (window.location.search) navigate("/smart/callback", { replace: true });
    if (sent.error) {
      setProblem(sent.error === "access_denied"
        ? "The EHR did not share the record, so nothing was read. Open Caduceus from the EHR again, and allow access when it asks."
        : "The EHR stopped the sign-in, so nothing was read. Open Caduceus from the EHR again.");
      return;
    }
    if (!sent.code || !sent.state) {
      setProblem("This page opens when the EHR sends you back to Caduceus. Open Caduceus from the EHR again.");
      return;
    }
    const body = JSON.stringify({ code: sent.code, state: sent.state });
    once(`callback ${sent.state}`, () => api<{ record: string }>("/api/smart/callback", { method: "POST", body }))
      .then(({ record }) => navigate(`/smart/record/${record}`, { replace: true }))
      .catch((e) => setProblem(e instanceof ApiError ? e.message : UNREACHABLE));
  }, [navigate, sent]);

  return (
    <div className="page ehr ehr--step">
      <h1 className="page-title">{problem ? "The record was not read" : "Reading the record from the EHR"}</h1>
      {problem ? <Problem message={problem} /> : (
        <Working>Reading the patient’s record from the EHR. This can take up to 20 seconds.</Working>
      )}
    </div>
  );
}

/** How much of one service's criteria the record answers, as icons and words. */
function Answers({ s }: { s: EhrService }) {
  return (
    <p className="ehr__counts">
      {s.met > 0 && <span className="status status--seal"><CheckCircle size={20} /><span>{s.met} met</span></span>}
      {s.unmet > 0 && <span className="status status--ink"><CrossCircle size={20} /><span>{s.unmet} not met</span></span>}
      {s.unknown > 0 && <span className="status status--caution"><Triangle size={20} /><span>{s.unknown} to confirm</span></span>}
    </p>
  );
}

const FIELD = { service: "service", side: "side", payer: "plan" } as const;

/** The record the EHR shared: who the patient is, what the record holds, and a request started from it. The
 *  choices live in the address (?service=, ?side=, ?plan=), so a reload keeps them and the page can be shared within
 *  this copy of the demo. */
export function EhrRecordPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { data, error } = useResource<EhrRecordView>(`/api/smart/records/${key}`);
  const [raised, setRaised] = useState<FormError[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  // The form answers at once from its own state; the address follows each change, replacing rather than adding
  // history, so a reload keeps the choices.
  const [picked, setPicked] = useState(() => ({
    service: params.get("service") ?? "", side: params.get("side") ?? "", plan: params.get("plan") ?? "" }));
  useTitle("Opened from the EHR");

  if (error) return <div className="page ehr"><Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link><Problem message={error.message} /></div>;
  if (!data) return <div className="page ehr"><p role="status" className="muted">Opening the record the EHR shared.</p></div>;

  const { service, side, plan } = picked;
  const chosen = data.services.find((s) => s.code === service) ?? null;
  const choose = (name: keyof typeof picked, value: string) => {
    const next = { ...picked, [name]: value, ...(name === "service" && value !== service ? { side: "" } : {}) };
    setPicked(next);
    setParams(new URLSearchParams(Object.entries(next).filter(([, v]) => v)), { replace: true });
  };

  const validate = (): FormError[] => {
    const found: FormError[] = [];
    if (!chosen) found.push({ key: "service", message: "Choose what is being requested", target: `service-${data.services[0]?.code}` });
    else if (chosen.side && side !== "left" && side !== "right") {
      found.push({ key: "side", message: `Choose which ${chosen.side} the request is for`, target: "side-left" });
    }
    if (!data.payers.includes(plan)) found.push({ key: "plan", message: "Choose the health plan", target: "ehr-plan" });
    return found;
  };
  const errors = outstanding(raised, validate());
  const errorFor = (k: string) => errors.find((e) => e.key === k);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const found = validate();
    setRaised(found);
    setAttempt((n) => n + 1);
    if (found.length) return;
    setBusy(true);
    try {
      const body = JSON.stringify({ service, side: chosen?.side ? side : null, payer: plan });
      const made = await api<{ case_id: string }>(`/api/smart/records/${data.key}/case`, { method: "POST", body });
      navigate(`/cases/${made.case_id}`);
    } catch (err) {
      const x = err as ApiError;
      const k: string = FIELD[x.refusal.field as keyof typeof FIELD] ?? "form";
      const targets: Record<string, string> = { service: `service-${service || data.services[0]?.code}`,
                                                side: "side-left", plan: "ehr-plan" };
      setRaised([{ key: k, message: x.message, target: targets[k], caseId: x.refusal.case_id, server: true }]);
      setAttempt((n) => n + 1);
      setBusy(false);
    }
  };

  const p = data.patient;
  return (
    <div className="page ehr">
      <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
      <ErrorSummary errors={errors} attempt={attempt} />
      <section className="panel ehr__head" aria-labelledby="ehr-patient">
        <p className="ehr__from"><span className="tag tag--seal">From the EHR</span>
          <span className="support">Read from {data.source.host} {glue(data.source.read)}, when {data.source.by} opened
            Caduceus from the EHR.</span></p>
        <h1 id="ehr-patient" className="case-name">{p.name}</h1>
        <dl className="facts">
          <div className="facts__item"><dt className="facts__term">Date of birth</dt><dd className="facts__value">{p.born} (age {p.age})</dd></div>
          <div className="facts__item"><dt className="facts__term">Sex</dt><dd className="facts__value">{p.sex}</dd></div>
        </dl>
      </section>

      <section className="panel ehr__holds" aria-labelledby="holds-title">
        <h2 id="holds-title" className="section-title">What the record holds</h2>
        <dl className="ehr__list">
          {data.holds.map((h) => (
            <div key={h.what} className="ehr__row"><dt>{h.what}</dt><dd>{h.words}</dd></div>
          ))}
        </dl>
        {data.relevant.length > 0 && (
          <div className="ehr__relevant">
            <h3 className="packet__title">Diagnoses the coverage criteria look for</h3>
            <ul>{data.relevant.map((r) => <li key={r}>{r}.</li>)}</ul>
          </div>
        )}
        <p className="support">{data.reads}</p>
      </section>

      {data.cases.length > 0 && (
        <section className="panel ehr__cases" aria-labelledby="cases-title">
          <h2 id="cases-title" className="section-title">Requests already started</h2>
          <ul>
            {data.cases.map((c) => (
              <li key={c.id} className="waiting__item">
                <Link to={`/cases/${c.id}`} className="waiting__name">{c.service}, case {c.id}</Link>
                <StatusWord statusKey={c.status.key} word={c.status.word} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {!data.read_only && (
        <form className="panel ehr__form" noValidate onSubmit={submit} aria-labelledby="start-title">
          <h2 id="start-title" className="section-title">Start a prior-authorization request</h2>
          <fieldset className={`fieldset${errorFor("service") ? " ehr__field--error" : ""}`}
                    aria-describedby={errorFor("service") ? "err-service" : undefined}>
            <legend className="legend">What is being requested?</legend>
            <ErrorMessage id="err-service" message={errorFor("service")?.message} />
            {data.services.map((s) => (
              <label key={s.code} className={`choice choice--rich${service === s.code ? " choice--chosen" : ""}`}>
                <input type="radio" id={`service-${s.code}`} name="service" value={s.code} checked={service === s.code}
                       aria-labelledby={`service-name-${s.code}`} aria-describedby={`answers-${s.code}`}
                       onChange={() => choose("service", s.code)} />
                <span className="choice__body">
                  <span className="choice__title" id={`service-name-${s.code}`}>{s.label}</span>
                  <span className="choice__note" id={`answers-${s.code}`}>{s.lead}</span>
                </span>
              </label>
            ))}
          </fieldset>

          {chosen?.side && (
            <fieldset className={`fieldset${errorFor("side") ? " ehr__field--error" : ""}`}
                      aria-describedby={["side-hint", errorFor("side") ? "err-side" : ""].filter(Boolean).join(" ")}>
              <legend className="legend">Which {chosen.side}?</legend>
              <ErrorMessage id="err-side" message={errorFor("side")?.message} />
              <div className="ehr__sides">
                {(["left", "right"] as const).map((v) => (
                  <label key={v} className={`choice${side === v ? " choice--chosen" : ""}`}>
                    <input type="radio" id={`side-${v}`} name="side" value={v} checked={side === v}
                           onChange={() => choose("side", v)} />
                    <span>{v === "left" ? "Left" : "Right"} {chosen.side}</span>
                  </label>
                ))}
              </div>
              <p className="support" id="side-hint">The criteria check that the diagnosis in the record is for this
                {" "}{chosen.side}.</p>
            </fieldset>
          )}

          <div className={`field${errorFor("plan") ? " ehr__field--error" : ""}`}>
            <label className="field-label" htmlFor="ehr-plan">Health plan</label>
            <p className="support" id="plan-hint">The EHR shared the clinical record, not the patient’s coverage, so
              choose the plan from the order.</p>
            <ErrorMessage id="err-plan" message={errorFor("plan")?.message} />
            <select id="ehr-plan" className="input select" value={data.payers.includes(plan) ? plan : ""}
                    aria-describedby={["plan-hint", errorFor("plan") ? "err-plan" : ""].filter(Boolean).join(" ")}
                    aria-invalid={errorFor("plan") ? true : undefined} onChange={(e) => choose("plan", e.target.value)}>
              <option value="">Choose a health plan</option>
              {data.payers.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </div>

          {chosen && (
            <div className="ehr__answers">
              <h3 className="packet__title">Before the case opens</h3>
              <p>{glue(chosen.answers)}</p>
              <Answers s={chosen} />
            </div>
          )}

          <div className="ehr__actions">
            <button type="submit" className="btn btn--primary btn--large" disabled={busy}>Start the case</button>
            <p className="support ehr__status" role="status">{busy
              ? "Checking the record against the payer’s policy and writing the draft. This can take up to 20 seconds."
              : "Caduceus checks the record against the payer’s policy and writes the draft, as for any referral."}</p>
          </div>
        </form>
      )}
    </div>
  );
}
