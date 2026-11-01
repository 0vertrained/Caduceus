import { useEffect, useRef, useState } from "react";
import { api, ApiError, type CaseView, type Page } from "../../api";
import { DraftEditor } from "./DraftEditor";
import { DraftPanel } from "./DraftPanel";
import { PacketPage } from "./PacketPage";
import type { Panel } from "./panel";
import { SentPanel } from "./SentPanel";

export function SidePanel({ c, panel, setPanel, onSaved }: {
  c: CaseView; panel: Panel; setPanel: (p: Panel) => void; onSaved: (c: CaseView) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [draftText, setDraftText] = useState(c.draft?.text ?? "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (panel.kind !== "page") return;
    setPage(null);
    api<Page>(`/api/cases/${c.id}/pages/${panel.docId}`).then(setPage).catch((e: ApiError) => setError(e.message));
  }, [panel, c.id]);
  // Move focus to the panel's heading when the person changes panels, but not when the case first opens: that
  // would skip the patient's name for a screen reader and scroll past the case header.
  const opened = useRef(false);
  const mark = useRef<HTMLElement>(null);
  useEffect(() => {
    if (opened.current) heading.current?.focus();
    opened.current = true;
    mark.current?.scrollIntoView({ block: "nearest" });  // a long page: the highlighted sentence, not just its top
  }, [panel, page]);

  const toDraft = () => setPanel({ kind: "draft" });

  if (panel.kind === "sent" && c.draft?.sent) return <SentPanel c={c} onBack={toDraft} heading={heading} />;

  if (panel.kind === "page") {
    return <PacketPage c={c} panel={panel} page={page} error={error} heading={heading} mark={mark} onBack={toDraft} />;
  }

  if (panel.kind === "edit") {
    const save = async () => {
      try {
        onSaved(await api<CaseView>(`/api/cases/${c.id}/draft`, { method: "PUT", body: JSON.stringify({ text: draftText }) }));
        toDraft();
      } catch (e) {
        setError((e as ApiError).message);
      }
    };
    return <DraftEditor c={c} text={draftText} onChange={setDraftText} error={error} heading={heading}
                        onSave={save} onCancel={toDraft} />;
  }

  const edit = () => { setDraftText(c.draft?.text ?? ""); setPanel({ kind: "edit" }); };
  return <DraftPanel c={c} heading={heading} open={setPanel} onEdit={edit} />;
}
