import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, CHANGED, sandboxLink, type DemoInfo, type Me, type Tour } from "../api";
import { glue } from "../hooks";

const HIDDEN_KEY = "caduceus.tourHidden";

function every(hours: number): string {
  return hours === 1 ? "an hour" : `${hours} hours`;
}

function storedHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Shown only on the public demo, so no one mistakes synthetic patients for real ones, with a guided tour that
 *  follows one case and says who to act as at each step. */
export function DemoBanner({ me, onSwitch }: { me: Me | null; onSwitch: (user: string, path: string | null) => void }) {
  const [info, setInfo] = useState<DemoInfo | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [hidden, setHidden] = useState(storedHidden);
  const location = useLocation();

  const load = useCallback(() => {
    api<DemoInfo>("/api/demo").then(setInfo).catch(() => setInfo(null));
  }, []);
  useEffect(load, [load, location.pathname, me?.id]);
  useEffect(() => {
    window.addEventListener(CHANGED, load);
    return () => window.removeEventListener(CHANGED, load);
  }, [load]);
  const waiting = info?.demo && info.tour?.waiting;
  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [waiting, load]);

  // The browser's own bar matches the dark banner at the top of the page while the demo is on.
  const demo = Boolean(info?.demo);
  useEffect(() => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!demo || !meta) return;
    const before = meta.getAttribute("content") ?? "";
    meta.setAttribute("content", getComputedStyle(document.documentElement).getPropertyValue("--ink").trim());
    return () => meta.setAttribute("content", before);
  }, [demo]);

  if (!info?.demo) return null;

  const startOver = async () => {
    setMessage(null);
    try {
      await api("/api/demo/reset", { method: "POST" });
      window.location.assign("/");
    } catch (e) {
      setMessage((e as Error).message);
    }
  };
  const setTourHidden = (value: boolean) => {
    setHidden(value);
    try {
      if (value) localStorage.setItem(HIDDEN_KEY, "1");
      else localStorage.removeItem(HIDDEN_KEY);
    } catch {
      /* the choice lasts until the page is reloaded */
    }
  };

  return (
    <>
      <div className="demo-banner" role="region" aria-label="About this demo">
        <div className="demo-banner__inner">
          <p>
            <strong>Public demo.</strong> Every patient here is synthetic. These cases are your own copy: sign in as
            anyone, and they start over after {every(info.reset_every_hours)} or when you choose.
          </p>
          <div className="demo-banner__actions">
            {hidden && info.tour && (
              <button type="button" className="demo-banner__action" onClick={() => setTourHidden(false)}>Show the tour</button>
            )}
            {me && <button type="button" className="demo-banner__action" onClick={startOver}>Start over now</button>}
          </div>
          {message && <p role="status">{message}</p>}
        </div>
      </div>
      {!hidden && info.tour && (
        <TourStrip tour={info.tour} me={me} here={location.pathname} onSwitch={onSwitch} onStartOver={startOver}
                   onHide={() => setTourHidden(true)} />
      )}
    </>
  );
}

function TourStrip({ tour, me, here, onSwitch, onStartOver, onHide }: {
  tour: Tour; me: Me | null; here: string; onSwitch: (user: string, path: string | null) => void;
  onStartOver: () => void; onHide: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const clinicianStep = tour.actor?.startsWith("dr.");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sandboxLink());
      setCopied(true);
    } catch {
      window.prompt("Open this link on your phone:", sandboxLink());
    }
  };

  let action = null;
  if (tour.actor && me?.id !== tour.actor) {
    action = <button type="button" className="btn btn--primary" onClick={() => onSwitch(tour.actor!, tour.path)}>
      {me ? `Switch to ${tour.actor_name}` : `Sign in as ${tour.actor_name}`}</button>;
  } else if (tour.path && here !== tour.path && !tour.ended) {
    action = <Link to={tour.path} className="btn btn--primary">{tour.path.startsWith("/attest") ? "Open the request" : "Open the case"}</Link>;
  } else if (tour.ended || tour.done) {
    action = <button type="button" className="btn btn--secondary" onClick={onStartOver}>Start the tour over</button>;
  }

  return (
    <section className="tour" aria-label="Guided tour">
      <div className="tour__inner">
        <div className="tour__body">
          <p className="tour__step">
            {tour.done ? "Tour complete" : `Tour, step ${tour.step} of ${tour.steps}`}
            <span className="tour__meter" aria-hidden="true"><span style={{ width: `${(tour.step / tour.steps) * 100}%` }} /></span>
          </p>
          <p className="tour__text" aria-live="polite">{glue(tour.text)}</p>
        </div>
        <div className="tour__actions">
          {action}
          {clinicianStep && (
            <button type="button" className="link-button" onClick={copy}>
              {copied ? "Link copied. Open it on your phone." : "Copy a link to do this on your phone"}
            </button>
          )}
          <button type="button" className="btn--text" onClick={onHide}>Hide the tour</button>
        </div>
      </div>
    </section>
  );
}
