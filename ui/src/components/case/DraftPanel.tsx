import type { RefObject } from "react";
import type { CaseView } from "../../api";
import { glue, midSentence } from "../../hooks";
import { ChevronRight, Triangle } from "../Icons";
import { SealMark } from "../Seal";
import type { Panel } from "./panel";

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

function Signature({ c }: { c: CaseView }) {
  const pending = c.criteria.results.filter((r) => r.status === "unknown");
  const signed = c.criteria.results.filter((r) => r.attested_by);
  const signedBy = signed[0]?.attested_by;
  const signedAt = c.reply?.attested.find((a) => a.by === signedBy)?.at;
  if (signedBy) {
    return (
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
    );
  }
  if (pending.length) {
    return (
      <div className="signature">
        <div className="signature__text">
          <span className="signature__label">Clinician attestation</span>
          <span className="signature__name signature__name--awaiting">awaiting signature</span>
          <span className="signature__meta">To be signed by {glue(c.ordering_clinician.name)}.</span>
        </div>
        <SealMark size={48} empty />
      </div>
    );
  }
  return (
    <div className="signature">
      <div className="signature__text">
        <span className="signature__label">Clinician attestation</span>
        <span className="signature__name signature__name--awaiting">No clinician signature needed</span>
      </div>
    </div>
  );
}

function Packet({ c, open }: { c: CaseView; open: (p: Panel) => void }) {
  return (
    <div className="packet">
      <h3 className="packet__title" id="packet-title">{c.came_from_ehr ? "Newest notes from the EHR" : "Referral packet"}</h3>
      <ul aria-labelledby="packet-title">
        {c.packet.map((p) => (
          <li key={p.id}>
            <button type="button" className="packet__item"
                    onClick={() => open({ kind: "page", docId: p.id, sentence: null, supports: null })}>
              <span className="packet__name">{p.title}</span><span className="support">page&nbsp;{p.page}</span>
              <ChevronRight size={18} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DraftPanel({ c, heading, open, onEdit }: {
  c: CaseView; heading: RefObject<HTMLHeadingElement | null>; open: (p: Panel) => void; onEdit: () => void;
}) {
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
        : <DraftOrigin c={c} onOpen={() => open({ kind: "sent" })} />}
      <Signature c={c} />
      {c.state === "review" && (
        <button type="button" className="btn btn--secondary btn--start" onClick={onEdit}>Edit the draft</button>
      )}
      <Packet c={c} open={open} />
    </aside>
  );
}
