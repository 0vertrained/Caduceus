export function Steps({ steps, className = "helper__steps" }: { steps: readonly string[]; className?: string }) {
  return (
    <ol className={className}>
      {steps.map((s, i) => (
        <li key={s} className="helper__step"><span className="step-number" aria-hidden="true">{i + 1}</span>{s}</li>
      ))}
    </ol>
  );
}
