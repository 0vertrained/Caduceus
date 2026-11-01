import type { ReactNode } from "react";
import type { ChartCard, PatientMatch } from "../../api";
import { CheckCircle } from "../Icons";
import { Differs } from "./Differs";
import { sameName } from "./fields";

/** The patient in the chart: the one record with this name and date of birth, confirmed by a person, or the closest
 *  few to choose from. Only what the record holds is compared. */
export function PatientCheck({ match, looking, failure, name, born, confirmed, onLook, onConfirm, onUseName, onUseBorn }: {
  match: PatientMatch | null; looking: boolean; failure: string | null; name: string; born: string; confirmed: boolean;
  onLook: (ref?: string) => void; onConfirm: (ref: string | null) => void; onUseName: (c: ChartCard) => void;
  onUseBorn: (c: ChartCard) => void;
}) {
  const chart = match?.patient ?? null;
  const nameDiffers = chart ? !sameName(name, chart.name) : false;
  const bornDiffers = chart ? born !== chart.birth_date : false;
  const where = (c: ChartCard) => `born ${c.born}${c.city ? `, ${c.city}` : ""}`;
  let body: ReactNode;
  if (looking) {
    body = <p role="status" className="muted">Looking in the chart.</p>;
  } else if (!match) {
    body = (
      <>
        <p className="support">Look the patient up by the name and date of birth above, so the case is for the right person.</p>
        <button type="button" className="btn btn--secondary btn--start" onClick={() => onLook()}>Find the patient in the chart</button>
      </>
    );
  } else if (chart) {
    body = (
      <>
        <p className="match__who">{nameDiffers || bornDiffers ? "Is this the patient? " : "This is "}<strong>{chart.name}</strong>, {where(chart)}.</p>
        {nameDiffers && <Differs label="The name" fax={name} record={chart.name} source="In the chart"
                                 use="Use the chart’s value" onUse={() => onUseName(chart)} />}
        {bornDiffers && <Differs label="The date of birth" fax={born} record={chart.born} source="In the chart"
                                 use="Use the chart’s value" onUse={() => onUseBorn(chart)} />}
        {!nameDiffers && !bornDiffers && (confirmed ? (
          <p className="match__done"><span className="status status--seal"><CheckCircle size={20} /><span>Confirmed as the patient</span></span>{" "}
            <button type="button" className="link-button" onClick={() => onConfirm(null)}>Not the right person?</button></p>
        ) : (
          <button type="button" className="btn btn--secondary btn--start" onClick={() => onConfirm(chart.ref)}>Yes, this is the patient</button>
        ))}
        {!confirmed && <button type="button" className="link-button link-button--start" onClick={() => onLook()}>Look up again</button>}
      </>
    );
  } else if (match.candidates.length) {
    body = (
      <>
        <p>No one in the chart has exactly this name and date of birth. {match.candidates.length === 1
          ? "This is the closest. Is it the patient?" : "These are the closest. Is it one of them?"}</p>
        <ul className="match__list">
          {match.candidates.map((c) => (
            <li key={c.ref} className="match__item">
              <span><strong>{c.name}</strong>, {where(c)}</span>
              <button type="button" className="btn btn--secondary" onClick={() => onLook(c.ref)}>
                This is the patient<span className="visually-hidden">: {c.name}, {where(c)}</span></button>
            </li>
          ))}
        </ul>
        <button type="button" className="link-button link-button--start" onClick={() => onLook()}>Look up again</button>
      </>
    );
  } else {
    body = (
      <>
        <p>No one in the chart is close to this name and date of birth. Check both against the fax. If the fax does not
          give them clearly, ask the clinic.</p>
        <button type="button" className="btn btn--secondary btn--start" onClick={() => onLook()}>Look up again</button>
      </>
    );
  }
  return (
    <section className="match" id="patient-check" tabIndex={-1} aria-labelledby="match-title">
      <h2 id="match-title" className="match__title">The patient in the chart</h2>
      {body}
      {failure && <p role="alert" className="alert">{failure}</p>}
    </section>
  );
}
