import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { FaxRow, Me, Queue as QueueData, QueueRow } from "../api";
import { CheckCircle, Clock, Triangle } from "../components/Icons";
import { Seal } from "../components/Seal";
import { searchTerm } from "../components/Header";
import { StatusWord } from "../components/Status";
import { countWords, glue, useResource, useTitle } from "../hooks";

function helperKey(me: Me) {
  return `caduceus.helperHidden.${me.id}`;
}

function Helper({ me }: { me: Me }) {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(helperKey(me)) === "1";
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  const hide = () => {
    setHidden(true);
    try {
      localStorage.setItem(helperKey(me), "1");
    } catch {
      /* hidden for this visit */
    }
  };
  const steps = [
    "Caduceus reads the referral and checks the record.",
    "You review each coverage criterion and its evidence.",
    "If something is missing, a clinician signs for it.",
    "You approve, and the request goes to the payer.",
  ];
  return (
    <section className="panel helper" aria-labelledby="helper-title">
      <div className="helper__head">
        <h2 id="helper-title" className="helper__title">How a case moves through Caduceus</h2>
        <button type="button" className="link-button" onClick={hide}>Got it, hide this</button>
      </div>
      <ol className="helper__steps">
        {steps.map((s, i) => (
          <li key={s} className="helper__step"><span className="step-number" aria-hidden="true">{i + 1}</span>{s}</li>
        ))}
      </ol>
    </section>
  );
}

function Row({ row }: { row: QueueRow }) {
  return (
    <li className="row">
      <div className="row__who">
        <span className="row__name">{row.patient.name}, {row.patient.age}</span>
        <span className="muted">{row.service}. {row.payer}.</span>
      </div>
      <div className="row__due">
        <span className={`row__due-words${row.due.urgent ? " row__due-words--urgent" : ""}`}>{glue(row.due.words)}</span>
        <span className="support">{glue(row.due.sub)}</span>
      </div>
      <div className="row__status"><StatusWord statusKey={row.status.key} word={row.status.word} /></div>
      <Link to={`/cases/${row.id}`} className="btn btn--secondary"
            aria-label={`Open case for ${row.patient.name}`}>Open case</Link>
    </li>
  );
}

function ClearDesk({ data }: { data: QueueData }) {
  return (
    <div className="clear">
      <section className="clear__main" aria-labelledby="clear-title">
        <Seal count={data.counts.approved_today} full />
        <h1 id="clear-title" className="clear__title">Your desk is clear.</h1>
        <p className="lead clear__lead">
          Nothing needs you right now. When a clinician signs or a payer replies, the case comes back to the top of
          your queue.
        </p>
      </section>
      <Waiting data={data} />
    </div>
  );
}

function Waiting({ data }: { data: QueueData }) {
  if (!data.waiting.length) return null;
  return (
    <section className="panel waiting" aria-labelledby="waiting-title">
      <div>
        <h2 id="waiting-title" className="section-title">Waiting on others</h2>
        <p className="muted">{countWords(data.waiting.length)} {data.waiting.length === 1 ? "case. You do not need to do anything for it yet."
          : "cases. You do not need to do anything for these yet."}</p>
      </div>
      <ul>
        {data.waiting.map((w) => (
          <li key={w.id} className="waiting__item">
            <Link to={`/cases/${w.id}`} className="waiting__name">{w.patient}</Link>
            <span className="muted">{w.status.word} since {glue(w.since)}.{w.next ? ` ${w.next}` : ""}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Referrals that arrived by fax: Caduceus has read them; a person checks what it was unsure of. */
function FaxInbox() {
  const { data } = useResource<{ available: boolean; faxes: FaxRow[] }>("/api/intake", 30000);
  const open = (data?.faxes ?? []).filter((f) => f.status !== "confirmed");
  if (!data || !open.length) return null;
  return (
    <section className="panel fax-inbox" aria-labelledby="fax-title">
      <div>
        <h2 id="fax-title" className="section-title">Arrived by fax</h2>
        <p className="muted">{data.available
          ? "Caduceus read each cover sheet inside Mesa Verde’s systems. Check what it was unsure of, and the case is created."
          : "Fax reading is not available right now. The faxes wait here; nothing is guessed."}</p>
      </div>
      <ul>
        {open.map((f) => (
          <li key={f.id} className="fax-inbox__item">
            <div>
              <span className="fax-inbox__name">{f.patient || "Patient not read yet"}</span>
              <span className="muted"> Referral {f.referral || "unread"}, {f.pages} pages, received {f.received}</span>
            </div>
            {f.status === "read" && (f.to_check
              ? <span className="status status--caution"><Triangle size={20} /><span>{f.to_check} {f.to_check === 1 ? "field" : "fields"} to check</span></span>
              : <span className="status status--seal"><CheckCircle size={20} /><span>Read clearly</span></span>)}
            {f.status === "waiting_on_clinic" && f.request && (
              <span className="fax-inbox__waiting">
                <span className="status status--ink"><Clock size={20} /><span>Waiting on {f.request.to ?? "the clinic"}</span></span>
                <span className="support">Asked {glue(f.request.asked)}</span>
              </span>
            )}
            <Link to={`/intake/${f.id}`} className="btn btn--secondary" aria-label={`${f.status === "waiting_on_clinic" ? "Open" : "Check"} the fax for ${f.patient || f.id}`}>
              {f.status === "waiting_on_clinic" ? "Open the fax" : "Check the fax"}</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function QueuePage({ me }: { me: Me }) {
  const { data, error } = useResource<QueueData>("/api/queue", 15000);
  const [laterOpen, setLaterOpen] = useState(false);
  const term = searchTerm(useLocation().state);
  const q = term.trim().toLowerCase();
  // The title never carries the search term: it is usually a patient's name.
  useTitle(q ? "Search results" : data ? `Your queue (${data.counts.open})` : "Your queue");

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="page"><p className="muted" role="status">Loading your queue.</p></div>;

  if (q) {
    const rows = data.groups.flatMap((g) => g.cases)
      .filter((r) => r.patient.name.toLowerCase().includes(q) || r.id.includes(q));
    return (
      <div className="page">
        <div className="queue-intro__text">
          <h1 className="page-title">Results for “{term}”</h1>
          <p className="lead">{rows.length ? `${countWords(rows.length)} matching ${rows.length === 1 ? "case" : "cases"}.`
            : "No open case matches that name or number."} <Link to="/">Back to your queue</Link></p>
        </div>
        {rows.length > 0 && <ul className="panel rows">{rows.map((r) => <Row key={r.id} row={r} />)}</ul>}
      </div>
    );
  }

  if (data.counts.open === 0) {
    return <div className="page"><ClearDesk data={data} /></div>;
  }

  const n = data.counts.open;
  return (
    <div className="page">
      <Helper me={me} />
      <FaxInbox />
      <div className="queue-intro">
        <div className="queue-intro__text">
          <h1 className="page-title">{countWords(n)} {n === 1 ? "case is" : "cases are"} waiting for you.</h1>
          <p className="lead">
            {data.counts.due_today
              ? `${countWords(data.counts.due_today)} ${data.counts.due_today === 1 ? "is" : "are"} due today. They are at the top, soonest first.`
              : "Nothing is due today."}
          </p>
        </div>
        <div className="seal-block">
          <Seal count={data.counts.approved_today} />
          <div className="seal-notes">
            <span>{data.counts.with_clinicians} with clinicians</span>
            <span>{data.counts.due_today} still due today</span>
          </div>
        </div>
      </div>

      {data.groups.filter((g) => g.cases.length).map((g) => (
        <section key={g.key} className="group" aria-labelledby={`group-${g.key}`}>
          <div className="group__head">
            <h2 id={`group-${g.key}`} className="group__title">{g.title}</h2>
            <span className="muted">{g.cases.length} {g.cases.length === 1 ? "case" : "cases"}</span>
            {g.key === "later" && (
              <button type="button" className="link-button" aria-expanded={laterOpen}
                      aria-controls="later-rows" onClick={() => setLaterOpen(!laterOpen)}>
                {laterOpen ? "Hide them" : "Show them"}
              </button>
            )}
          </div>
          {(g.key !== "later" || laterOpen) && (
            <ul id={g.key === "later" ? "later-rows" : undefined} className="panel rows">
              {g.cases.map((r) => <Row key={r.id} row={r} />)}
            </ul>
          )}
        </section>
      ))}
      <Waiting data={data} />
    </div>
  );
}
