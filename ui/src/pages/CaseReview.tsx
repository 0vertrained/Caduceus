import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type CaseView, type CriterionResult, type Page, type PayerReply, type Timeline } from "../api";
import { useAuthImage } from "../components/FaxImage";
import { ChevronLeft, ChevronRight, CheckCircle, Triangle } from "../components/Icons";
import { SealMark } from "../components/Seal";
import { CriterionIcon, StatusWord, criterionWord } from "../components/Status";
import { countWords, glue, midSentence, useResource, useSecondsLeft, useTitle } from "../hooks";

type Panel = { kind: "draft" } | { kind: "edit" } | { kind: "sent" }
  | { kind: "page"; docId: string; sentence: string | null; supports: string | null };

const REMOVED: Record<string, [string, string]> = {
  NAME: ["name part", "name parts"], DATE: ["date", "dates"], ADDRESS: ["street address", "street addresses"],
  CITY: ["city", "cities"], ZIP: ["ZIP code", "ZIP codes"], PHONE: ["phone number", "phone numbers"],
  EMAIL: ["email address", "email addresses"], RECORD_NUMBER: ["record number", "record numbers"],
  SSN: ["Social Security number", "Social Security numbers"], URL: ["web address", "web addresses"],
};

function removedWords(removed: Record<string, number> = {}): string {
  const parts = Object.entries(removed).filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${(REMOVED[k] ?? [k.toLowerCase(), k.toLowerCase()])[n === 1 ? 0 : 1]}`);
  if (!parts.length) return "Nothing identifying was found in the facts.";
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** De-identified text with each placeholder marked, so a reader sees where a name or date was taken out. */
function Placeholders({ text }: { text: string }) {
  return <>{text.split(/(\[[A-Z_]+_\d+\])/).map((part, i) =>
    /^\[[A-Z_]+_\d+\]$/.test(part) ? <span key={i} className="placeholder">{part}</span> : part)}</>;
}

function DraftOrigin({ c, onOpen }: { c: CaseView; onOpen: () => void }) {
  const d = c.draft;
  if (!d) return null;
  if (d.route === "template" || !d.sent) {
    return <p className="support">Written from a template inside Caduceus{d.fallback_reason ? " because the model was not available" : ""}.
      No model was used, so nothing was sent anywhere.</p>;
  }
  return (
    <p className="support">Written by {d.model} on {d.sent.vendor}, from the case facts with every identifier removed.{" "}
      <button type="button" className="link-button" onClick={onOpen}>See what was sent</button></p>
  );
}

function SentPanel({ c, back, heading }: { c: CaseView; back: () => void; heading: RefObject<HTMLHeadingElement | null> }) {
  const d = c.draft!, sent = d.sent!;
  return (
    <aside className="panel side" aria-labelledby="panel-title">
      <button type="button" className="link-button link-button--plain"
              onClick={back}><ChevronLeft size={20} />Back to the draft</button>
      <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">What was sent to the model</h2>
      <p>{d.model} on {sent.vendor} saw only the text below. Caduceus took the patient’s identifiers out first, and put
        them back into the reply here, inside the clinic’s systems.</p>
      <dl className="sent-facts">
        <div><dt>Taken out before sending</dt><dd>{removedWords(d.deid_removed)}</dd></div>
        <div><dt>Checked before it left</dt><dd>The de-identification stamp ({sent.policy}) was verified, and the text
          was scanned again for this patient’s identifiers. None were found.</dd></div>
        <div><dt>Put back here</dt><dd>{sent.tokens_filled_in === 1 ? "1 placeholder" : `${sent.tokens_filled_in} placeholders`} in
          the reply, filled in after it came back.</dd></div>
        <div><dt>The call</dt><dd>{sent.cached ? "A recorded reply to this exact request, reused. No new call was made."
          : `${sent.input_tokens.toLocaleString()} tokens in, ${sent.output_tokens.toLocaleString()} out.`}</dd></div>
      </dl>
      <div className="paper paper--sent"><Placeholders text={sent.text} /></div>
    </aside>
  );
}

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
            <dd className={`facts__value${c.due.urgent ? " facts__value--urgent" : ""}`}>{glue(`${c.due.words}, ${c.due.sub}`)}</dd></div>
          <div className="facts__item"><dt className="facts__term">Requested</dt>
            <dd className="facts__value">{c.service.display}</dd></div>
          <div className="facts__item"><dt className="facts__term">Ordered by</dt>
            <dd className="facts__value">{glue(c.ordering_clinician.name)}, {c.ordering_clinician.specialty}</dd></div>
          {c.payer_clock && (
            <div className="facts__item facts__item--wide"><dt className="facts__term">The payer’s decision</dt>
              <dd className="facts__value">{glue(c.payer_clock.words)}</dd></div>
          )}
        </dl>
        {c.came_by_fax && <p className="case-head__fax">{glue(c.came_by_fax.words)}</p>}
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

function Criteria({ c, open, showing }: { c: CaseView; open: (p: Panel) => void; showing: string | null }) {
  const { met, unknown } = c.criteria.counts;
  const total = c.criteria.total;
  const lead = (met === 0 ? `None of ${total} are met.` : met === total ? `All ${total} are met.`
    : `${countWords(met)} of ${total} ${met === 1 ? "is" : "are"} met.`) +
    (unknown ? ` ${unknown === 1 ? "One needs" : `${countWords(unknown)} need`} ${c.ordering_clinician.name}.` : "");
  const pageTitle = (id: string | null) => c.packet.find((p) => p.id === id)?.title ? midSentence(c.packet.find((p) => p.id === id)!.title) : "page";
  return (
    <section className="panel criteria" aria-labelledby="criteria-title">
      <div>
        <h2 id="criteria-title" className="section-title">Coverage criteria</h2>
        <p className="criteria__lead">{glue(lead)}</p>
      </div>
      <ul>
        {c.criteria.results.map((r: CriterionResult) => {
          const w = criterionWord(r.status);
          const linked = r.evidence.filter((e) => e.document_id);
          const shown = showing === r.text;
          return (
            <li key={r.id} aria-current={shown ? "true" : undefined}
                className={`criterion${r.status === "unknown" ? " criterion--attention" : ""}${shown ? " criterion--showing" : ""}`}>
              <CriterionIcon status={r.status} signed={Boolean(r.attested_by)} />
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
                  <span key={e.document_id} className="criterion__evidence">
                    <button type="button" className="link-button"
                            onClick={() => open({ kind: "page", docId: e.document_id!, sentence: e.sentence, supports: r.text })}>
                      Show the {pageTitle(e.document_id)}, page {e.page}
                    </button>
                    {shown && <span className="tag tag--seal">Showing</span>}
                  </span>
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
        {c.requirements && (c.requirements.matches ? (
          <p>{c.payer.name} confirmed through its coverage-requirements service, at {c.requirements.checked_at}, that
            this needs prior authorization under the same policy version approved here.</p>
        ) : (
          <p className="alert">{c.payer.name} publishes a different version of this policy than the one approved here.
            Have it reviewed before approving.</p>
        ))}
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

/** A faxed page: the text below is what OCR read, so the page itself is a click away. */
function FaxPageImage({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const src = useAuthImage(open ? path : null);
  return (
    <div className="side__fax">
      <p className="support">The text below is what OCR read from the faxed page.{" "}
        <button type="button" className="link-button" aria-expanded={open} aria-controls="fax-page-image"
                onClick={() => setOpen(!open)}>{open ? "Hide the faxed page" : "Show the faxed page"}</button></p>
      {open && (
        <div id="fax-page-image" className="whole-page whole-page--tall" tabIndex={0} role="region" aria-label="The faxed page">
          {src ? <img src={src} alt="The faxed cover sheet" /> : <p className="muted">Opening the page.</p>}
        </div>
      )}
    </div>
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
  // Move focus to the panel's heading when the person changes panels, but not when the case first opens: that
  // would skip the patient's name for a screen reader and scroll past the case header.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) heading.current?.focus();
    opened.current = true;
  }, [panel, page]);

  const pending = c.criteria.results.filter((r) => r.status === "unknown");
  const signed = c.criteria.results.filter((r) => r.attested_by);
  const signedBy = signed[0]?.attested_by;
  const signedAt = c.reply?.attested.find((a) => a.by === signedBy)?.at;

  if (panel.kind === "sent" && c.draft?.sent) return <SentPanel c={c} back={() => setPanel({ kind: "draft" })} heading={heading} />;

  if (panel.kind === "page") {
    return (
      <aside className="panel side" aria-labelledby="panel-title" aria-live="polite">
        <button type="button" className="link-button link-button--plain"
                onClick={() => setPanel({ kind: "draft" })}><ChevronLeft size={20} />Back to the draft</button>
        {!page ? <p role="status" className="muted">{error ?? "Opening the page."}</p> : (
          <>
            <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">{page.title}</h2>
            {panel.sentence && page.text.includes(panel.sentence) && (
              <p className="support side__note">
                <mark>Highlighted</mark> is the sentence that supports: {panel.supports}.
              </p>
            )}
            {page.image && <FaxPageImage path={page.image} />}
            <div className="paper">
              <p className="paper__head">
                <span>Page {page.page} of {page.pages}</span>
                {(page.date || page.author) && <span>{[page.date, page.author].filter(Boolean).join(" · ")}</span>}
              </p>
              <Highlighted text={page.text} sentence={panel.sentence} />
            </div>
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
      {c.draft?.edited_by ? <p className="support">Edited by {c.draft.edited_by}.</p>
        : <DraftOrigin c={c} onOpen={() => setPanel({ kind: "sent" })} />}
      {signedBy ? (
        <div className="signature">
          <div className="signature__text">
            <span className="signature__label">Clinician attestation, signed by</span>
            <span className="signature__name">{glue(signedBy)}</span>
            <span className="signature__meta">
              {signedAt ? `Signed ${signedAt}. ` : ""}{signed.length === 1 ? `Confirmed: ${midSentence(signed[0].text)}.`
                : `Confirmed ${signed.length} criteria.`}
            </span>
          </div>
          <SealMark size={48} />
        </div>
      ) : pending.length ? (
        <div className="signature">
          <div className="signature__text">
            <span className="signature__label">Clinician attestation</span>
            <span className="signature__name signature__name--awaiting">awaiting signature</span>
            <span className="signature__meta">To be signed by {glue(c.ordering_clinician.name)}.</span>
          </div>
          <SealMark size={48} empty />
        </div>
      ) : (
        <div className="signature">
          <div className="signature__text">
            <span className="signature__label">Clinician attestation</span>
            <span className="signature__name signature__name--awaiting">No clinician signature needed</span>
          </div>
        </div>
      )}
      {c.state === "review" && (
        <button type="button" className="btn btn--secondary btn--start"
                onClick={() => { setDraftText(c.draft?.text ?? ""); setPanel({ kind: "edit" }); }}>Edit the draft</button>
      )}
      <div className="packet">
        <h3 className="packet__title" id="packet-title">Referral packet</h3>
        <ul aria-labelledby="packet-title">
          {c.packet.map((p) => (
            <li key={p.id}>
              <button type="button" className="packet__item"
                      onClick={() => setPanel({ kind: "page", docId: p.id, sentence: null, supports: null })}>
                <span className="packet__name">{p.title}</span><span className="support">page&nbsp;{p.page}</span>
                <ChevronRight size={18} />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

function PayerQuestion({ c, reply }: { c: CaseView; reply: PayerReply }) {
  const attested = reply.attested[0];
  return (
    <section className="panel payer-question callout" aria-labelledby="payer-question-title">
      <h2 id="payer-question-title" className="section-title">{c.payer.name} asked a question</h2>
      {reply.question && <p className="payer-question__ask">{reply.question}</p>}
      {attested && !reply.note && (
        <p>No page in the referral packet documents this. {glue(attested.by)} attested to it {glue(attested.at)}, so
          the payer wants it written in the chart.</p>
      )}
      {reply.note ? (
        <figure className="payer-question__note">
          <figcaption className="support">Addendum by {glue(reply.note.author)}, signed {glue(reply.note.signed_at)}</figcaption>
          <div className="paper">{reply.note.text}</div>
        </figure>
      ) : reply.declined ? (
        <p><strong>{reply.declined.by} cannot document this.</strong> They said so {glue(reply.declined.at)}. Nothing was
          sent to {c.payer.name}.</p>
      ) : null}
    </section>
  );
}

function Denial({ c, onSaved }: { c: CaseView; onSaved: (c: CaseView) => void }) {
  const a = c.appeal!;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(a.draft?.text ?? "");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      onSaved(await api<CaseView>(`/api/cases/${c.id}/appeal`, { method: "PUT", body: JSON.stringify({ text }) }));
      setEditing(false);
    } catch (e) {
      setError((e as ApiError).message);
    }
  };
  const how = a.draft?.route === "external" ? `by ${a.draft.model} from de-identified facts` : "from the template";
  return (
    <section className="panel payer-question callout callout--ink" aria-labelledby="denial-title">
      <h2 id="denial-title" className="section-title">{c.payer.name} denied it</h2>
      {a.reason && <p className="payer-question__ask">{a.reason}</p>}
      {a.policy_sentence && (
        <dl className="sent-facts denial__facts">
          <div><dt>What the policy says</dt><dd>{a.policy_sentence}</dd></div>
          <div><dt>What the record shows</dt><dd>{a.record}</dd></div>
        </dl>
      )}
      {a.draft && (
        <figure className="payer-question__note">
          <figcaption className="support">Appeal drafted {how}{a.draft.edited_by ? `, then edited by ${a.draft.edited_by}` : ""}.</figcaption>
          {editing ? (
            <>
              <label className="field-label" htmlFor="appeal-text">The appeal sent to {c.payer.name}</label>
              <textarea id="appeal-text" className="input textarea" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
              {error && <p role="alert" className="alert">{error}</p>}
              <div className="decision-bar__actions">
                <button type="button" className="btn btn--primary" onClick={save}>Save the appeal</button>
                <button type="button" className="btn btn--secondary" onClick={() => { setEditing(false); setText(a.draft?.text ?? ""); }}>Cancel</button>
              </div>
            </>
          ) : (
            <>
              <div className="paper">{a.draft.text}</div>
              {c.actions.can_edit_appeal && (
                <button type="button" className="btn btn--secondary btn--start"
                        onClick={() => { setText(a.draft?.text ?? ""); setEditing(true); }}>Edit the appeal</button>
              )}
            </>
          )}
        </figure>
      )}
    </section>
  );
}

function CaseHistory({ c }: { c: CaseView }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Timeline | null>(null);
  const [check, setCheck] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    api<Timeline>(`/api/cases/${c.id}/timeline`).then(setData).catch((e: ApiError) => setError(e.message));
  }, [open, c.id, c.state, c.version]);

  const verify = async () => {
    setCheck(null);
    try {
      const r = await api<{ intact: boolean; events?: number; message?: string }>("/api/audit/verify");
      setCheck(r.intact ? `Intact. All ${r.events?.toLocaleString()} events in the log check out, each sealed to the one before it.`
        : `Changed: ${r.message}`);
    } catch (e) {
      setCheck((e as ApiError).message);
    }
  };

  return (
    <section className="panel history" aria-labelledby="history-title">
      <div className="history__head">
        <div>
          <h2 id="history-title" className="section-title">Case history</h2>
          <p className="support">Every step, who took it, and when. Each is sealed to the one before it, so a change
            to any of them would show.</p>
        </div>
        <button type="button" className="btn btn--secondary" aria-expanded={open} aria-controls="history-list"
                onClick={() => setOpen(!open)}>{open ? "Hide the history" : "Show the history"}</button>
      </div>
      {open && (
        <div id="history-list" className="history__body">
          {error && <p role="alert" className="alert">{error}</p>}
          {!data ? !error && <p role="status" className="muted">Loading the history.</p> : (
            <>
              <ol className="history__list">
                {data.entries.map((e) => (
                  <li key={e.first_seq} className={`history__item history__item--${e.kind}`}>
                    <span className="history__at">{e.at}</span>
                    <span className="history__who">{e.kind === "refused" && <Triangle size={18} />}<strong>{e.who}</strong>
                      <span className="muted"> {e.role}</span></span>
                    <span className="history__what">{e.what}
                      {e.tools.length > 0 && <span className="support history__tools">Tools: {e.tools.join(", ")}</span>}
                    </span>
                  </li>
                ))}
              </ol>
              <div className="history__verify">
                {data.can_verify
                  ? <button type="button" className="btn btn--secondary" onClick={verify}>Check that nothing was changed</button>
                  : <p className="support">An auditor can check that none of this was changed afterward.</p>}
                {check && <p role="status">{check}</p>}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function DecisionBar({ c, setCase, reload }: { c: CaseView; setCase: (c: CaseView) => void; reload: () => void }) {
  // The bar's real height, so the page keeps a focused field clear of it (WCAG 2.4.11), however much it wraps.
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = bar.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--bar-space", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => { ro.disconnect(); document.documentElement.style.removeProperty("--bar-space"); };
  }, []);
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
      Undo <span className="countdown" aria-hidden="true">({undoLeft} seconds)</span>
    </button>
  ) : null;

  let summary: ReactNode = glue(c.summary);
  let actions: ReactNode = null;
  if (c.read_only) {
    actions = <Link to="/" className="btn btn--primary btn--large">Back to the queue</Link>;
  } else if (c.state === "review") {
    actions = (
      <>
        {c.actions.can_return && (
          <button type="button" className="btn--text" onClick={() => setReturning(true)} aria-expanded={returning}>
            Return for correction
          </button>
        )}
        {/* No disabled "Approve and send" beside it: the summary already says why it cannot be approved yet. */}
        {c.actions.can_request_attestation ? (
          <button type="button" className="btn btn--primary btn--large" disabled={busy} onClick={() => act("request-attestation")}>
            Send to {c.ordering_clinician.name} to attest
          </button>
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
  } else if (c.state === "denied") {
    actions = (
      <>
        {c.actions.can_return && (
          <button type="button" className="btn--text" onClick={() => setReturning(true)} aria-expanded={returning}>
            Return for correction</button>
        )}
        {c.actions.can_send_appeal && (
          <button type="button" className="btn btn--primary btn--large" disabled={busy} onClick={() => act("send-appeal")}>
            Send the appeal to {c.payer.name}</button>
        )}
      </>
    );
  } else if (c.state === "awaiting_note") {
    summary = <><CheckCircle size={26} /><span><strong>Asked {c.ordering_clinician.name} for the note.</strong> This
      case comes back to you when they sign it.</span></>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else if (c.state === "submitted" && !c.submission?.sent) {
    // The countdown is for the eye; a screen reader hears one steady sentence instead of a number every second.
    const what = c.submission?.kind === "reply" ? "note" : c.submission?.kind === "appeal" ? "appeal" : "request";
    summary = <><CheckCircle size={26} /><span><strong>Approved.</strong> Sending
      {c.submission?.kind === "reply" ? " the note" : c.submission?.kind === "appeal" ? " the appeal" : ""} to {c.payer.name}
      <span aria-hidden="true">{sendLeft > 0 ? ` in ${sendLeft} seconds` : " now"}</span>
      <span className="visually-hidden">{undo ? " after 30 seconds, unless you undo it" : c.actions.can_recall ? ", unless you recall it first" : ""}</span>.</span></>;
    // After the undo window, until the outbox sends it, any specialist can still recall it.
    const recall = !undo && c.actions.can_recall ? (
      <button type="button" className="btn btn--secondary btn--large" disabled={busy} onClick={() => act("recall")}>Recall the {what}</button>
    ) : null;
    actions = <>{undo ?? recall}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else if (c.state === "returned") {
    summary = <span><strong>Returned to {c.ordering_clinician.name}.</strong> Reason: {c.returned_reason}</span>;
    actions = <>{undo}<Link to="/" className="btn btn--primary btn--large">Next case</Link></>;
  } else {
    actions = <Link to="/" className="btn btn--primary btn--large">Back to your queue</Link>;
    if (c.state === "approved") summary = <><CheckCircle size={26} /><span><strong>Approved by {c.payer.name}.</strong> Authorization {c.payer_response?.decision.authorization_number}.</span></>;
    if (c.state === "submitted") summary = <span><strong>Sent to {c.payer.name}.</strong> Waiting for their decision.</span>;
  }

  return (
    <div className="decision-bar" role="region" aria-label="Next step" ref={bar}>
      <div className="decision-bar__inner">
        <p id="bar-summary" className="decision-bar__summary" role="status">{summary}</p>
        <div className="decision-bar__actions">{actions}</div>
        {returning && (
          <form className="decision-bar__form" onSubmit={(e) => { e.preventDefault(); void act("return", { reason }); }}>
            <label className="field-label" htmlFor="return-reason">What needs correcting? The clinic will see this.</label>
            <textarea id="return-reason" className="input textarea textarea--short" value={reason}
                      onChange={(e) => setReason(e.target.value)} />
            <div className="decision-bar__actions">
              <button type="submit" className="btn btn--primary" disabled={busy}>Return to {c.ordering_clinician.name}</button>
              <button type="button" className="btn btn--secondary" onClick={() => setReturning(false)}>Cancel</button>
            </div>
          </form>
        )}
        {error && <p role="alert" className="alert decision-bar__error">{error}</p>}
      </div>
    </div>
  );
}

export function CaseReviewPage() {
  const { id } = useParams();
  const { data: c, setData, error, reload } = useResource<CaseView>(`/api/cases/${id}`);
  const [panel, setPanel] = useState<Panel>({ kind: "draft" });
  useTitle("Case review");

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
        {c.appeal && (c.state === "denied" || c.submission?.kind === "appeal") && c.state !== "approved" && (
          <Denial key={c.appeal.draft?.text} c={c} onSaved={setData} />
        )}
        <div className="case-body">
          <Criteria c={c} open={setPanel} showing={panel.kind === "page" ? panel.supports : null} />
          <SidePanel key={`${c.id}-${c.draft?.text?.length}`} c={c} panel={panel} setPanel={setPanel} onSaved={setData} />
        </div>
        <CaseHistory c={c} />
      </div>
      <DecisionBar c={c} setCase={setData} reload={reload} />
    </>
  );
}
