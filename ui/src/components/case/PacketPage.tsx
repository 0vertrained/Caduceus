import { useState, type RefObject } from "react";
import type { CaseView, Page } from "../../api";
import { glue } from "../../hooks";
import { useAuthImage } from "../FaxImage";
import { ChevronLeft } from "../Icons";
import type { PagePanel } from "./panel";

/** A faxed page: the text below is what OCR read, so the page itself is a click away. */
function FaxPageImage({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const src = useAuthImage(open ? path : null);
  return (
    <div className="side__fax">
      <p className="support">The text below is what OCR read from the faxed page.{" "}
        <button type="button" className="link-button" aria-expanded={open} aria-controls="fax-page-image"
                onClick={() => setOpen(!open)}>{open ? "Hide the faxed page" : "Show the faxed page"}</button></p>
      {open && (
        <div id="fax-page-image" className="whole-page whole-page--tall" tabIndex={0} role="region" aria-label="The faxed page">
          {src ? <img src={src} alt="The faxed cover sheet" /> : <p className="muted">Opening the page.</p>}
        </div>
      )}
    </div>
  );
}

/** Where the sentence is on the page: at the offsets given when they still hold it, else its first appearance. */
function markAt(text: string, sentence: string | null, at?: { start: number; end: number }): number {
  if (!sentence) return -1;
  return at && text.slice(at.start, at.end) === sentence ? at.start : text.indexOf(sentence);
}

function Highlighted({ text, sentence, at, markRef }: {
  text: string; sentence: string | null; at: number; markRef: RefObject<HTMLElement | null>;
}) {
  if (!sentence || at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark ref={markRef}>{sentence}</mark>{text.slice(at + sentence.length)}</>;
}

export function PacketPage({ c, panel, page, error, heading, mark, onBack }: {
  c: CaseView; panel: PagePanel; page: Page | null; error: string | null;
  heading: RefObject<HTMLHeadingElement | null>; mark: RefObject<HTMLElement | null>; onBack: () => void;
}) {
  const at = page ? markAt(page.text, panel.sentence, panel.lead) : -1;
  return (
    <aside className="panel side" aria-labelledby="panel-title" aria-live="polite">
      <button type="button" className="link-button link-button--plain"
              onClick={onBack}><ChevronLeft size={20} />Back to the draft</button>
      {!page ? <p role="status" className="muted">{error ?? "Opening the page."}</p> : (
        <>
          <h2 id="panel-title" ref={heading} tabIndex={-1} className="section-title">{page.title}</h2>
          {at >= 0 && (panel.lead ? (
            <p className="support side__note">
              <mark>Highlighted</mark> is the passage that may answer: {panel.supports}. It is not evidence: only
              {" "}{glue(c.ordering_clinician.name)}’s answer can mark it met.
            </p>
          ) : (
            <p className="support side__note">
              <mark>Highlighted</mark> is the sentence that supports: {panel.supports}.
            </p>
          ))}
          {page.image && <FaxPageImage path={page.image} />}
          <div className="paper">
            <p className="paper__head">
              <span>Page {page.page} of {page.pages}</span>
              {(page.date || page.author) && <span>{[page.date, page.author].filter(Boolean).join(" · ")}</span>}
            </p>
            <Highlighted text={page.text} sentence={panel.sentence} at={at} markRef={mark} />
          </div>
        </>
      )}
    </aside>
  );
}
