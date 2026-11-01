/** Where the fax and the record disagree on something the record holds: both, and a way to take the record's. */
export function Differs({ label, fax, record, source, use, onUse }: {
  label: string; fax: string; record: string; source: string; use?: string; onUse?: () => void;
}) {
  return (
    <div className="differs">
      <p><strong>{label} differs.</strong> On the fax: {fax || "nothing"}. {source}: {record}.</p>
      {onUse && <button type="button" className="btn btn--secondary btn--start" onClick={onUse}>
        {use}<span className="visually-hidden"> for the {label.toLowerCase()}</span></button>}
    </div>
  );
}
