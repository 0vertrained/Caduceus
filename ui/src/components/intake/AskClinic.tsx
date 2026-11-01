import type { FormField } from "../../api";

export function AskClinic({ asking, clinic, onAsk }: { asking: FormField[]; clinic: string | null; onAsk: () => void }) {
  return (
    <section className="ask" id="ask-clinic" tabIndex={-1} aria-labelledby="ask-title">
      <h2 id="ask-title" className="match__title">Ask {clinic ?? "the clinic that sent it"}</h2>
      <p>The fax does not clearly give:</p>
      <ul className="ask__list">{asking.map((f) => <li key={f.key}>{f.label}</li>)}</ul>
      <p className="support">Caduceus writes the request, naming the referral and {asking.length === 1 ? "this field" : "these fields"} but
        not the patient. You send it by fax or phone; nothing is sent for you. The fax waits here until the clinic answers.</p>
      <button type="button" className="btn btn--primary btn--start" onClick={onAsk}>
        Ask the clinic for {asking.length === 1 ? "it" : `these ${asking.length}`}</button>
    </section>
  );
}
