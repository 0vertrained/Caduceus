import { useEffect, useState } from "react";
import { api } from "../api";

export type Box = [number, number, number, number];

/** A fax page needs the sign-in header, so it is fetched once and shown from memory rather than linked. */
export function useAuthImage(path: string | null | undefined): string | null {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let url: string | null = null;
    let live = true;
    api<Blob>(path, {}, "blob")
      .then((b) => { if (live) { url = URL.createObjectURL(b); setSrc(url); } })
      .catch(() => { if (live) setSrc(null); });
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [path]);
  return src;
}

/** When a line could not be found, the whole page in its place, at the field's width, to look for it. */
export function WholePage({ src, label }: { src: string | null; label: string }) {
  if (!src) return null;
  return (
    <div className="whole-page" tabIndex={0} role="region"
         aria-label={`The whole fax, because the ${label} could not be found on it. Scroll to look for it.`}>
      <img src={src} alt="The faxed cover sheet" />
    </div>
  );
}

const ZOOM = [100, 150, 200, 300];

/** The whole page on request: the buttons or + and − zoom it; once it has focus, the arrow keys move around it. */
export function FaxViewer({ src, alt }: { src: string | null; alt: string }) {
  const [open, setOpen] = useState(false);
  const [zoom, setZoom] = useState(100);
  const at = ZOOM.indexOf(zoom);
  const step = (by: number) => setZoom(ZOOM[Math.min(ZOOM.length - 1, Math.max(0, at + by))]);
  const size = zoom === 100 ? "fitted to the width" : `at ${zoom}%`;
  return (
    <div className="fax-viewer">
      <button type="button" className="btn btn--secondary btn--start" aria-expanded={open} aria-controls="whole-fax"
              onClick={() => setOpen(!open)}>{open ? "Hide the whole fax" : "View the whole fax"}</button>
      {open && (
        <section id="whole-fax" className="panel fax-viewer__panel" aria-labelledby="whole-fax-title">
          <div className="fax-viewer__bar">
            <h2 id="whole-fax-title" className="fax-viewer__title">The whole fax</h2>
            <div className="fax-viewer__zoom" role="group" aria-label="Zoom">
              <button type="button" className="btn btn--secondary" disabled={at === 0} onClick={() => step(-1)}>Zoom out</button>
              <button type="button" className="btn btn--secondary" disabled={at === 0} onClick={() => setZoom(100)}>Fit to width</button>
              <button type="button" className="btn btn--secondary" disabled={at === ZOOM.length - 1} onClick={() => step(1)}>Zoom in</button>
              <span className="fax-viewer__level" role="status">{zoom === 100 ? "Fitted" : `${zoom}%`}</span>
            </div>
          </div>
          <div className="fax-viewer__page" tabIndex={0} role="region" aria-label={`The fax page, ${size}`}
               onKeyDown={(e) => {
                 if (e.key === "+" || e.key === "=") { e.preventDefault(); step(1); }
                 if (e.key === "-" || e.key === "_") { e.preventDefault(); step(-1); }
               }}>
            {src ? <img src={src} alt={alt} style={{ width: `${zoom}%` }} /> : <p className="muted">Opening the page.</p>}
          </div>
        </section>
      )}
    </div>
  );
}

/** The part of the page a field was read from, cropped out of the fax image, at a readable size. */
export function Crop({ src, size, box, label }: { src: string | null; size: [number, number]; box: Box | null; label: string }) {
  if (!box || !src) return null;
  const pad = 16, [x, y, w, h] = [box[0] - pad, box[1] - pad, box[2] + pad * 2, box[3] + pad * 2];
  const scale = Math.min(1, 560 / w);
  return (
    <div className="crop" role="img" aria-label={`The fax, where it gives the ${label}`}
         style={{ width: w * scale, height: h * scale, backgroundImage: `url(${src})`,
                  backgroundSize: `${size[0] * scale}px ${size[1] * scale}px`,
                  backgroundPosition: `${-x * scale}px ${-y * scale}px` }} />
  );
}
