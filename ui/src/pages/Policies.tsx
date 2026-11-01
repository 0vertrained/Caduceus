import { useResource, useTitle } from "../hooks";

interface PolicySet {
  key: string; version: string; title: string; status: "draft" | "approved";
  approved_by: string | null; approved_at: string | null;
  source: { kind: "mcd-mirror" | "development-paraphrase"; title: string; document: string | null; url: string | null;
            note: string | null; retrieved: string | null };
  sha256: string; intact: boolean; supersedes: string | null;
  criteria?: { id: string; text: string; kind: string; policy_sentence: string;
               codes: { system: string; code: string; display: string | null }[] }[];
}
interface Policies { services: { code: string; running: PolicySet; earlier: PolicySet[] }[] }

/** Where a set's words come from: the published document, linked, or a note that they are development criteria. */
function Source({ s }: { s: PolicySet }) {
  if (!s.source.document) return <>Development criteria, paraphrased by the team. No published policy covers this
    service here.</>;
  return (
    <>
      {s.source.url ? (
        <a href={s.source.url} target="_blank" rel="noopener noreferrer" translate="no">
          {s.source.document}<span className="visually-hidden"> (opens in a new tab)</span></a>
      ) : <span translate="no">{s.source.document}</span>}
      , {s.source.title}
    </>
  );
}

function Earlier({ s, running }: { s: PolicySet; running: string }) {
  const what = s.source.document ? s.source.document : "development criteria";
  return (
    <li>
      <strong>Version {s.version}</strong>, from {what}. {s.status === "approved"
        ? `Replaced by version ${running}. Cases decided under it still show it.` : "A draft, never approved."}
    </li>
  );
}

/** Each service's criteria as a reviewer reads them: the policy's words, their source, approval and fingerprint. */
export function PoliciesPage() {
  const { data, error } = useResource<Policies>("/api/policies");
  useTitle("Coverage policies");
  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Loading the policies.</p></div>;
  return (
    <div className="page policies">
      <div className="outcomes__intro">
        <h1 className="page-title">Coverage policies</h1>
        <p className="lead">Each request is checked against criteria compiled from its policy’s own words. A version
          runs only once a reviewer has approved it, and a version changed after approval does not run at all.</p>
      </div>
      {data.services.map(({ code, running: r, earlier }) => (
        <section key={code} className="panel policy" aria-labelledby={`policy-${code}`}>
          <div className="policy__head">
            <h2 id={`policy-${code}`} className="section-title">{r.title}</h2>
            {r.intact ? <span className="tag tag--seal">Version {r.version} runs now</span>
              : <span className="tag tag--urgent">Changed since approval: stopped</span>}
          </div>
          <dl className="facts">
            <div className="facts__item facts__item--wide"><dt className="facts__term">Compiled from</dt>
              <dd className="facts__value policy__plain"><Source s={r} /></dd></div>
            {r.source.retrieved && (
              <div className="facts__item"><dt className="facts__term">Text retrieved</dt>
                <dd className="facts__value policy__plain">{r.source.retrieved}</dd></div>
            )}
            <div className="facts__item"><dt className="facts__term">Approved</dt>
              <dd className="facts__value policy__plain">{r.approved_by}, {r.approved_at}</dd></div>
            <div className="facts__item facts__item--wide"><dt className="facts__term">Fingerprint</dt>
              <dd className="facts__value policy__plain"><code translate="no">{r.sha256.slice(0, 16)}</code>{" "}
                {r.intact ? "is the one the reviewer approved." : "is not the one the reviewer approved."}</dd></div>
          </dl>
          {r.source.note && <p className="support">{r.source.note}</p>}
          <ul className="policy__criteria" aria-label={`Criteria for ${r.title}`}>
            {r.criteria?.map((c) => (
              <li key={c.id} className="policy__criterion">
                <p className="criterion__title">{c.text}</p>
                <blockquote className="policy__quote">{c.policy_sentence}</blockquote>
                {c.codes.length > 0 && (
                  <details className="details policy__codes">
                    <summary className="details__summary">
                      {c.codes.length === 1 ? "The code it looks for" : `The ${c.codes.length} codes it looks for`}
                      <span className="visually-hidden">: {c.text}</span>
                    </summary>
                    <ul>
                      {c.codes.map((k) => (
                        <li key={`${k.system}:${k.code}`}><span className="policy__code" translate="no">{k.system}&nbsp;{k.code}</span>
                          {k.display ? ` ${k.display}` : ""}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
          {earlier.length > 0 && (
            <div className="policy__earlier">
              <h3 className="packet__title">Earlier versions</h3>
              <ul>{earlier.map((e) => <Earlier key={e.key} s={e} running={r.version} />)}</ul>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
