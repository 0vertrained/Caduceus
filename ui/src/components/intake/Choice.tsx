import { useState, type ReactNode } from "react";
import type { FaxChoices, FormField } from "../../api";
import { ReadAs } from "./FieldNotes";

/** The list a field is chosen from: health plans, clinicians (those at the sending clinic first), or services. */
export function Choice({ f, choices, value, onChange, describedBy, invalid, clinic }: {
  f: FormField; choices: FaxChoices; value: string; onChange: (v: string) => void; describedBy: string;
  invalid: boolean; clinic: string | null;
}) {
  const [every, setEvery] = useState(false);
  const atClinic = f.at_clinic ?? [];
  const narrowed = f.key === "clinician" && atClinic.length > 0 && !every && (!value || atClinic.includes(value));
  let options: ReactNode;
  if (f.options) {
    options = f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>);
  } else if (f.key === "payer") {
    options = choices.payers.map((p) => <option key={p} value={p}>{p}</option>);
  } else if (f.key === "service_code") {
    options = choices.services.map((s) => <option key={s.code} value={s.code}>{s.label}</option>);
  } else if (narrowed) {
    options = choices.clinicians.filter((c) => atClinic.includes(c.name))
      .map((c) => <option key={c.name} value={c.name}>{c.name}, {c.specialty}</option>);
  } else {
    const clinics = [...new Set(choices.clinicians.map((c) => c.clinic ?? "Other clinicians on file"))].sort();
    options = clinics.map((name) => (
      <optgroup key={name} label={name}>
        {choices.clinicians.filter((c) => (c.clinic ?? "Other clinicians on file") === name)
          .map((c) => <option key={c.name} value={c.name}>{c.name}, {c.specialty}</option>)}
      </optgroup>
    ));
  }
  const placeholder = { payer: "Choose a health plan", clinician: "Choose a clinician", service_code: "Choose a service",
                        side: "Choose a side" }[f.key];
  return (
    <>
      <select id={`fax-${f.key}`} className="input select" value={value} aria-describedby={describedBy}
              aria-invalid={invalid || undefined} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options}
      </select>
      <ReadAs f={f} />
      {f.key === "clinician" && (narrowed ? (
        <p className="support">Showing the clinicians at {clinic}, where the fax came from.{" "}
          <button type="button" className="link-button" onClick={() => setEvery(true)}>Show every clinician</button></p>
      ) : !clinic ? <p className="support">The clinic that sent the fax could not be read, so every clinician on file is listed.</p> : null)}
    </>
  );
}
