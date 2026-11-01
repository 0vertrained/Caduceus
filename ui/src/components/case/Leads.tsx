import type { CaseView, CriterionResult } from "../../api";
import { glue, midSentence, oneLine } from "../../hooks";
import type { PagePanel, Panel } from "./panel";

/** Packet passages that may answer a criterion the record could not: where to look, never evidence. */
export function Leads({ c, r, open, showing }: {
  c: CaseView; r: CriterionResult; open: (p: Panel) => void; showing: PagePanel | null;
}) {
  return (
    <div className="leads">
      <p className="leads__title" id={`leads-${r.id}`}>The packet may answer this</p>
      <ul className="leads__list" aria-labelledby={`leads-${r.id}`}>
        {r.leads.map((l) => {
          const on = showing?.docId === l.document_id && showing.lead?.start === l.start;
          return (
            <li key={`${l.document_id}:${l.start}`} className="leads__item">
              <q className="leads__text">{oneLine(l.text)}</q>
              <span className="criterion__evidence">
                <button type="button" className="link-button"
                        onClick={() => open({ kind: "page", docId: l.document_id, sentence: l.text, supports: r.text,
                                              lead: { start: l.start, end: l.end } })}>
                  {glue(`Show the ${midSentence(l.title)}, page ${l.page}`)}
                </button>
                {on && <span className="tag tag--seal">Showing</span>}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="support">Found by searching the packet. Only {glue(c.ordering_clinician.name)}’s answer can mark
        this met.</p>
    </div>
  );
}
