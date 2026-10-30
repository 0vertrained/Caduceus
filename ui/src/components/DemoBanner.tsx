import { useEffect, useState } from "react";
import { api, type DemoInfo, type Me } from "../api";

function every(hours: number): string {
  return hours === 1 ? "every hour" : `every ${hours} hours`;
}

/** Shown only on the public demo, so no one mistakes synthetic patients for real ones. */
export function DemoBanner({ me }: { me: Me | null }) {
  const [info, setInfo] = useState<DemoInfo | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<DemoInfo>("/api/demo").then(setInfo).catch(() => setInfo(null));
  }, []);

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

  return (
    <div className="demo-banner" role="region" aria-label="About this demo">
      <p>
        <strong>Public demo.</strong> Every patient here is synthetic. Sign in as anyone; the cases go back to the
        start {every(info.reset_every_hours)}.
      </p>
      {me && (
        <button type="button" className="demo-banner__action" onClick={startOver}>Start over now</button>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
