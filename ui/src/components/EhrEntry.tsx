import type { SmartInfo } from "../api";
import { launcherUrl } from "../smart";
import { Steps } from "./Steps";

const LAUNCH_STEPS = [
  "Open the SMART launcher below. It is already set up for Caduceus.",
  "Choose a patient, for example one with low back pain, knee osteoarthritis or sleep apnea, then choose Launch. If it asks, choose a practitioner too.",
  "Caduceus reads the chart and shows what it holds. Choose what is being requested, then start the case.",
];

/** The SMART launcher, set up for Caduceus: one line on the queue (`compact`), the steps through it on Help. */
export function EhrEntry({ info, compact = false }: { info: SmartInfo; compact?: boolean }) {
  if (!info.enabled || !info.client_id) {
    return compact ? null : <p>Opening Caduceus from an EHR is not set up here.</p>;
  }
  const href = launcherUrl(window.location.origin, info.client_id);
  if (compact) {
    return (
      <p className="ehr-entry--compact">
        A request can also start inside the EHR. <a href={href}>Open a patient from the SMART launcher</a>, a public
        test EHR: Caduceus reads the patient’s chart, and you choose what is being requested.
      </p>
    );
  }
  return (
    <div className="ehr-entry">
      <p>The public SMART Health IT launcher plays the EHR here: it opens Caduceus for a patient you choose, Caduceus
        reads that patient’s chart over FHIR, and you start a prior-authorization request from it in your own copy
        of the demo.</p>
      <Steps className="helper__steps help__steps" steps={LAUNCH_STEPS} />
      <p><a className="btn btn--secondary" href={href}>Open the SMART launcher</a></p>
      <p className="support">The launcher is a public test site with synthetic patients. Leave its “Simulate EHR”
        option off: Caduceus does not open inside another site’s frame.</p>
    </div>
  );
}
