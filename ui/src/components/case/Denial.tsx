import { useState } from "react";
import { api, ApiError, type CaseView } from "../../api";

export function Denial({ c, onSaved }: { c: CaseView; onSaved: (c: CaseView) => void }) {
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
  const how = a.draft && a.draft.route !== "template"
    ? `by ${a.draft.model}${a.draft.route === "deid_in_vpc" ? " inside Mesa Verde’s network" : ""} from de-identified facts`
    : "from the template";
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
