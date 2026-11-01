import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { CaseView } from "../api";
import { CaseHeader } from "../components/case/CaseHeader";
import { CaseHistory } from "../components/case/CaseHistory";
import { Criteria } from "../components/case/Criteria";
import { DecisionBar } from "../components/case/DecisionBar";
import { Denial } from "../components/case/Denial";
import type { Panel } from "../components/case/panel";
import { PayerQuestion } from "../components/case/PayerQuestion";
import { SidePanel } from "../components/case/SidePanel";
import { ChevronLeft } from "../components/Icons";
import { useResource, useTitle } from "../hooks";

export function CaseReviewPage() {
  const { id } = useParams();
  const { data: c, setData, error, reload } = useResource<CaseView>(`/api/cases/${id}`);
  const [panel, setPanel] = useState<Panel>({ kind: "draft" });
  useTitle("Case review");

  // While it is with the outbox or the payer: the send, then the payer's decision, arrive without a reload.
  useEffect(() => {
    if (c?.state !== "submitted") return;
    const t = window.setInterval(() => void reload(), 4000);
    return () => window.clearInterval(t);
  }, [c?.state, reload]);

  if (error) return <div className="page"><p role="alert" className="alert">{error.message}</p><Link to="/">Back to your queue</Link></div>;
  if (!c) return <div className="page"><p role="status" className="muted">Opening the case.</p></div>;

  return (
    <>
      <div className="page page--case">
        <Link to="/" className="back-link"><ChevronLeft />Back to your queue</Link>
        <CaseHeader c={c} setCase={setData} />
        {c.reply && ["pended", "awaiting_note", "submitted"].includes(c.state) && <PayerQuestion c={c} reply={c.reply} />}
        {c.appeal && (c.state === "denied" || c.submission?.kind === "appeal") && c.state !== "approved" && (
          <Denial key={c.appeal.draft?.text} c={c} onSaved={setData} />
        )}
        <div className="case-body">
          <Criteria c={c} open={setPanel} showing={panel.kind === "page" ? panel : null} />
          <SidePanel key={`${c.id}-${c.draft?.text?.length}`} c={c} panel={panel} setPanel={setPanel} onSaved={setData} />
        </div>
        <CaseHistory c={c} />
      </div>
      <DecisionBar c={c} setCase={setData} reload={reload} />
    </>
  );
}
