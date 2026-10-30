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
