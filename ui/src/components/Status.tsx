import type { StatusKey } from "../api";
import { CheckCircle, Clock, CrossCircle, Pencil, Triangle } from "./Icons";
import { SealMark } from "./Seal";

// The status vocabulary from the design system: one icon, one word, one color per meaning.
const LOOK: Record<StatusKey, { tone: string; Icon: typeof CheckCircle }> = {
  ready: { tone: "seal", Icon: CheckCircle },
  approved: { tone: "seal", Icon: CheckCircle },
  needs_clinician: { tone: "caution", Icon: Triangle },
  payer_question: { tone: "caution", Icon: Triangle },
  not_met: { tone: "ink", Icon: CrossCircle },
  denied: { tone: "ink", Icon: CrossCircle },
  returned: { tone: "ink", Icon: Pencil },
  waiting_clinician: { tone: "ink", Icon: Clock },
  sending: { tone: "ink", Icon: Clock },
  waiting_payer: { tone: "muted", Icon: Clock },
  preparing: { tone: "muted", Icon: Clock },
};

export function StatusWord({ statusKey, word, pill = false }: { statusKey: StatusKey; word: string; pill?: boolean }) {
  const { tone, Icon } = LOOK[statusKey];
  return (
    <span className={`status status--${tone}${pill ? " status-pill" : ""}`}>
      <Icon size={20} />
      <span>{word}</span>
    </span>
  );
}

const CRITERION = {
  met: { tone: "seal", Icon: CheckCircle, word: "Met" },
  unknown: { tone: "caution", Icon: Triangle, word: "Not found" },
  unmet: { tone: "ink", Icon: CrossCircle, word: "Not met" },
} as const;

/** A criterion's icon. One a clinician signed for carries the seal instead of a plain check. */
export function CriterionIcon({ status, signed = false }: { status: keyof typeof CRITERION; signed?: boolean }) {
  const { tone, Icon } = CRITERION[status];
  return (
    <span className={`status status--${tone} criterion__icon`}>
      {signed && status === "met" ? <SealMark size={26} /> : <Icon size={26} />}
    </span>
  );
}

export function criterionWord(status: keyof typeof CRITERION) {
  return CRITERION[status];
}
