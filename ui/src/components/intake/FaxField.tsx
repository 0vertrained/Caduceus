import type { FaxView, FormField, PatientMatch } from "../../api";
import { ErrorMessage, type FormError } from "../ErrorSummary";
import { Crop, WholePage } from "../FaxImage";
import { Triangle } from "../Icons";
import { Choice } from "./Choice";
import { Differs } from "./Differs";
import { DATE_PARTS, joinDate, NAMED, splitDate, type DateParts } from "./fields";
import { ReadAs, Why } from "./FieldNotes";

export function FaxField({ f, fax, image, error, value, onValue, dob, onDob, onFile, ticked, changed, onTick, absent,
                           onAbsent }: {
  f: FormField; fax: FaxView; image: string | null; error: FormError | undefined;
  value: string; onValue: (value: string) => void; dob: DateParts; onDob: (dob: DateParts) => void;
  onFile: PatientMatch["on_file"]; ticked: boolean; changed: boolean; onTick: (on: boolean) => void;
  absent: boolean; onAbsent: (on: boolean) => void;
}) {
  const errId = `err-${f.key}`;
  const describedBy = [`why-${f.key}`, error && error.target !== `tick-${f.key}` ? errId : ""].filter(Boolean).join(" ");
  const invalid = Boolean(error && error.target !== `tick-${f.key}`);
  // The line it was read from, or the whole page when the line could not be found at all.
  const named = NAMED[f.key === "side" ? "diagnosis" : f.key];  // the side is read from the diagnosis
  const crop = f.box ? <Crop src={image} size={fax.size} box={f.box} label={named} />
    : <WholePage src={image} label={named} />;
  return (
    <div className={`intake__field${f.problem ? " intake__field--check" : ""}${error ? " intake__field--error" : ""}`}>
      {f.kind === "date" ? (
        <fieldset className="fieldset intake__date" aria-describedby={`hint-dob ${describedBy}`}>
          <legend className="field-label">{f.label}</legend>
          <p className="support" id="hint-dob">For example, 03 09 1941</p>
          <Why f={f} />
          {f.problem && crop}
          <ErrorMessage id={errId} message={error?.message} />
          <div className="date-input">
            {DATE_PARTS.map(([k, label]) => (
              <div key={k} className="date-input__item">
                <label className="date-input__label" htmlFor={`dob-${k}`}>{label}</label>
                <input id={`dob-${k}`} className={`input date-input__${k}`} inputMode="numeric" autoComplete="off"
                       value={dob[k]} aria-invalid={invalid || undefined}
                       onChange={(e) => onDob({ ...dob, [k]: e.target.value })} />
              </div>
            ))}
          </div>
          {f.read_as && joinDate(splitDate(f.initial)) !== f.read_as && <ReadAs f={f} />}
        </fieldset>
      ) : (
        <>
          <label className="field-label" htmlFor={`fax-${f.key}`}>{f.label}</label>
          <Why f={f} />
          {f.problem && crop}
          <ErrorMessage id={errId} message={error?.message} />
          {f.kind === "select" ? (
            <Choice f={f} choices={fax.choices} value={value} describedBy={describedBy}
                    invalid={invalid} clinic={fax.clinic} onChange={onValue} />
          ) : (
            <input id={`fax-${f.key}`} className="input" value={value} autoComplete="off" spellCheck={false}
                   aria-describedby={describedBy} aria-invalid={invalid || undefined}
                   onChange={(e) => onValue(e.target.value)} />
          )}
        </>
      )}
      {f.key === "payer" && onFile && value && value !== onFile.payer && (
        <Differs label="The health plan" fax={value} record={onFile.payer} source="On file for this patient"
                 use="Use the plan on file" onUse={() => onValue(onFile.payer)} />
      )}
      {f.key === "member_id" && onFile && /^[A-Z]{2}\d{9}$/.test(value) && value.slice(-4) !== onFile.member_id_last4 && (
        <div className="differs"><p><strong>The member ID differs.</strong> This one ends in {value.slice(-4)}; the
          one on file for this patient ends in {onFile.member_id_last4}. Check it against the fax.</p></div>
      )}
      {f.problem && (absent ? (
        <p className="intake__absent"><Triangle size={18} /><span>Not on the fax. The clinic will be asked for
          it below. <button type="button" className="link-button" onClick={() => onAbsent(false)}>It
          is on the fax<span className="visually-hidden">: the {NAMED[f.key]}</span></button></span></p>
      ) : (
        <div className="intake__tick">
          <label className={`tick${ticked ? " tick--on" : ""}`} htmlFor={`tick-${f.key}`}>
            <input type="checkbox" id={`tick-${f.key}`} checked={ticked}
                   aria-describedby={error?.target === `tick-${f.key}` ? errId : undefined}
                   onChange={(e) => onTick(e.target.checked)} />
            <span>Matches the fax<span className="visually-hidden">: {NAMED[f.key]}</span></span>
          </label>
          {changed && !ticked ? <span className="support">You changed this, so it counts as checked.</span>
            : !ticked && (
              <button type="button" className="link-button" onClick={() => onAbsent(true)}>
                Not on the fax: ask the clinic<span className="visually-hidden"> for the {NAMED[f.key]}</span></button>
            )}
        </div>
      ))}
    </div>
  );
}
