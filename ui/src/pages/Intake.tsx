import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type FaxField, type FaxView } from "../api";
import { ChevronLeft, CheckCircle, Triangle } from "../components/Icons";
import { useResource } from "../hooks";

/** The part of the page a field was read from, cropped out of the fax image, at a readable size. */
function Crop({ image, size, box, label }: { image: string; size: [number, number]; box: FaxField["box"]; label: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    // The image needs the sign-in header, so it is fetched and shown from memory rather than linked.
    let url: string | null = null;
    api<Blob>(image, {}, "blob").then((b) => { url = URL.createObjectURL(b); setSrc(url); }).catch(() => setSrc(null));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [image]);
  if (!box || !src) return null;
  const pad = 16, [x, y, w, h] = [box[0] - pad, box[1] - pad, box[2] + pad * 2, box[3] + pad * 2];
  const scale = Math.min(1, 560 / w);
  return (
    <div className="crop" role="img" aria-label={`The fax, where it gives the ${label.toLowerCase()}`}
         style={{ width: w * scale, height: h * scale, backgroundImage: `url(${src})`,
                  backgroundSize: `${size[0] * scale}px ${size[1] * scale}px`,
                  backgroundPosition: `${-x * scale}px ${-y * scale}px` }} />
  );
}

export function IntakePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, error } = useResource<FaxView>(`/api/intake/${id}`);
  const [values, setValues] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data?.fields) setValues(Object.fromEntries(data.fields.map((f) => [f.key, f.value])));
  }, [data]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!data) return <div className="page"><p role="status" className="muted">Opening the fax.</p></div>;

  const flagged = data.fields.filter((f) => f.problem);
  const create = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const made = await api<{ case_id: string }>(`/api/intake/${data.id}/confirm`, { method: "POST", body: JSON.stringify({ values }) });
      navigate(`/cases/${made.case_id}`);
    } catch (e) {
      setFailure((e as ApiError).message);
      setBusy(false);
    }
  };

  return (
    <div className="page intake">
      <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
      <div className="intake__intro">
        <h1 className="page-title">Check the fax</h1>
        <p className="lead">{data.status === "confirmed" ? "This fax is already a case." : flagged.length
          ? `${flagged.length === 1 ? "One field needs" : `${flagged.length} fields need`} your eyes. Each is shown beside the part of the page it came from.`
          : "Every field was read clearly. Look them over, then create the case."}</p>
        <p className="support">Read by {data.engine} inside Mesa Verde’s systems; the page never left. Fields below 90% confidence,
          or that do not look right, are sent to you.</p>
      </div>
      {data.status === "confirmed" && data.case_id ? <Link to={`/cases/${data.case_id}`} className="btn btn--primary">Open the case</Link> : (
        <form className="panel intake__form" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          {[...flagged, ...data.fields.filter((f) => !f.problem)].map((f) => (
            <div key={f.key} className={`intake__field${f.problem ? " intake__field--check" : ""}`}>
              <label className="field-label" htmlFor={`fax-${f.key}`}>{f.label}</label>
              {f.problem
                ? <p className="intake__why" id={`why-${f.key}`}><Triangle size={18} />{f.problem}</p>
                : <p className="intake__why intake__why--ok" id={`why-${f.key}`}><CheckCircle size={18} />Read clearly ({Math.round(f.confidence * 100)}%)</p>}
              {f.problem && <Crop image={data.image} size={data.size} box={f.box} label={f.label} />}
              <input id={`fax-${f.key}`} className="input" value={values[f.key] ?? ""} aria-describedby={`why-${f.key}`}
                     onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            </div>
          ))}
          {failure && <p role="alert" className="alert">{failure}</p>}
          <div className="intake__actions">
            <button type="submit" className="btn btn--primary btn--large" disabled={busy}>Create the case</button>
            <span className="support">Caduceus matches the patient in the record, then checks the case against the payer’s policy.</span>
          </div>
        </form>
      )}
    </div>
  );
}
