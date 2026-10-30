import { useEffect, useRef, useState } from "react";

// The one moment of motion in the product: the seal settles when the approved count goes up.
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
