import type { CaseView } from "../../api";
import { glue } from "../../hooks";
import { StatusWord } from "../Status";
import { Progress } from "./Progress";
import { Urgency } from "./Urgency";

export function CaseHeader({ c, setCase }: { c: CaseView; setCase: (c: CaseView) => void }) {
  return (
    <section className="panel case-head" aria-labelledby="patient-name">
      <div className="case-head__main">
        <div className="case-head__title-row">
          <h1 id="patient-name" className="case-name">{c.patient.name}</h1>
          <StatusWord statusKey={c.status.key} word={c.status.word} pill />
          {c.urgency === "expedited" && <span className="tag tag--urgent">Expedited</span>}
        </div>
        <dl className="facts">
          <div className="facts__item"><dt className="facts__term">Date of birth</dt>
            <dd className="facts__value">{c.patient.birth_date} (age {c.patient.age})</dd></div>
          <div className="facts__item"><dt className="facts__term">Due</dt>
            <dd className={`facts__value${c.due.urgent ? " facts__value--urgent" : ""}`}>{glue(`${c.due.words}, ${c.due.sub}`)}</dd></div>
          <div className="facts__item"><dt className="facts__term">Requested</dt>
            <dd className="facts__value">{c.service.display}</dd></div>
          {c.side && (
            <div className="facts__item"><dt className="facts__term">Side</dt>
              <dd className="facts__value">{c.side === "right" ? "Right" : "Left"}</dd></div>
          )}
          <div className="facts__item"><dt className="facts__term">Ordered by</dt>
            <dd className="facts__value">{glue(c.ordering_clinician.name)}, {c.ordering_clinician.specialty}</dd></div>
          <Urgency c={c} setCase={setCase} />
          {c.payer_clock && (
            <div className="facts__item facts__item--wide"><dt className="facts__term">The payer’s decision</dt>
              <dd className="facts__value">{glue(c.payer_clock.words)}</dd></div>
          )}
        </dl>
        {c.came_by_fax && <p className="case-head__fax">{glue(c.came_by_fax.words)}</p>}
        {c.came_from_ehr && <p className="case-head__fax">{glue(c.came_from_ehr.words)}</p>}
        <details className="details">
          <summary className="details__summary">More details about this request</summary>
          <dl className="facts">
            <div className="facts__item"><dt className="facts__term">Payer</dt><dd className="facts__value">{c.payer.name}</dd></div>
            <div className="facts__item"><dt className="facts__term">Member ID</dt><dd className="facts__value">{c.payer.member_id_last4
              ? `Ending in ${c.payer.member_id_last4}` : "Not given: the EHR shared the clinical record only"}</dd></div>
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
