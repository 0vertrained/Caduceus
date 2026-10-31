import { useEffect, useRef, useState } from "react";
import { finishSignIn, SignInProblem, startSignIn, type SignInNotice } from "../auth";
import { CheckCircle, Clock, Triangle } from "../components/Icons";
import { useTitle } from "../hooks";

const ROSTER = [
  { id: "dolores", name: "Dolores Montoya", role: "Prior authorization specialist" },
  { id: "dr.reyes", name: "Dr. Ana Reyes", role: "Orthopedics, ordering clinician" },
  { id: "dr.lee", name: "Dr. Marcus Lee", role: "Orthopedics, ordering clinician" },
  { id: "dr.patel", name: "Dr. Priya Patel", role: "Sleep medicine, ordering clinician" },
  { id: "sam", name: "Sam Whitlock", role: "Auditor" },
  { id: "avery", name: "Avery Chen", role: "Administrator" },
];

export function SignInPage({ onSignIn }: { onSignIn: (user: string) => void }) {
  useTitle("Sign in");
  return (
    <div className="signin">
      <h1 className="page-title">Sign in</h1>
      <p className="lead">
        This is the development sign-in. In the deployed system you sign in with your Mesa Verde account, and your
        role comes from it.
      </p>
      <ul className="signin__list">
        {ROSTER.map((u) => (
          <li key={u.id}>
            <button type="button" className="btn btn--secondary btn--large signin__user" onClick={() => onSignIn(u.id)}>
              <span>{u.name}</span><span className="muted signin__role">{u.role}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SUPPORT = "the support desk at extension 4100";

/** What the sign-in page says, and how: a problem with its next step (an alert), news, or a wait. */
const NOTICES: Record<SignInNotice, { text: string; kind: "problem" | "done" | "wait" }> = {
  "signed-out": { text: "You are signed out of Caduceus and of your Mesa Verde account.", kind: "done" },
  "signed-out-here": {
    text: "You are signed out of Caduceus, but not of your Mesa Verde account. Close this browser before you leave " +
      "the computer, so no one else can sign in as you.", kind: "problem" },
  "signing-out": { text: "Signing you out…", kind: "wait" },
  ended: { text: "Your session ended, so you were signed out. Sign in again to go back to where you were.",
    kind: "problem" },
  failed: { text: `Sign-in did not finish. Try again, and if it keeps happening, call ${SUPPORT}.`, kind: "problem" },
  refused: {
    text: `Your Mesa Verde account signed in, but Caduceus could not accept it. Ask ${SUPPORT} to check that your ` +
      "account has a role in Caduceus.", kind: "problem" },
  unreachable: { text: "Mesa Verde’s sign-in service could not be reached. Check your connection, then try again.",
    kind: "problem" },
  misconfigured: {
    text: `Mesa Verde’s sign-in service is not set up the way Caduceus expects, so sign-in cannot start. Call ${SUPPORT}.`,
    kind: "problem" },
};

/** Why the page is showing. A problem is an alert and takes focus, as the error summary does. */
function Notice({ notice }: { notice: SignInNotice }) {
  const { text, kind } = NOTICES[notice];
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (kind === "problem") box.current?.focus();
  }, [notice, kind]);
  const Icon = kind === "problem" ? Triangle : kind === "done" ? CheckCircle : Clock;
  return (
    <div ref={box} className={`signin__notice signin__notice--${kind}`} role={kind === "problem" ? "alert" : "status"}
         tabIndex={kind === "problem" ? -1 : undefined}>
      <Icon />
      <p>{text}</p>
    </div>
  );
}

/** Sign-in with the health system's identity provider: one action, and why the page is showing when it says. */
export function OidcSignInPage({ notice, returnTo }: { notice: SignInNotice | null; returnTo: string }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<SignInNotice | null>(null);
  const shown = problem ?? notice;
  useTitle(shown === "signed-out" || shown === "signed-out-here" ? "Signed out" : "Sign in");

  const go = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await startSignIn(returnTo);  // the page leaves for the provider; this returns only if the person comes back
    } catch (e) {
      setProblem(e instanceof SignInProblem ? e.notice : "unreachable");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin">
      <h1 className="page-title">Sign in</h1>
      {shown && <Notice notice={shown} />}
      {shown !== "signing-out" && (
        <>
          <p className="lead">
            Use your Mesa Verde account, the one you use for the clinic’s other systems. Your role in Caduceus comes
            with it.
          </p>
          <button type="button" className="btn btn--primary btn--large signin__go" disabled={busy}
                  data-busy={busy || undefined} onClick={() => void go()}>
            Sign in with your Mesa Verde account
          </button>
          <p className="support">
            Trouble signing in? Call the support desk at extension 4100, weekdays 7:00 am to 6:00 pm.
          </p>
        </>
      )}
    </div>
  );
}

export type SignInOutcome = { returnTo: string } | { problem: SignInNotice };

/** Where the identity provider sends the person back. It finishes sign-in once, then says where to go. */
export function SignInCallbackPage({ onDone }: { onDone: (outcome: SignInOutcome) => void }) {
  useTitle("Signing in");
  useEffect(() => {
    let current = true;  // React can run this twice in development: finishSignIn works once, and the last one answers
    finishSignIn().then(
      (returnTo) => current && onDone({ returnTo }),
      (e: unknown) => current && onDone({ problem: e instanceof SignInProblem ? e.notice : "failed" }),
    );
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="signin">
      <h1 className="page-title">Signing you in…</h1>
      <p className="lead" role="status">Finishing sign-in with your Mesa Verde account.</p>
    </div>
  );
}
