import { useEffect, useRef, useState } from "react";
import { api, ApiError, type CaseView } from "../../api";
import { ErrorMessage } from "../ErrorSummary";
import { submitOnModEnter } from "./submitOnModEnter";

/** Standard or expedited, and the clinical reason for expediting. A PA specialist can change it until approval. */
export function Urgency({ c, setCase }: { c: CaseView; setCase: (c: CaseView) => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  const expedited = c.urgency === "expedited";
  const reopened = useRef(false);

  // Opening moves focus into the form; closing returns it to the button that opened it.
  useEffect(() => {
    if (open) (expedited ? confirm : field).current?.focus();
    else if (reopened.current) trigger.current?.focus();
    reopened.current = open;
  }, [open, expedited]);

  const close = () => { setOpen(false); setReason(""); setError(null); };
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      setCase(await api<CaseView>(`/api/cases/${c.id}/urgency`, { method: "POST",
        body: JSON.stringify({ expedited: !expedited, reason: expedited ? "" : reason }) }));
      setSaid(expedited ? "Changed to a standard request." : "Marked expedited.");
      close();
    } catch (e) {
      setError((e as ApiError).message);
      field.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`facts__item${expedited || open ? " facts__item--wide" : ""}`}>
      <dt className="facts__term">Urgency</dt>
      <dd className="facts__value urgency">
        {expedited ? <p><strong className="urgency__word">Expedited.</strong> <span className="urgency__reason">{c.urgency_reason}</span></p>
          : <p>Standard</p>}
        {c.actions.can_set_urgency && !open && (
          <button type="button" ref={trigger} className="link-button link-button--start" aria-expanded={false}
                  aria-controls="urgency-form" onClick={() => setOpen(true)}>
            {expedited ? "Change to standard…" : "Ask for an expedited decision…"}
          </button>
        )}
        {open && (
          <form id="urgency-form" className="urgency__form" noValidate
                onSubmit={(e) => { e.preventDefault(); void submit(); }}>
            {expedited ? (
              <p>The request will go to {c.payer.name} as a standard request, and the reason above will be removed.</p>
            ) : (
              <>
                <label className="field-label" htmlFor="urgency-reason">Why can’t this wait the standard time?</label>
                <p className="support" id="urgency-hint">Expedite only when waiting could seriously jeopardize the
                  patient’s life, health or ability to regain maximum function. Give the clinical reason, as the
                  ordering clinician states it.</p>
                <ErrorMessage id="urgency-error" message={error ?? undefined} />
                <textarea id="urgency-reason" ref={field} name="urgency-reason" className="input textarea textarea--short"
                          value={reason} aria-describedby={`urgency-hint${error ? " urgency-error" : ""}`}
                          aria-invalid={error ? true : undefined} maxLength={500}
                          onChange={(e) => setReason(e.target.value)} onKeyDown={submitOnModEnter} />
              </>
            )}
            {expedited && error && <p role="alert" className="alert">{error}</p>}
            <div className="decision-bar__actions">
              <button type="submit" ref={confirm} className="btn btn--primary" disabled={busy} data-busy={busy || undefined}>
                {expedited ? "Change to standard" : "Ask for an expedited decision"}</button>
              <button type="button" className="btn btn--secondary" onClick={close}>Cancel</button>
            </div>
          </form>
        )}
        <span className="visually-hidden" role="status">{said}</span>
      </dd>
    </div>
  );
}
