import { useEffect, useState } from "react";
import { api, ApiError, type CaseView, type Timeline } from "../../api";
import { Triangle } from "../Icons";

export function CaseHistory({ c }: { c: CaseView }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Timeline | null>(null);
  const [check, setCheck] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    api<Timeline>(`/api/cases/${c.id}/timeline`).then(setData).catch((e: ApiError) => setError(e.message));
  }, [open, c.id, c.state, c.version]);

  const verify = async () => {
    setCheck(null);
    try {
      const r = await api<{ intact: boolean; events?: number; message?: string }>("/api/audit/verify");
      setCheck(r.intact ? `Intact. All ${r.events?.toLocaleString()} events in the log check out, each sealed to the one before it.`
        : `Changed: ${r.message}`);
    } catch (e) {
      setCheck((e as ApiError).message);
    }
  };

  return (
    <section className="panel history" aria-labelledby="history-title">
      <div className="history__head">
        <div>
          <h2 id="history-title" className="section-title">Case history</h2>
          <p className="support">Every step, who took it, and when. Each is sealed to the one before it, so a change
            to any of them would show.</p>
        </div>
        <button type="button" className="btn btn--secondary" aria-expanded={open} aria-controls="history-list"
                onClick={() => setOpen(!open)}>{open ? "Hide the history" : "Show the history"}</button>
      </div>
      {open && (
        <div id="history-list" className="history__body">
          {error && <p role="alert" className="alert">{error}</p>}
          {!data ? !error && <p role="status" className="muted">Loading the history.</p> : (
            <>
              <ol className="history__list">
                {data.entries.map((e) => (
                  <li key={e.first_seq} className={`history__item history__item--${e.kind}`}>
                    <span className="history__at">{e.at}</span>
                    <span className="history__who">{e.kind === "refused" && <Triangle size={18} />}<strong>{e.who}</strong>
                      <span className="muted"> {e.role}</span></span>
                    <span className="history__what">{e.what}
                      {e.tools.length > 0 && <span className="support history__tools">Tools: {e.tools.join(", ")}</span>}
                    </span>
                  </li>
                ))}
              </ol>
              <div className="history__verify">
                {data.can_verify
                  ? <button type="button" className="btn btn--secondary" onClick={verify}>Check that nothing was changed</button>
                  : <p className="support">An auditor can check that none of this was changed afterward.</p>}
                {check && <p role="status">{check}</p>}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
