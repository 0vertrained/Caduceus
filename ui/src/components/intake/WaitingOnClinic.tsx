import type { ClinicRequest } from "../../api";
import { glue, midSentence } from "../../hooks";

/** "a", "a and b", "a, b and c" */
function andList(items: string[]): string {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export function WaitingOnClinic({ request, copied, failure, onCopy, onAnswered }: {
  request: ClinicRequest; copied: string | null; failure: string | null; onCopy: () => void; onAnswered: () => void;
}) {
  return (
    <section className="panel callout asked" aria-labelledby="asked-title">
      <h2 id="asked-title" className="section-title">Waiting on {request.to ?? "the clinic"}</h2>
      <p>{request.asked_by} asked {glue(request.asked)} {request.kind === "resend"
        ? "for the whole referral again, because the copy was too poor to read."
        : `for ${andList(request.missing.map((m) => `the ${midSentence(m)}`))}.`}</p>
      <figure className="asked__request">
        <figcaption className="support">The request, ready to send by fax or phone. Caduceus has not sent it
          anywhere; it is recorded in the fax’s history.</figcaption>
        <div className="paper">{request.text}</div>
      </figure>
      <p>When the clinic answers, choose “The clinic sent it”, then finish checking the fax here.</p>
      <div className="asked__actions">
        <button type="button" className="btn btn--secondary" onClick={onCopy}>Copy the request</button>
        <button type="button" className="btn btn--primary" onClick={onAnswered}>The clinic sent it</button>
      </div>
      {copied && <p role="status" className="support">{copied}</p>}
      {failure && <p role="alert" className="alert">{failure}</p>}
    </section>
  );
}
