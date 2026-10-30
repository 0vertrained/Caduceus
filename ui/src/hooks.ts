import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function useResource<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "The server could not be reached."));
    }
  }, [path]);

  useEffect(() => {
    void reload();
    if (!refreshMs) return;
    const t = window.setInterval(() => void reload(), refreshMs);
    return () => window.clearInterval(t);
  }, [reload, refreshMs]);

  return { data, setData, error, reload };
}

/** Seconds left until an ISO time; ticks once a second and stops at zero. */
export function useSecondsLeft(until: string | null | undefined) {
  const compute = () => (until ? Math.max(0, Math.ceil((Date.parse(until) - Date.now()) / 1000)) : 0);
  const [left, setLeft] = useState(compute);
  useEffect(() => {
    setLeft(compute());
    if (!until) return;
    const t = window.setInterval(() => setLeft(compute()), 1000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [until]);
  return left;
}

/** A count for running text: "No" and "One" read as words, every other number as numerals, which scan faster
 *  (GOV.UK style: "Write all other numbers in numerals (including 2 to 9)"). */
export function countWords(n: number): string {
  return n === 0 ? "No" : n === 1 ? "One" : String(n);
}

/** Keep pieces that belong together on one line: "4:01 pm", "Dr. Reyes", "page 2". */
export function glue(text: string): string {
  return text.replace(/(\d) (am|pm)\b/g, "$1 $2").replace(/\bDr\. /g, "Dr. ").replace(/\bpage (\d)/g, "page $1");
}

/** The browser tab's title. Never a patient's name: titles are kept in history and synced across devices. */
export function useTitle(title: string | null) {
  useEffect(() => {
    document.title = title ? `${title} – Caduceus` : "Caduceus";
  }, [title]);
}

/** Lower the first letter for use mid-sentence, leaving acronyms and names like MRI, CPAP or X-ray alone. */
export function midSentence(text: string): string {
  if (text.length > 1 && (/[A-Z]/.test(text[1]) || text[1] === "-")) return text;
  return text.charAt(0).toLowerCase() + text.slice(1);
}
