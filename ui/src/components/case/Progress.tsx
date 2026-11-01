import type { CaseView } from "../../api";
import { CheckCircle } from "../Icons";

const STEPS = ["Packet checked", "Review the criteria", "Approve the draft", "Sent to the payer"];

function stepIndex(c: CaseView): number {
  if (["approved", "pended", "denied"].includes(c.state) || c.submission?.sent) return 4;
  if (c.state === "submitted") return 3;
  if (c.actions.can_approve) return 2;
  return 1;
}

export function Progress({ c }: { c: CaseView }) {
  const at = stepIndex(c);
  return (
    <ol className="progress" aria-label="Progress">
      {STEPS.map((s, i) => {
        const done = i < at, current = i === at;
        return (
          <li key={s} className={`progress__step${done ? " progress__step--done" : ""}${current ? " progress__step--current" : ""}`}
              aria-current={current ? "step" : undefined}>
            {done ? <CheckCircle size={24} /> : <span className="progress__marker" aria-hidden="true" />}
            <span>{s}{done ? <span className="visually-hidden">, done</span> : null}</span>
          </li>
        );
      })}
    </ol>
  );
}
