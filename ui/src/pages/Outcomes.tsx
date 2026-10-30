import { useResource } from "../hooks";

interface Outcomes {
  demo: boolean;
  sections: { title: string; rows: { label: string; value: string; note: string }[] }[];
}

/** What the queue's work adds up to, read live from the audit log. A ledger, not a dashboard: each figure says what
 *  it counts. */
export function OutcomesPage() {
  const { data, error } = useResource<Outcomes>("/api/outcomes", 15000);
  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Adding it up.</p></div>;
  return (
    <div className="page outcomes">
      <div className="outcomes__intro">
        <h1 className="page-title">Outcomes</h1>
        <p className="lead">Counted from the audit log, as it stands now. Nothing here is estimated.
          {data.demo ? " In the demo these describe your own copy, so they change as you work." : ""}</p>
      </div>
      {data.sections.map((s) => (
        <section key={s.title} className="panel ledger" aria-labelledby={`o-${s.title}`}>
          <h2 id={`o-${s.title}`} className="section-title">{s.title}</h2>
          <dl className="ledger__rows">
            {s.rows.map((r) => (
              <div key={r.label} className="ledger__row">
                <dt>{r.label}</dt>
                <dd className="ledger__value">{r.value}</dd>
                <dd className="ledger__note">{r.note}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
