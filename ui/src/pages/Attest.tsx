import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type AttestationView } from "../api";
import { Check } from "../components/Icons";
import { countWords, useResource, useSecondsLeft } from "../hooks";

export function AttestListPage() {
  const { data, error } = useResource<{ case_id: string; patient: { name: string; age: number }; service: string }[]>(
    "/api/attestations", 15000);
  if (error) return <div className="attest"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="attest"><p role="status" className="muted">Loading.</p></div>;
  return (
    <div className="attest">
      <h1 className="attest__name">{data.length ? `${countWords(data.length)} ${data.length === 1 ? "request needs" : "requests need"} your signature.`
        : "Nothing needs your signature."}</h1>
      <ul className="panel" style={{ padding: "4px 18px" }}>
        {data.map((a) => (
          <li key={a.case_id} className="waiting__item">
            <Link to={`/attest/${a.case_id}`} style={{ fontWeight: 700, minHeight: 44, display: "inline-flex", alignItems: "center" }}>
              {a.patient.name}, {a.patient.age}
            </Link>
            <span className="muted">{a.service}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AttestPage() {
  const { id } = useParams();
  const { data, setData, error } = useResource<AttestationView>(`/api/attestations/${id}`);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [signed, setSigned] = useState<AttestationView | null>(null);
  const undoLeft = useSecondsLeft(signed?.undo_until);

  if (error) return <div className="attest"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="attest"><p role="status" className="muted">Loading the request.</p></div>;

  if (signed) {
    const yes = Object.values(signed.answers ?? {}).every((a) => a === "yes");
    return (
      <div className="attest attest__done" role="status">
        <div className="seal seal--full" aria-hidden="true" style={{ background: "var(--paper)", color: "var(--seal)" }}><Check /></div>
        <h1 className="attest__name">Signed. Thank you.</h1>
        <p>{yes ? `${data.requested_by.split(" ")[0]} can now send the request to ${data.payer}.`
          : `${data.requested_by.split(" ")[0]} will see that this needs more review before the request can go.`}</p>
        <p className="muted">Your answer, your name and the time are now part of {data.patient.name}’s case history.</p>
        {undoLeft > 0 && (
          <button type="button" className="btn btn--secondary btn--large" style={{ width: "100%" }}
                  onClick={async () => {
                    try {
                      setData(await api<AttestationView>(`/api/cases/${data.case_id}/undo`, { method: "POST" }));
                      setSigned(null);
                    } catch (e) {
                      setFailure((e as ApiError).message);
                    }
                  }}>
            Undo my signature <span className="muted" style={{ fontWeight: 400 }} aria-hidden="true">({undoLeft} seconds)</span>
          </button>
        )}
        <Link to="/" className="btn btn--primary btn--large" style={{ width: "100%" }}>Done</Link>
        {failure && <p role="alert" className="alert">{failure}</p>}
      </div>
    );
  }

  if (data.state !== "awaiting_attestation") {
    return <div className="attest"><h1 className="attest__name">This request is not waiting for a signature.</h1>
      <Link to="/">See your other requests</Link></div>;
  }

  const complete = data.questions.every((q) => answers[q.criterion_id]);
  const sign = async () => {
    if (!complete) {
      setFailure("Choose an answer for each question, then sign.");
      return;
    }
    try {
      setSigned(await api<AttestationView>(`/api/attestations/${data.case_id}`, { method: "POST", body: JSON.stringify({ answers }) }));
    } catch (e) {
      setFailure((e as ApiError).message);
    }
  };

  return (
    <div className="attest">
      <p className="muted">{data.requested_by} asked you to confirm {data.questions.length === 1 ? "one item" :
        `${countWords(data.questions.length).toLowerCase()} items`} before sending this request.</p>
      <section aria-labelledby="pt">
        <h1 id="pt" className="attest__name">{data.patient.name}</h1>
        <p style={{ fontWeight: 700 }}>Born {data.patient.birth_date}</p>
        <p className="muted">{data.service}, ordered {data.ordered}</p>
      </section>
      <section className="panel attest__card" aria-labelledby="chart-title">
        <h2 id="chart-title" style={{ fontSize: "1rem", fontWeight: 700 }}>What the chart shows</h2>
        <p>{data.chart_summary}</p>
      </section>
      {data.questions.map((q) => (
        <fieldset key={q.criterion_id} className="fieldset">
          <legend className="legend">{q.question}</legend>
          {q.help && <p className="support">{q.help}</p>}
          {[["yes", "Yes, I confirm this"], ["no", "No, or I need to check first"]].map(([value, label]) => (
            <label key={value} className={`choice${answers[q.criterion_id] === value ? " choice--chosen" : ""}`}>
              <input type="radio" name={q.criterion_id} value={value} checked={answers[q.criterion_id] === value}
                     onChange={() => { setAnswers({ ...answers, [q.criterion_id]: value }); setFailure(null); }} />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
      ))}
      <button type="button" className="btn btn--primary btn--large" style={{ width: "100%" }} onClick={sign}
              aria-describedby="sign-note">Sign and return</button>
      <p id="sign-note" className="support" style={{ textAlign: "center" }}>
        {failure ? <span role="alert" className="alert">{failure}</span> : complete ? "You are signing with your own name."
          : "Choose an answer above, then sign."}
      </p>
    </div>
  );
}
