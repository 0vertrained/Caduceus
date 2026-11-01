import type { FormField } from "../../api";
import { CheckCircle, Triangle } from "../Icons";

export function Why({ f }: { f: FormField }) {
  return f.problem
    ? <p className="intake__why" id={`why-${f.key}`}><Triangle size={18} />{f.problem}</p>
    : <p className="intake__why intake__why--ok" id={`why-${f.key}`}><CheckCircle size={18} />Read clearly ({Math.round(f.confidence * 100)}%)</p>;
}

export function ReadAs({ f }: { f: FormField }) {
  if (!f.read_as) return null;
  const nearest = f.kind === "select" && f.problem && f.initial;
  return <p className="support intake__read-as">Read as “{f.read_as}”.{nearest ? " The list starts on the closest one on file." : ""}</p>;
}
