import { useEffect, useRef, useState } from "react";

/** The one moment of motion in the product: the seal settles when the approved count goes up. */
export function Seal({ count, full = false }: { count: number; full?: boolean }) {
  const previous = useRef(count);
  const [settling, setSettling] = useState(false);

  useEffect(() => {
    if (count > previous.current) {
      setSettling(true);
      const t = window.setTimeout(() => setSettling(false), 800);
      previous.current = count;
      return () => window.clearTimeout(t);
    }
    previous.current = count;
  }, [count]);

  return (
    <div role="img" aria-label={`${count} ${count === 1 ? "case" : "cases"} approved today`}
         className={`seal${full ? " seal--full" : ""}${settling ? " seal--settle" : ""}`}>
      <span className="seal__count" aria-hidden="true">{count}</span>
      <span className="seal__label" aria-hidden="true">approved<br />today</span>
    </div>
  );
}

/** The small seal: a person attested, or, `empty`, a signature still to come. Decorative: the words say who. */
export function SealMark({ size = 24, empty = false }: { size?: number; empty?: boolean }) {
  return (
    <svg className={`seal-mark${empty ? " seal-mark--empty" : ""}`} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" aria-hidden="true" focusable="false">
      {empty ? <circle cx="12" cy="12" r="10" strokeWidth={1.5} strokeDasharray="4 4" /> : <>
        <circle cx="12" cy="12" r="11.4" strokeWidth={1} />
        <circle cx="12" cy="12" r="8.6" strokeWidth={2} />
        <path d="M8.4 12.3l2.5 2.5 4.8-5.3" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      </>}
    </svg>
  );
}
