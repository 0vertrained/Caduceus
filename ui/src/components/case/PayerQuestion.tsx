import type { CaseView, PayerReply } from "../../api";
import { glue } from "../../hooks";

export function PayerQuestion({ c, reply }: { c: CaseView; reply: PayerReply }) {
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
