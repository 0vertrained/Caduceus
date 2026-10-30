import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type CaseView, type CriterionResult, type Page, type PayerReply } from "../api";
import { ChevronLeft, CheckCircle, Triangle } from "../components/Icons";
import { CriterionIcon, StatusWord, criterionWord } from "../components/Status";
import { countWords, midSentence, useResource, useSecondsLeft } from "../hooks";

type Panel = { kind: "draft" } | { kind: "edit" } | { kind: "page"; docId: string; sentence: string | null; supports: string | null };

const STEPS = ["Packet checked", "Review the criteria", "Approve the draft", "Sent to the payer"];

function stepIndex(c: CaseView): number {
  if (["approved", "pended", "denied"].includes(c.state) || c.submission?.sent) return 4;
  if (c.state === "submitted") return 3;
  if (c.actions.can_approve) return 2;
  return 1;
}

function Progress({ c }: { c: CaseView }) {
  const at = stepIndex(c);
  return (
    <ol className="progress" aria-label="Progress">
      {STEPS.map((s, i) => {
        const done = i < at, current = i === at;
        return (
          <li key={s} className={`progress__step${done ? " progress__step--done" : ""}${current ? " progress__step--current" : ""}`}
              aria-current={current ? "step" : undefined}>
            {done ? <CheckCircle size={24} /> : <span className="progress__marker" aria-hidden="true" />}
            <span>{s}{done ? <span className="visually-hidden">, done</span> : null}</span>
          </li>
        );
      })}
    </ol>
  );
}

function CaseHeader({ c }: { c: CaseView }) {
  return (
    <section className="panel case-head" aria-labelledby="patient-name">
      <div className="case-head__main">
        <div className="case-head__title-row">
          <h1 id="patient-name" className="case-name">{c.patient.name}</h1>
          <StatusWord statusKey={c.status.key} word={c.status.word} pill />
        </div>
        <dl className="facts">
          <div className="facts__item"><dt className="facts__term">Date of birth</dt>
            <dd className="facts__value">{c.patient.birth_date} (age {c.patient.age})</dd></div>
          <div className="facts__item"><dt className="facts__term">Due</dt>
            <dd className={`facts__value${c.due.urgent ? " facts__value--urgent" : ""}`}>{c.due.words}, {c.due.sub}</dd></div>
          <div className="facts__item"><dt className="facts__term">Requested</dt>
            <dd className="facts__value">{c.service.display}</dd></div>
          <div className="facts__item"><dt className="facts__term">Ordered by</dt>
            <dd className="facts__value">{c.ordering_clinician.name}, {c.ordering_clinician.specialty}</dd></div>
        </dl>
        <details className="details">
          <summary className="details__summary">More details about this request</summary>
          <dl className="facts">
            <div className="facts__item"><dt className="facts__term">Payer</dt><dd className="facts__value">{c.payer.name}</dd></div>
            <div className="facts__item"><dt className="facts__term">Member ID</dt><dd className="facts__value">Ending in {c.payer.member_id_last4}</dd></div>
            <div className="facts__item"><dt className="facts__term">Procedure code</dt><dd className="facts__value">{c.service.system.toUpperCase()} {c.service.code}</dd></div>
            <div className="facts__item"><dt className="facts__term">Received</dt><dd className="facts__value">{c.received}, {c.packet.length} pages</dd></div>
            <div className="facts__item"><dt className="facts__term">Case number</dt><dd className="facts__value">{c.id}</dd></div>
          </dl>
        </details>
      </div>
      <Progress c={c} />
    </section>
  );
}

function Criteria({ c, open }: { c: CaseView; open: (p: Panel) => void }) {
  const { met, unknown } = c.criteria.counts;
  const lead = `${countWords(met)} of ${countWords(c.criteria.total).toLowerCase()} are met.` +
    (unknown ? ` ${unknown === 1 ? "One needs" : `${countWords(unknown)} need`} ${c.ordering_clinician.name}.` : "");
  const pageTitle = (id: string | null) => c.packet.find((p) => p.id === id)?.title ? midSentence(c.packet.find((p) => p.id === id)!.title) : "page";
  return (
    <section className="panel criteria" aria-labelledby="criteria-title">
      <div>
        <h2 id="criteria-title" className="section-title">Coverage criteria</h2>
        <p className="criteria__lead">{lead}</p>
      </div>
      <ul>
        {c.criteria.results.map((r: CriterionResult) => {
          const w = criterionWord(r.status);
          const linked = r.evidence.filter((e) => e.document_id);
          return (
            <li key={r.id} className={`criterion${r.status === "unknown" ? " criterion--attention" : ""}`}>
              <CriterionIcon status={r.status} />
              <div className="criterion__body">
                <span className="criterion__title">{r.text}
                  <span className={`criterion__word status--${w.tone}`}>{r.attested_by ? (r.status === "met" ? "Met, signed" : "Not met, signed") : w.word}</span>
                </span>
                <span className="muted">
                  {r.attested_by ? (r.status === "met" ? `${r.attested_by} confirmed this.`
                    : `${r.attested_by} said this was not done.`) :
                    r.status === "unknown" ? "No note says this either way, so nothing was assumed." : r.explanation}
                </span>
                {linked.slice(0, 1).map((e) => (
                  <button key={e.document_id} type="button" className="link-button" style={{ alignSelf: "flex-start" }}
                          onClick={() => open({ kind: "page", docId: e.document_id!, sentence: e.sentence, supports: r.text })}>
                    Show the {pageTitle(e.document_id)}, page {e.page}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="provenance">
        <p>
          Checked today at {c.criteria.checked_at} against the {c.criteria.version} version of the coverage policy for{" "}
          {midSentence(c.criteria.title)}
          {c.criteria.source_kind === "development-paraphrase" ? " (development criteria)" : ""}. A criterion is marked
          met only when its evidence is found in the record.
        </p>
        <details>
          <summary className="details__summary">Read the policy text</summary>
          <ul className="policy-list">
            {c.criteria.results.map((r) => <li key={r.id}><strong>{r.text}.</strong> {r.policy_sentence}</li>)}
          </ul>
        </details>
      </div>
    </section>
  );
}

function Highlighted({ text, sentence }: { text: string; sentence: string | null }) {
  const at = sentence ? text.indexOf(sentence) : -1;
  if (!sentence || at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark>{sentence}</mark>{text.slice(at + sentence.length)}</>;
}

function SidePanel({ c, panel, setPanel, onSaved }: {
  c: CaseView; panel: Panel; setPanel: (p: Panel) => void; onSaved: (c: CaseView) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [draftText, setDraftText] = useState(c.draft?.text ?? "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (panel.kind !== "page") return;
    setPage(null);
    api<Page>(`/api/cases/${c.id}/pages/${panel.docId}`).then(setPage).catch((e: ApiError) => setError(e.message));
  }, [panel, c.id]);
  useEffect(() => { heading.current?.focus(); }, [panel, page]);

  const pending = c.criteria.results.filter((r) => r.status === "unknown");
  const signedBy = c.criteria.results.find((r) => r.attested_by)?.attested_by;

  if (panel.kind === "page") {
    return (
      <aside className="panel side" aria-labelledby="panel-title" aria-live="polite">
        <button type="button" className="link-button" style={{ display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}
                onClick={() => setPanel({ kind: "draft" })}><ChevronLeft size={20} />Back to the draft</button>
        {!page ? <p role="status" className="muted">{error ?? "Opening the page."}</p> : (
          <>
            <div>
              <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title" style={{ fontSize: "1.5rem" }}>{page.title}</h2>
              <p className="support">Page {page.page} of {page.pages}{page.date ? `, ${page.date}` : ""}{page.author ? `, ${page.author}` : ""}</p>
            </div>
            {panel.sentence && page.text.includes(panel.sentence) && (
              <p className="support" style={{ color: "var(--ink)" }}>
                <mark>Highlighted</mark> is the sentence that supports: {panel.supports}.
              </p>
            )}
            <div className="paper"><Highlighted text={page.text} sentence={panel.sentence} /></div>
          </>
        )}
      </aside>
    );
  }

  if (panel.kind === "edit") {
    const save = async () => {
      try {
        onSaved(await api<CaseView>(`/api/cases/${c.id}/draft`, { method: "PUT", body: JSON.stringify({ text: draftText }) }));
        setPanel({ kind: "draft" });
      } catch (e) {
        setError((e as ApiError).message);
      }
    };
    return (
      <aside className="panel side" aria-labelledby="panel-title">
        <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">Edit the draft</h2>
        <label className="field-label" htmlFor="draft-text">Request text sent to {c.payer.name}</label>
        <textarea id="draft-text" className="input textarea" rows={12} value={draftText} onChange={(e) => setDraftText(e.target.value)} />
        {error && <p role="alert" className="alert">{error}</p>}
        <div className="decision-bar__actions">
          <button type="button" className="btn btn--primary" onClick={save}>Save the draft</button>
          <button type="button" className="btn btn--secondary" onClick={() => setPanel({ kind: "draft" })}>Cancel</button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="panel side" aria-labelledby="panel-title">
      <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">Draft request</h2>
      {c.draft?.out_of_date && (
        <div className="draft-notice" role="note">
          <Triangle />
          <p><strong>Read this draft again.</strong> {c.ordering_clinician.name} signed after it was edited, so it
            may not match the answers. Save it to continue.</p>
        </div>
      )}
      <p className="draft">{c.draft?.text}</p>
      {c.draft?.edited_by && <p className="support">Edited by {c.draft.edited_by}.</p>}
      <div className="signature">
        {signedBy ? <span className="signature__line signature__line--signed">Signed by {signedBy}</span>
          : pending.length ? <span className="signature__line">awaiting signature</span>
          : <span className="signature__line signature__line--signed">No clinician signature needed</span>}
        {pending.length > 0 && <span className="support">Clinician attestation, {c.ordering_clinician.name}</span>}
      </div>
      {c.state === "review" && (
        <button type="button" className="btn btn--secondary" style={{ alignSelf: "flex-start" }}
                onClick={() => { setDraftText(c.draft?.text ?? ""); setPanel({ kind: "edit" }); }}>Edit the draft</button>
      )}
      <div className="packet">
        <span style={{ fontWeight: 700 }}>Referral packet</span>
        {c.packet.map((p) => (
          <button key={p.id} type="button" className="packet__item"
                  onClick={() => setPanel({ kind: "page", docId: p.id, sentence: null, supports: null })}>
            <span>{p.title}</span><span className="support">page {p.page}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}

function PayerQuestion({ c, reply }: { c: CaseView; reply: PayerReply }) {
  const attested = reply.attested[0];
  return (
    <section className="panel payer-question" aria-labelledby="payer-question-title">
      <h2 id="payer-question-title" className="section-title">{c.payer.name} asked a question</h2>
      {reply.question && <p className="payer-question__ask">{reply.question}</p>}
      {attested && !reply.note && (
        <p>No page in the referral packet documents this. {attested.by} attested to it {attested.at}, so the payer
          wants it written in the chart.</p>
      )}
      {reply.note ? (
        <figure className="payer-question__note">
          <figcaption className="support">Addendum by {reply.note.author}, signed {reply.note.signed_at}</figcaption>
          <div className="paper">{reply.note.text}</div>
        </figure>
      ) : reply.declined ? (
        <p><strong>{reply.declined.by} cannot document this.</strong> They said so {reply.declined.at}. Nothing was
          sent to {c.payer.name}.</p>
      ) : null}
    </section>
  );
}

function DecisionBar({ c, setCase, reload }: { c: CaseView; setCase: (c: CaseView) => void; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState("");
  const undoLeft = useSecondsLeft(c.undo?.until);
  const sendLeft = useSecondsLeft(c.state === "submitted" && !c.submission?.sent ? c.submission?.send_after : null);

  useEffect(() => {
    if (c.undo && undoLeft === 0) reload();
  }, [undoLeft, c.undo, reload]);

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      setCase(await api<CaseView>(`/api/cases/${c.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      setReturning(false);
      setReason("");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  const undo = c.undo && undoLeft > 0 ? (
    <button type="button" className="btn btn--secondary btn--large" disabled={busy} onClick={() => act("undo")}>
      Undo <span className="muted" style={{ fontWeight: 400 }} aria-hidden="true">({undoLeft} seconds)</span>
    </button>
  ) : null;

  let summary: ReactNode = c.summary;
  let actions: ReactNode = null;
  if (c.state === "review") {
    actions = (
      <>
        {c.actions.can_return && (
          <button type="button" className="btn--text" onClick={() => setReturning(true)} aria-expanded={returning}>
            Return for correction
          </button>
        )}
        {c.actions.can_request_attestation ? (
          <>
            <button type="button" className="btn btn--large" disabled aria-describedby="bar-summary">Approve and send</button>
            <button type="button" className="btn btn--primary btn--large" disabled={busy} onClick={() => act("request-attestation")}>
              Send to {c.ordering_clinician.name} to attest
            </button>
          </>
        ) : (
          <button type="button" className="btn btn--primary btn--large" disabled={busy || !c.actions.can_approve}
                  aria-describedby="bar-summary" onClick={() => act("approve")}>Approve and send</button>
        )}
      </>
    );
  } else if (c.state === "awaiting_attestation") {
    summary = <><CheckCircle size={26} /><span><strong>Sent to {c.ordering_clinician.name}.</strong> This case comes back to you when they sign.</span></>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else if (c.state === "pended") {
    const returnButton = (primary: boolean) => (
      <button type="button" className={primary ? "btn btn--primary btn--large" : "btn--text"}
              onClick={() => setReturning(true)} aria-expanded={returning}>Return for correction</button>
    );
    actions = (
      <>
        {c.actions.can_send_note ? (
          <>{c.actions.can_return && returnButton(false)}
            <button type="button" className="btn btn--primary btn--large" disabled={busy} onClick={() => act("send-note")}>
              Send the note to {c.payer.name}
            </button></>
        ) : c.actions.can_ask_for_note ? (
          <>{c.actions.can_return && returnButton(false)}
            <button type="button" className="btn btn--primary btn--large" disabled={busy} onClick={() => act("request-note")}>
              Ask {c.ordering_clinician.name} for the note
            </button></>
        ) : c.actions.can_return ? returnButton(true) : null}
      </>
    );
  } else if (c.state === "awaiting_note") {
    summary = <><CheckCircle size={26} /><span><strong>Asked {c.ordering_clinician.name} for the note.</strong> This
      case comes back to you when they sign it.</span></>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else if (c.state === "submitted" && !c.submission?.sent) {
    summary = <><CheckCircle size={26} /><span><strong>Approved.</strong> Sending
      {c.submission?.kind === "reply" ? " the note" : ""} to {c.payer.name}
      {sendLeft > 0 ? ` in ${sendLeft} seconds` : " now"}.</span></>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else if (c.state === "returned") {
    summary = <span><strong>Returned to {c.ordering_clinician.name}.</strong> Reason: {c.returned_reason}</span>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else {
    actions = <Link to="/" className="btn btn--primary btn--large">Back to your queue</Link>;
    if (c.state === "approved") summary = <><CheckCircle size={26} /><span><strong>Approved by {c.payer.name}.</strong> Authorization {c.payer_response?.decision.authorization_number}.</span></>;
    if (c.state === "submitted") summary = <span><strong>Sent to {c.payer.name}.</strong> Waiting for their decision.</span>;
  }

  return (
    <div className="decision-bar" role="region" aria-label="Next step">
      <div className="decision-bar__inner">
        <p id="bar-summary" className="decision-bar__summary" role="status">{summary}</p>
        <div className="decision-bar__actions">{actions}</div>
        {returning && (
          <form className="decision-bar__form" onSubmit={(e) => { e.preventDefault(); void act("return", { reason }); }}>
            <label className="field-label" htmlFor="return-reason">What needs correcting? The clinic will see this.</label>
            <textarea id="return-reason" className="input textarea" style={{ minHeight: 80 }} value={reason}
                      onChange={(e) => setReason(e.target.value)} />
            <div className="decision-bar__actions">
              <button type="submit" className="btn btn--primary" disabled={busy}>Return to {c.ordering_clinician.name}</button>
              <button type="button" className="btn btn--secondary" onClick={() => setReturning(false)}>Cancel</button>
            </div>
          </form>
        )}
        {error && <p role="alert" className="alert" style={{ flexBasis: "100%" }}>{error}</p>}
      </div>
    </div>
  );
}

export function CaseReviewPage() {
  const { id } = useParams();
  const { data: c, setData, error, reload } = useResource<CaseView>(`/api/cases/${id}`);
  const [panel, setPanel] = useState<Panel>({ kind: "draft" });

  useEffect(() => {
    if (c?.state !== "submitted" || c.submission?.sent) return;
    const t = window.setInterval(() => void reload(), 4000);
    return () => window.clearInterval(t);
  }, [c?.state, c?.submission?.sent, reload]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!c) return <div className="page"><p role="status" className="muted">Opening the case.</p></div>;

  return (
    <>
      <div className="page page--case">
        <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
        <CaseHeader c={c} />
        {c.reply && ["pended", "awaiting_note", "submitted"].includes(c.state) && <PayerQuestion c={c} reply={c.reply} />}
        <div className="case-body">
          <Criteria c={c} open={setPanel} />
          <SidePanel key={`${c.id}-${c.draft?.text?.length}`} c={c} panel={panel} setPanel={setPanel} onSaved={setData} />
        </div>
      </div>
      <DecisionBar c={c} setCase={setData} reload={reload} />
    </>
  );
}
