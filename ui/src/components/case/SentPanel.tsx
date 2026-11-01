import type { RefObject } from "react";
import type { CaseView } from "../../api";
import { andList } from "../../hooks";
import { ChevronLeft } from "../Icons";

const REMOVED: Record<string, [string, string]> = {
  NAME: ["name part", "name parts"], DATE: ["date", "dates"], ADDRESS: ["street address", "street addresses"],
  CITY: ["city", "cities"], ZIP: ["ZIP code", "ZIP codes"], PHONE: ["phone number", "phone numbers"],
  EMAIL: ["email address", "email addresses"], RECORD_NUMBER: ["record number", "record numbers"],
  SSN: ["Social Security number", "Social Security numbers"], URL: ["web address", "web addresses"],
};

function removedWords(removed: Record<string, number> = {}): string {
  const parts = Object.entries(removed).filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${(REMOVED[k] ?? [k.toLowerCase(), k.toLowerCase()])[n === 1 ? 0 : 1]}`);
  return parts.length ? andList(parts) : "Nothing identifying was found in the facts.";
}

/** De-identified text with each placeholder marked, so a reader sees where a name or date was taken out. */
function Placeholders({ text }: { text: string }) {
  return <>{text.split(/(\[[A-Z_]+_\d+\])/).map((part, i) =>
    /^\[[A-Z_]+_\d+\]$/.test(part) ? <span key={i} className="placeholder">{part}</span> : part)}</>;
}

export function SentPanel({ c, onBack, heading }: {
  c: CaseView; onBack: () => void; heading: RefObject<HTMLHeadingElement | null>;
}) {
  const d = c.draft!, sent = d.sent!;
  return (
    <aside className="panel side" aria-labelledby="panel-title">
      <button type="button" className="link-button link-button--plain"
              onClick={onBack}><ChevronLeft size={20} />Back to the draft</button>
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
