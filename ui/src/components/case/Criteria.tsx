import type { CaseView, CriterionResult } from "../../api";
import { countWords, glue, midSentence } from "../../hooks";
import { CriterionIcon, criterionWord } from "../Status";
import { Leads } from "./Leads";
import type { PagePanel, Panel } from "./panel";

export function Criteria({ c, open, showing }: { c: CaseView; open: (p: Panel) => void; showing: PagePanel | null }) {
  const { met, unknown } = c.criteria.counts;
  const total = c.criteria.total;
  const lead = (met === 0 ? `None of ${total} are met.` : met === total ? `All ${total} are met.`
    : `${countWords(met)} of ${total} ${met === 1 ? "is" : "are"} met.`) +
    (unknown ? ` ${unknown === 1 ? "One needs" : `${countWords(unknown)} need`} ${c.ordering_clinician.name}.` : "");
  return (
    <section className="panel criteria" aria-labelledby="criteria-title">
      <div>
        <h2 id="criteria-title" className="section-title">Coverage criteria</h2>
        <p className="criteria__lead">{glue(lead)}</p>
      </div>
      <ul>
        {c.criteria.results.map((r) => <Criterion key={r.id} c={c} r={r} open={open} showing={showing} />)}
      </ul>
      <Provenance c={c} />
    </section>
  );
}

function pageTitle(c: CaseView, id: string | null): string {
  const title = c.packet.find((p) => p.id === id)?.title;
  return title ? midSentence(title) : "page";
}

function Criterion({ c, r, open, showing }: {
  c: CaseView; r: CriterionResult; open: (p: Panel) => void; showing: PagePanel | null;
}) {
  const w = criterionWord(r.status);
  const evidence = r.evidence.find((e) => e.document_id);
  const shown = showing?.supports === r.text;
  return (
    <li aria-current={shown ? "true" : undefined}
        className={`criterion${r.status === "unknown" ? " criterion--attention" : ""}${shown ? " criterion--showing" : ""}`}>
      <CriterionIcon status={r.status} signed={Boolean(r.attested_by)} />
      <div className="criterion__body">
        <span className="criterion__title">{r.text}
          <span className={`criterion__word status--${w.tone}`}>{r.attested_by ? (r.status === "met" ? "Met, signed" : "Not met, signed") : w.word}</span>
        </span>
        <span className="muted">
          {r.attested_by ? (r.status === "met" ? `${r.attested_by} confirmed this.`
            : `${r.attested_by} said this was not done.`) :
            r.status === "unknown" ? "The record does not say either way, so nothing was assumed." : r.explanation}
        </span>
        {evidence && (
          <span className="criterion__evidence">
            <button type="button" className="link-button"
                    onClick={() => open({ kind: "page", docId: evidence.document_id!, sentence: evidence.sentence, supports: r.text })}>
              Show the {pageTitle(c, evidence.document_id)}, page {evidence.page}
            </button>
            {shown && !showing?.lead && <span className="tag tag--seal">Showing</span>}
          </span>
        )}
        {r.leads.length > 0 && <Leads c={c} r={r} open={open} showing={showing} />}
      </div>
    </li>
  );
}

function Provenance({ c }: { c: CaseView }) {
  return (
    <div className="provenance">
      <p>
        Checked today at {c.criteria.checked_at} against the {c.criteria.version} version of the coverage policy for{" "}
        {midSentence(c.criteria.title)}
        {c.criteria.source ? (
          <>, compiled from Medicare’s{" "}
            {c.criteria.source.url ? (
              <a href={c.criteria.source.url} target="_blank" rel="noopener noreferrer" translate="no">
                {c.criteria.source.document}<span className="visually-hidden"> (opens in a new tab)</span></a>
            ) : <span translate="no">{c.criteria.source.document}</span>}
            {c.criteria.source.retrieved ? `, retrieved ${c.criteria.source.retrieved}` : ""}</>
        ) : c.criteria.source_kind === "development-paraphrase" ? " (development criteria)" : ""}. A criterion is
        marked met only when its evidence is found in the record.
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
  );
}
