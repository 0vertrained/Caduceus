import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { glue, useResource, useTitle } from "../hooks";

interface Model { name: string; model: string }
interface AdminView {
  routes: { in_vpc: Model | null; external: Model | null; external_on: boolean; drafts_in_vpc: boolean };
  database: string; records: string; sign_in: string;
  payer: { review_seconds: number };
  people: { id: string; name: string; role: string }[];
  audit: { intact: boolean; events: number; last: string | null };
  policies: { title: string; version: string; intact: boolean }[];
}

const ROLES: Record<string, string> = {
  pa_specialist: "Prior authorization specialist", clinician: "Clinician", admin: "Administrator", auditor: "Auditor",
};

/** The external model's off switch: one action, said plainly, with what happens next. */
function ExternalSwitch({ view, onChange }: { view: AdminView; onChange: (v: AdminView) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const on = view.routes.external_on;
  // Where drafts go without it: the model inside the network, unless it is kept for work on patients' details alone.
  const instead = view.routes.drafts_in_vpc ? "inside the network" : "from the template";
  const flip = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api<AdminView>("/api/admin/external-route", { method: "POST", body: JSON.stringify({ on: !on }) }));
      setSaid(on ? "The external model is off." : "The external model is on again.");
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`admin__switch${on ? "" : " admin__switch--off"}`}>
      <p><strong>{on ? "The external model is on." : "The external model is off."}</strong> {on
        ? `Turning it off stops every call to it at once. Drafts are then written ${instead}.`
        : `Nothing is sent to it. Drafts are written ${instead} until it is turned back on.`}
      </p>
      <button type="button" className={`btn ${on ? "btn--secondary" : "btn--primary"} btn--start`} disabled={busy}
              data-busy={busy || undefined} onClick={() => void flip()}>
        {on ? "Turn off the external model" : "Turn the external model back on"}</button>
      <span className="visually-hidden" role="status">{said}</span>
      {error && <p role="alert" className="alert">{error}</p>}
    </div>
  );
}

/** How this deployment is set up, for its administrator, and the one switch they may need in a hurry. */
export function AdminPage() {
  const { data, setData, error } = useResource<AdminView>("/api/admin");
  useTitle("Administration");
  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Loading.</p></div>;
  const { in_vpc, external } = data.routes;
  const review = data.payer.review_seconds;
  return (
    <div className="page admin">
      <div className="outcomes__intro">
        <h1 className="page-title">Administration</h1>
        <p className="lead">How this deployment of Caduceus is set up, and whether what must not change has not.</p>
      </div>

      <section className="panel admin__section" aria-labelledby="routes-title">
        <h2 id="routes-title" className="section-title">Where patient information can go</h2>
        <dl className="sent-facts">
          <div><dt>Inside Mesa Verde’s network</dt>
            <dd>{in_vpc ? <><span translate="no">{in_vpc.model}</span>. Work that needs the patient’s details runs
              here and nowhere else.</> : "No model runs inside the network, so work that needs the patient’s " +
              "details is done without one."}</dd></div>
          <div><dt>Outside the network, de-identified text only</dt>
            <dd>{external ? <><span translate="no">{external.model}</span>. It sees text with the patient’s
              identifiers taken out, checked again before each request leaves.</> : "None is set up."}</dd></div>
        </dl>
        {external && <ExternalSwitch view={data} onChange={setData} />}
      </section>

      <section className="panel admin__section" aria-labelledby="policies-title">
        <h2 id="policies-title" className="section-title">Coverage policies</h2>
        <ul className="admin__list">
          {data.policies.map((p) => (
            <li key={p.title}><strong>{p.title}</strong>: version {p.version}, {p.intact
              ? "unchanged since it was approved." : <span className="alert">changed since it was approved, so it does not run.</span>}</li>
          ))}
        </ul>
        <p><Link to="/policies">Read the policies and their sources</Link></p>
      </section>

      <section className="panel admin__section" aria-labelledby="audit-title">
        <h2 id="audit-title" className="section-title">The audit log</h2>
        <p>{data.audit.intact
          ? <>Intact. All {data.audit.events.toLocaleString()} events check out, each sealed to the one before it.</>
          : <span className="alert">Changed. An event no longer matches the seal of the one after it.</span>}
          {data.audit.last ? ` The last was ${glue(data.audit.last)}.` : ""}</p>
      </section>

      <section className="panel admin__section" aria-labelledby="people-title">
        <h2 id="people-title" className="section-title">People and roles</h2>
        {data.people.length ? (
          <table className="admin__table">
            <caption className="visually-hidden">People who can sign in, and their roles</caption>
            <thead><tr><th scope="col">Name</th><th scope="col">Role</th></tr></thead>
            <tbody>
              {data.people.map((p) => <tr key={p.id}><td>{glue(p.name)}</td><td>{ROLES[p.role] ?? p.role}</td></tr>)}
            </tbody>
          </table>
        ) : <p>People sign in through your identity provider, and each one’s role comes with their sign-in, in
          its <span translate="no">caduceus_role</span> claim.</p>}
      </section>

      <section className="panel admin__section" aria-labelledby="systems-title">
        <h2 id="systems-title" className="section-title">Systems</h2>
        <dl className="sent-facts">
          <div><dt>Cases and the audit log</dt><dd>{data.database}</dd></div>
          <div><dt>Patient records</dt><dd>{data.records}</dd></div>
          <div><dt>Sign-in</dt><dd>{data.sign_in}</dd></div>
          <div><dt>Payer</dt><dd>Simulated. {review > 0
            ? `It decides ${review} seconds after a request arrives, or half that when expedited.`
            : "It decides as soon as a request arrives."}</dd></div>
        </dl>
      </section>
    </div>
  );
}
