import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type AttestationRow, type AttestationView } from "../api";
import { Check } from "../components/Icons";
import { countWords, glue, oneLine, useResource, useSecondsLeft, useTitle } from "../hooks";

export function AttestListPage() {
  const { data, error } = useResource<AttestationRow[]>("/api/attestations", 15000);
  useTitle("Requests to sign");
  if (error) return <div className="attest"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="attest"><p role="status" className="muted">Loading.</p></div>;
  return (
    <div className="attest">
      <h1 className="attest__name">{data.length ? `${countWords(data.length)} ${data.length === 1 ? "request needs" : "requests need"} your signature.`
        : "Nothing needs your signature."}</h1>
      {data.length > 1 && <p className="muted">The one due soonest is first.</p>}
      <ul className="panel attest__list">
        {data.map((a) => (
          <li key={a.case_id} className="attest__item">
            <Link to={`/attest/${a.case_id}`} className="attest__link">
              {a.patient.name}, {a.patient.age}
            </Link>
            <span className="muted">{a.kind === "note" ? `${a.service}: the payer asked for a note` : a.service}</span>
            <span className={`attest__due${a.due.urgent ? " attest__due--urgent" : ""}`}>{glue(`${a.due.words}, ${a.due.sub}`)}</span>
            {a.reminders && (
              <span className="support">{a.reminders.count === 1 ? `${a.reminders.by} reminded you at ${glue(a.reminders.last)}.`
                : `${a.reminders.by} reminded you ${a.reminders.count} times, last at ${glue(a.reminders.last)}.`}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AttestPage() {
  const { id } = useParams();
  const [waiting, setWaiting] = useState(false);
  const { data, setData, error } = useResource<AttestationView>(`/api/attestations/${id}`, waiting ? 5000 : undefined);
  useEffect(() => { setWaiting(Boolean(data?.chart_summary_pending)); }, [data?.chart_summary_pending]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [signed, setSigned] = useState<AttestationView | null>(null);
  const undoLeft = useSecondsLeft(signed?.undo_until);
  useTitle(data?.state === "awaiting_note" ? "Write a note" : "Sign a request");

  if (error) return <div className="attest"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="attest"><p role="status" className="muted">Loading the request.</p></div>;

  if (signed) {
    const yes = Object.values(signed.answers ?? {}).every((a) => a === "yes");
    return (
      <div className="attest attest__done" role="status">
        <div className="seal seal--full seal--mark" aria-hidden="true"><Check size={64} /></div>
        <h1 className="attest__name">Signed. Thank you.</h1>
        <p>{yes ? `${data.requested_by.split(" ")[0]} can now send the request to ${data.payer}.`
          : `${data.requested_by.split(" ")[0]} will see that this needs more review before the request can go.`}</p>
        <p className="muted">Your answer, your name and the time are now part of {data.patient.name}’s case history.</p>
        {undoLeft > 0 && (
          <button type="button" className="btn btn--secondary btn--large btn--block"
                  onClick={async () => {
                    try {
                      setData(await api<AttestationView>(`/api/cases/${data.case_id}/undo`, { method: "POST" }));
                      setSigned(null);
                    } catch (e) {
                      setFailure((e as ApiError).message);
                    }
                  }}>
            Undo my signature <span className="countdown" aria-hidden="true">({undoLeft} seconds)</span>
          </button>
        )}
        <Link to="/" className="btn btn--primary btn--large btn--block">Done</Link>
        {failure && <p role="alert" className="alert">{failure}</p>}
      </div>
    );
  }

  if (data.state === "awaiting_note" && data.note_request) return <NoteForm data={data} setData={setData} />;
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
      <p className="muted">{glue(data.requested_by)} asked you to confirm {data.questions.length === 1 ? "one item" :
        `${countWords(data.questions.length).toLowerCase()} items`} before sending this request.</p>
      <section aria-labelledby="pt">
        <h1 id="pt" className="attest__name">{data.patient.name}</h1>
        <p className="attest__born">Born {data.patient.birth_date}</p>
        <p className="muted">{data.service}, ordered {data.ordered}</p>
      </section>
      <section className="panel attest__card" aria-labelledby="chart-title">
        <h2 id="chart-title" className="attest__card-title">What the chart shows</h2>
        <p>{data.chart_summary}</p>
        <p className="support" aria-live="polite">{data.chart_summary_by.route === "phi"
          ? <>Summarized by <span translate="no">{data.chart_summary_by.model}</span>, inside Mesa Verde’s network.
              What the chart does not say is from the record’s facts.</>
          : data.chart_summary_pending ? "From the record’s facts, for now. The model inside Mesa Verde’s network is "
            + "writing a summary, and it will appear here."
          : "From the record’s facts. No model was used."}</p>
      </section>
      {data.questions.map((q) => (
        <fieldset key={q.criterion_id} className="fieldset">
          <legend className="legend">{q.question}</legend>
          {q.help && <p className="support">{q.help}</p>}
          {q.leads.length > 0 && (
            <div className="leads">
              <p className="leads__title" id={`leads-${q.criterion_id}`}>Where the referral packet may answer this</p>
              <ul className="leads__list" aria-labelledby={`leads-${q.criterion_id}`}>
                {q.leads.map((l) => (
                  <li key={`${l.page}:${l.text}`} className="leads__item">
                    <q className="leads__text">{oneLine(l.text)}</q>
                    <span className="support">{glue(`${l.title}, page ${l.page}`)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {[["yes", "Yes, I confirm this"], ["no", "No, or I need to check first"]].map(([value, label]) => (
            <label key={value} className={`choice${answers[q.criterion_id] === value ? " choice--chosen" : ""}`}>
              <input type="radio" name={q.criterion_id} value={value} checked={answers[q.criterion_id] === value}
                     onChange={() => { setAnswers({ ...answers, [q.criterion_id]: value }); setFailure(null); }} />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
      ))}
      <button type="button" className="btn btn--primary btn--large btn--block" onClick={sign}
              aria-describedby="sign-note">Sign and return</button>
      <p id="sign-note" className="support attest__sign-note">
        {failure ? <span role="alert" className="alert">{failure}</span> : complete ? "You are signing with your own name."
          : "Choose an answer above, then sign."}
      </p>
    </div>
  );
}

// The payer pended the request and asked for the note behind what this clinician confirmed. They write it in their
// own words (nothing is drafted for them) and it joins the chart as an addendum dated today, or they say they cannot.
function NoteForm({ data, setData }: { data: AttestationView; setData: (v: AttestationView) => void }) {
  const ask = data.note_request!;
  const [text, setText] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<{ signed: boolean; until: string | null | undefined } | null>(null);
  const undoLeft = useSecondsLeft(done?.until);
  const first = data.requested_by.split(" ")[0];

  const send = async (path: string, body?: unknown) => {
    setFailure(null);
    try {
      const out = await api<AttestationView>(`/api/attestations/${data.case_id}/${path}`,
        { method: "POST", body: body ? JSON.stringify(body) : undefined });
      setDone({ signed: path === "note", until: out.undo_until });
    } catch (e) {
      setFailure((e as ApiError).message);
    }
  };
  const sign = () => text.trim() ? send("note", { text }) : setFailure("Write the note, then sign it.");

  if (done) {
    return (
      <div className="attest attest__done" role="status">
        <div className="seal seal--full seal--mark" aria-hidden="true"><Check size={64} /></div>
        <h1 className="attest__name">{done.signed ? "Signed. Thank you." : "Thank you for saying so."}</h1>
        <p>{done.signed ? `${first} can now send your note to ${data.payer}.`
          : `${first} will see that you cannot document this. Nothing goes to ${data.payer}.`}</p>
        {done.signed && <p className="muted">Your note is in {data.patient.name}’s chart as an addendum dated today,
          with your name.</p>}
        {undoLeft > 0 && (
          <button type="button" className="btn btn--secondary btn--large btn--block"
                  onClick={async () => {
                    try {
                      setData(await api<AttestationView>(`/api/cases/${data.case_id}/undo`, { method: "POST" }));
                      setDone(null);
                    } catch (e) {
                      setFailure((e as ApiError).message);
                    }
                  }}>
            {done.signed ? "Undo my signature" : "Undo"} <span className="countdown" aria-hidden="true">({undoLeft} seconds)</span>
          </button>
        )}
        <Link to="/" className="btn btn--primary btn--large btn--block">Done</Link>
        {failure && <p role="alert" className="alert">{failure}</p>}
      </div>
    );
  }

  return (
    <div className="attest">
      <p className="muted">{data.requested_by} asked you for a note. {data.payer} wants the clinical note behind
        what you confirmed before it decides.</p>
      <section aria-labelledby="pt">
        <h1 id="pt" className="attest__name">{data.patient.name}</h1>
        <p className="attest__born">Born {data.patient.birth_date}</p>
        <p className="muted">{data.service}, ordered {data.ordered}</p>
      </section>
      <section className="panel attest__card" aria-labelledby="ask-title">
        <h2 id="ask-title" className="attest__card-title">What {data.payer} asked</h2>
        <p>{ask.question}</p>
      </section>
      {ask.confirmed.map((c) => (
        <section key={c.question} className="panel attest__card" aria-label="What you confirmed">
          <h2 className="attest__card-title">You confirmed this {c.at}</h2>
          <p>{c.question} You answered yes.</p>
          <p className="support">The policy: {c.policy}</p>
        </section>
      ))}
      <div className="field">
        <label className="field-label" htmlFor="note-text">Your note</label>
        <p id="note-help" className="support">Write what you examined and found, in your own words. It is added to
          the chart as an addendum dated today, with your name.</p>
        <textarea id="note-text" className="input textarea" rows={8} maxLength={ask.limit} value={text}
                  aria-describedby="note-help" onChange={(e) => { setText(e.target.value); setFailure(null); }} />
      </div>
      <button type="button" className="btn btn--primary btn--large btn--block" onClick={sign}
              aria-describedby="sign-note">Sign the note and return</button>
      <p id="sign-note" className="support attest__sign-note">
        {failure ? <span role="alert" className="alert">{failure}</span> : "You are signing with your own name."}
      </p>
      <button type="button" className="btn btn--secondary btn--large btn--block"
              onClick={() => send("decline-note")}>I cannot document this</button>
    </div>
  );
}
