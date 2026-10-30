import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

/** One problem with a form: what to do, the control to take the person to, and a case to open instead, if any. */
export interface FormError { key: string; message: string; target?: string; caseId?: string }

function goTo(target: string) {
  const el = document.getElementById(target);
  if (!el) return;
  el.scrollIntoView({ block: "center" });
  el.focus({ preventScroll: true });
}

/** The GOV.UK error summary: at the top of the page, announced, focused, each problem a link to its field. */
export function ErrorSummary({ errors }: { errors: FormError[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!errors.length) return;
    box.current?.focus();
    box.current?.scrollIntoView({ block: "start" });
  }, [errors]);
  if (!errors.length) return null;
  return (
    <div className="error-summary" role="alert" tabIndex={-1} ref={box} aria-labelledby="error-summary-title">
      <h2 id="error-summary-title" className="error-summary__title">There is a problem</h2>
      <ul className="error-summary__list">
        {errors.map((e) => (
          <li key={`${e.key}-${e.message}`}>
            {e.target ? (
              <a href={`#${e.target}`} onClick={(ev) => { ev.preventDefault(); goTo(e.target!); }}>{e.message}</a>
            ) : <span>{e.message}</span>}
            {e.caseId && <> <Link to={`/cases/${e.caseId}`}>Open case {e.caseId}</Link></>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The message under a field's label, read with the field. */
export function ErrorMessage({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return <p className="error-message" id={id}><span className="visually-hidden">Error: </span>{message}</p>;
}
