import type { RefObject } from "react";
import type { CaseView } from "../../api";

export function DraftEditor({ c, text, onChange, error, heading, onSave, onCancel }: {
  c: CaseView; text: string; onChange: (text: string) => void; error: string | null;
  heading: RefObject<HTMLHeadingElement | null>; onSave: () => void; onCancel: () => void;
}) {
  return (
    <aside className="panel side" aria-labelledby="panel-title">
      <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">Edit the draft</h2>
      <label className="field-label" htmlFor="draft-text">Request text sent to {c.payer.name}</label>
      <textarea id="draft-text" className="input textarea" rows={12} value={text} onChange={(e) => onChange(e.target.value)} />
      {error && <p role="alert" className="alert">{error}</p>}
      <div className="decision-bar__actions">
        <button type="button" className="btn btn--primary" onClick={onSave}>Save the draft</button>
        <button type="button" className="btn btn--secondary" onClick={onCancel}>Cancel</button>
      </div>
    </aside>
  );
}
