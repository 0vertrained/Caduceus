import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type CaseView } from "../../api";
import { glue, useSecondsLeft } from "../../hooks";
import { CheckCircle } from "../Icons";
import { submitOnModEnter } from "./submitOnModEnter";

/** The bar's real height, so the page keeps a focused field clear of it (WCAG 2.4.11), however much it wraps. */
function useBarSpace() {
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = bar.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--bar-space", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => { ro.disconnect(); document.documentElement.style.removeProperty("--bar-space"); };
  }, []);
  return bar;
}

export function DecisionBar({ c, setCase, reload }: { c: CaseView; setCase: (c: CaseView) => void; reload: () => void }) {
  const bar = useBarSpace();
  const [busy, setBusy] = useState<string | null>(null);  // the action under way, so its button shows it
  const [error, setError] = useState<string | null>(null);
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState("");
  const undoLeft = useSecondsLeft(c.undo?.until);
  const sendLeft = useSecondsLeft(c.state === "submitted" && !c.submission?.sent ? c.submission?.send_after : null);

  useEffect(() => {
    if (c.undo && undoLeft === 0) reload();
  }, [undoLeft, c.undo, reload]);

  async function act(path: string, body?: unknown) {
    setBusy(path);
    setError(null);
    try {
      setCase(await api<CaseView>(`/api/cases/${c.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      setReturning(false);
      setReason("");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  }
  // Every button waits while one action runs; the one that started it says so.
  const pending = (path: string) => ({ disabled: busy !== null, "data-busy": busy === path || undefined });

  const undo = c.undo && undoLeft > 0 ? (
    <button type="button" className="btn btn--secondary btn--large" {...pending("undo")} onClick={() => act("undo")}>
      Undo <span className="countdown" aria-hidden="true">({undoLeft} seconds)</span>
    </button>
  ) : null;

  // While the case waits on the clinician: a nudge, at most every 10 minutes (the summary says when the next can go).
  const remind = c.actions.can_remind ? (
    <button type="button" className="btn btn--secondary btn--large" {...pending("remind")} onClick={() => act("remind")}>
      Remind {c.ordering_clinician.name}</button>
  ) : null;
  const returnButton = (primary = false) => (
    <button type="button" className={primary ? "btn btn--primary btn--large" : "btn--text"}
            onClick={() => setReturning(true)} aria-expanded={returning}>Return for correction</button>
  );
  const nextCase = <Link to="/" className="btn btn--primary btn--large">Next case</Link>;
  let summary: ReactNode = glue(c.summary);
  let actions: ReactNode = null;
  if (c.read_only) {
    actions = <Link to="/" className="btn btn--primary btn--large">Back to the queue</Link>;
  } else if (c.state === "review") {
    actions = (
      <>
        {c.actions.can_return && returnButton()}
        {/* No disabled "Approve and send" beside it: the summary already says why it cannot be approved yet. */}
        {c.actions.can_request_attestation ? (
          <button type="button" className="btn btn--primary btn--large" {...pending("request-attestation")} onClick={() => act("request-attestation")}>
            Send to {c.ordering_clinician.name} to attest
          </button>
        ) : (
          <button type="button" className="btn btn--primary btn--large" {...pending("approve")}
                  disabled={busy !== null || !c.actions.can_approve}
                  aria-describedby="bar-summary" onClick={() => act("approve")}>Approve and send</button>
        )}
      </>
    );
  } else if (c.state === "awaiting_attestation") {
    summary = <><CheckCircle size={26} /><span><strong>Sent to {c.ordering_clinician.name}.</strong> This case comes
      back to you when they sign. {glue(c.waiting?.words ?? "")}</span></>;
    actions = <>{undo}{remind}{nextCase}</>;
  } else if (c.state === "pended") {
    actions = (
      <>
        {c.actions.can_send_note ? (
          <>{c.actions.can_return && returnButton()}
            <button type="button" className="btn btn--primary btn--large" {...pending("send-note")} onClick={() => act("send-note")}>
              Send the note to {c.payer.name}
            </button></>
        ) : c.actions.can_ask_for_note ? (
          <>{c.actions.can_return && returnButton()}
            <button type="button" className="btn btn--primary btn--large" {...pending("request-note")} onClick={() => act("request-note")}>
              Ask {c.ordering_clinician.name} for the note
            </button></>
        ) : c.actions.can_return ? returnButton(true) : null}
      </>
    );
  } else if (c.state === "denied") {
    actions = (
      <>
        {c.actions.can_return && returnButton()}
        {c.actions.can_send_appeal && (
          <button type="button" className="btn btn--primary btn--large" {...pending("send-appeal")} onClick={() => act("send-appeal")}>
            Send the appeal to {c.payer.name}</button>
        )}
      </>
    );
  } else if (c.state === "awaiting_note") {
    summary = <><CheckCircle size={26} /><span><strong>Asked {c.ordering_clinician.name} for the note.</strong> This
      case comes back to you when they sign it. {glue(c.waiting?.words ?? "")}</span></>;
    actions = <>{undo}{remind}{nextCase}</>;
  } else if (c.state === "submitted" && !c.submission?.sent) {
    // The countdown is for the eye; a screen reader hears one steady sentence instead of a number every second.
    const what = c.submission?.kind === "reply" ? "note" : c.submission?.kind === "appeal" ? "appeal" : "request";
    summary = <><CheckCircle size={26} /><span><strong>Approved.</strong> Sending
      {c.submission?.kind === "reply" ? " the note" : c.submission?.kind === "appeal" ? " the appeal" : ""} to {c.payer.name}
      <span aria-hidden="true">{sendLeft > 0 ? ` in ${sendLeft} seconds` : " now"}</span>
      <span className="visually-hidden">{undo ? " after 30 seconds, unless you undo it" : c.actions.can_recall ? ", unless you recall it first" : ""}</span>.</span></>;
    // After the undo window, until the outbox sends it, any specialist can still recall it.
    const recall = !undo && c.actions.can_recall ? (
      <button type="button" className="btn btn--secondary btn--large" {...pending("recall")} onClick={() => act("recall")}>Recall the {what}</button>
    ) : null;
    actions = <>{undo ?? recall}{nextCase}</>;
  } else if (c.state === "returned") {
    summary = <span><strong>Returned to {c.ordering_clinician.name}.</strong> Reason: {c.returned_reason}</span>;
    actions = (
      <>
        {undo}
        {c.actions.can_resubmit && (
          <button type="button" className="btn btn--secondary btn--large" {...pending("resubmitted")} onClick={() => act("resubmitted")}>
            The clinic resubmitted</button>
        )}
        {nextCase}
      </>
    );
  } else {
    actions = <Link to="/" className="btn btn--primary btn--large">Back to your queue</Link>;
    if (c.state === "approved") summary = <><CheckCircle size={26} /><span><strong>Approved by {c.payer.name}.</strong> Authorization {c.payer_response?.decision.authorization_number}.</span></>;
    if (c.state === "submitted") summary = <span><strong>Sent to {c.payer.name}.</strong>{" "}
      {c.payer_review ? glue(c.payer_review.words) : "Waiting for its decision."}</span>;
  }

  return (
    <div className="decision-bar" role="region" aria-label="Next step" ref={bar}>
      <div className="decision-bar__inner">
        <p id="bar-summary" className="decision-bar__summary" role="status">{summary}</p>
        <div className="decision-bar__actions">{actions}</div>
        {returning && (
          <form className="decision-bar__form" onSubmit={(e) => { e.preventDefault(); void act("return", { reason }); }}>
            <label className="field-label" htmlFor="return-reason">What needs correcting? The clinic will see this.</label>
            <textarea id="return-reason" className="input textarea textarea--short" value={reason}
                      onChange={(e) => setReason(e.target.value)} onKeyDown={submitOnModEnter} />
            <div className="decision-bar__actions">
              <button type="submit" className="btn btn--primary" {...pending("return")}>Return to {c.ordering_clinician.name}</button>
              <button type="button" className="btn btn--secondary" onClick={() => setReturning(false)}>Cancel</button>
            </div>
          </form>
        )}
        {error && <p role="alert" className="alert decision-bar__error">{error}</p>}
      </div>
    </div>
  );
}
