import { Clock, Triangle } from "../components/Icons";
import { StatusWord } from "../components/Status";
import { useTitle } from "../hooks";

export function HelpPage() {
  useTitle("Help");
  return (
    <div className="page help">
      <h1 className="page-title">Help</h1>
      <section className="help__section" aria-labelledby="flow">
        <h2 id="flow" className="section-title">How a case moves</h2>
        <ol className="helper__steps help__steps">
          {["Caduceus reads the referral and checks the record against the payer’s coverage policy.",
            "You review each criterion. Every “Met” shows the page and sentence it came from.",
            "If the record does not answer a criterion, the ordering clinician confirms it from their phone.",
            "You approve. For 30 seconds you can undo; then the request goes to the payer.",
            "If the payer asks for a note, the ordering clinician writes and signs it, and you send it the same way."].map((s, i) => (
            <li key={s} className="helper__step"><span className="step-number" aria-hidden="true">{i + 1}</span>{s}</li>
          ))}
        </ol>
      </section>
      <section className="help__section" aria-labelledby="words">
        <h2 id="words" className="section-title">What the statuses mean</h2>
        <ul className="panel help__list">
          {([["ready", "Ready to approve", "Every criterion is met, or the clinician signed the note the payer asked for. You can approve and send."],
             ["needs_clinician", "Needs a clinician", "The record does not answer one or more criteria. Ask the ordering clinician."],
             ["not_met", "Criteria not met", "The record shows a criterion is not met. Return it to the clinic with a reason."],
             ["waiting_clinician", "Waiting on a clinician", "You asked for a signature or a note. The case returns when they sign."],
             ["payer_question", "The payer asked a question", "The payer needs more before deciding. Ask the ordering clinician for the note."],
             ["denied", "Denied by the payer", "The payer said no and gave its reason. Caduceus drafts an appeal from the policy and the record; read it, then send it."]] as const).map(([k, w, d]) => (
            <li key={k} className="waiting__item"><StatusWord statusKey={k} word={w} /><span className="muted">{d}</span></li>
          ))}
        </ul>
      </section>
      <section className="help__section" aria-labelledby="faxes">
        <h2 id="faxes" className="section-title">Faxed referrals</h2>
        <ul className="panel help__list">
          <li className="waiting__item"><span className="status status--caution"><Triangle size={20} /><span>Fields to check</span></span>
            <span className="muted">Caduceus was unsure of some fields. Tick each one that matches the fax, or correct it,
              and confirm the patient in the chart. Then create the case.</span></li>
          <li className="waiting__item"><span className="status status--ink"><Clock size={20} /><span>Waiting on the clinic</span></span>
            <span className="muted">The fax does not give something, or is too poor to read, so you asked the clinic. Send
              them the request Caduceus wrote. When they answer, choose “The clinic sent it” and finish checking.</span></li>
        </ul>
      </section>
      <section className="help__section" aria-labelledby="contact">
        <h2 id="contact" className="section-title">Still stuck?</h2>
        <p>Call the Caduceus support desk at extension 4100, weekdays 7:00 am to 6:00 pm. Have the case number ready;
          it is under “More details” on every case.</p>
      </section>
    </div>
  );
}
