import { useEffect, useEffectEvent, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, currentUser, setCurrentUser, type DemoInfo, type Me } from "./api";
import {
  authConfig, CALLBACK_PATH, clearSession, hasSession, SESSION_ENDED, SIGNED_OUT_PATH, signOutEverywhere,
  type AuthConfig, type SignInNotice,
} from "./auth";
import { DemoBanner } from "./components/DemoBanner";
import { Header } from "./components/Header";
import { AttestListPage, AttestPage } from "./pages/Attest";
import { CaseReviewPage } from "./pages/CaseReview";
import { HelpPage } from "./pages/Help";
import { QueuePage } from "./pages/Queue";
import { OidcSignInPage, SignInCallbackPage, SignInPage, type SignInOutcome } from "./pages/SignIn";
import { IntakePage } from "./pages/Intake";
import { OutcomesPage } from "./pages/Outcomes";
import { AdminPage } from "./pages/Admin";
import { PoliciesPage } from "./pages/Policies";
import { EhrRecordPage, SmartCallbackPage, SmartLaunchPage } from "./pages/Smart";

/** In the public demo, whoever opens Caduceus from the EHR acts as its prior-authorization specialist. */
const DEMO_SPECIALIST = "dolores";
const launching = () => window.location.pathname.startsWith("/smart/");

export function App() {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [starting, setStarting] = useState(false);  // the first answer is taking long enough to say why
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const [notice, setNotice] = useState<SignInNotice | null>(null);
  const [landing, setLanding] = useState("/");  // where a person who just signed in was going
  const navigate = useNavigate();
  const location = useLocation();
  const oidc = config?.mode === "oidc";

  // How this deployment signs people in, before anything else is asked of the API. The public demo's API runs
  // on a server that stops when nobody is using it, so the first visit in a while can take ten seconds or so
  // while it starts, and a gateway in front of it may answer with an error before it is ready. For that long
  // the page says what is happening and keeps asking; after it, the API is unreachable.
  const readConfig = async () => {
    const begun = Date.now();
    const slow = window.setTimeout(() => setStarting(true), 1200);
    try {
      let attempt = 0;
      for (;;) {
        try {
          setConfig(await authConfig());
          setUnreachable(false);
          return;
        } catch (e) {
          if (Date.now() - begun > STARTUP_PATIENCE_MS) throw e;
          await pause(Math.min(1000 * ++attempt, 3000));
        }
      }
    } catch {
      setUnreachable(true);
    } finally {
      window.clearTimeout(slow);
      setStarting(false);
    }
  };
  useEffect(() => { void readConfig(); }, []);

  // Development sign-in and the demo: the roster user this tab chose.
  const load = async () => {
    if (launching() && currentUser() !== DEMO_SPECIALIST) {
      try {
        if ((await api<DemoInfo>("/api/demo")).demo) setCurrentUser(DEMO_SPECIALIST);
      } catch {
        /* the server could not say: sign in as usual */
      }
    }
    if (!currentUser()) {
      setMe(null);
      setChecked(true);
      return;
    }
    try {
      setMe(await api<Me>("/api/me"));
    } catch {
      setCurrentUser(null);
      setMe(null);
    }
    setChecked(true);
  };

  // OIDC: the session this tab holds, if it has one. On the callback page, sign-in finishes first.
  const loadSession = async () => {
    if (location.pathname !== CALLBACK_PATH && (await hasSession())) {
      try {
        setMe(await api<Me>("/api/me"));
      } catch (e) {
        await clearSession();
        setMe(null);
        if (!(e instanceof ApiError && e.status === 401)) setNotice("failed");  // a refused token has said so itself
      }
    }
    setChecked(true);
  };

  const loadSignedIn = useEffectEvent((c: AuthConfig) => void (c.mode === "oidc" ? loadSession() : load()));
  useEffect(() => {
    if (config) loadSignedIn(config);
  }, [config]);

  // The API refused the session's token, or it ran out and could not be renewed: back to sign-in, saying so. The
  // address stays, so signing in again comes back to it.
  useEffect(() => {
    const ended = (e: Event) => {
      setMe(null);
      setNotice((e as CustomEvent<SignInNotice>).detail);
    };
    window.addEventListener(SESSION_ENDED, ended);
    return () => window.removeEventListener(SESSION_ENDED, ended);
  }, []);

  const signIn = (user: string) => {
    setCurrentUser(user);
    // Signing in part-way through a launch from the EHR carries on with it; anywhere else starts at the queue.
    void load().then(() => navigate(launching() ? window.location.pathname + window.location.search : "/"));
  };
  // A new identity starts from a clean page, so nothing loaded as the previous person stays on screen.
  const switchTo = (user: string, path: string | null) => {
    setCurrentUser(user);
    window.location.assign(path ?? "/");
  };
  const signOut = () => {
    setCurrentUser(null);
    setMe(null);
    navigate("/");
  };

  // Back from the identity provider. Leaving the callback replaces its history entry, which takes the code out of the
  // address. Signed in, the callback route itself goes on to where the person was going: the router applies a
  // navigation as a transition, after the sign-in has rendered, so a navigate() here would lose to the catch-all.
  const signedIn = async (outcome: SignInOutcome) => {
    if ("problem" in outcome) {
      setNotice(outcome.problem);
      navigate("/", { replace: true });
      return;
    }
    try {
      const who = await api<Me>("/api/me");
      setLanding(outcome.returnTo);
      setNotice(null);
      setMe(who);
    } catch (e) {
      await clearSession();
      setMe(null);
      // Signed in at the provider but refused here: most often an account with no Caduceus role.
      setNotice(e instanceof ApiError && e.status === 401 ? "refused" : "failed");
      navigate("/", { replace: true });
    }
  };
  const signOutOidc = async () => {
    setMe(null);
    setNotice("signing-out");
    try {
      await signOutEverywhere();  // leaves for the provider, which comes back to /signed-out
      setNotice("signed-out-here");  // back without confirming there
    } catch {
      setNotice("signed-out-here");  // the provider could not be reached
    }
  };

  return (
    <div className="app">
      <a href="#main" className="skip-link">Skip to main content</a>
      {config && !oidc && <DemoBanner me={me} onSwitch={switchTo} />}
      <Header me={me} onSignOut={oidc ? () => void signOutOidc() : signOut} />
      <main id="main" className="app__main" tabIndex={-1}>
        {unreachable ? (
          <Unreachable onRetry={readConfig} />
        ) : starting && !config ? (
          <Starting />
        ) : !checked ? null : !me && oidc ? (
          <Routes>
            <Route path="/help" element={<HelpPage />} />
            <Route path={CALLBACK_PATH} element={<SignInCallbackPage onDone={(o) => void signedIn(o)} />} />
            <Route path={SIGNED_OUT_PATH} element={<OidcSignInPage notice={notice ?? "signed-out"} returnTo="/" />} />
            <Route path="*" element={<OidcSignInPage notice={notice} returnTo={location.pathname + location.search} />} />
          </Routes>
        ) : !me ? (
          <Routes>
            <Route path="/help" element={<HelpPage />} />
            <Route path="*" element={<SignInPage onSignIn={signIn} />} />
          </Routes>
        ) : (
          <Routes>
            <Route path="/" element={me.role === "clinician" ? <AttestListPage /> : <QueuePage me={me} />} />
            <Route path="/cases/:id" element={<CaseReviewPage />} />
            <Route path="/attest/:id" element={<AttestPage />} />
            <Route path="/intake/:id" element={<IntakePage />} />
            <Route path="/outcomes" element={<OutcomesPage />} />
            <Route path="/policies" element={me.role === "clinician" ? <Navigate to="/" replace /> : <PoliciesPage />} />
            <Route path="/admin" element={me.role === "admin" ? <AdminPage /> : <Navigate to="/" replace />} />
            <Route path="/help" element={<HelpPage />} />
            <Route path="/smart/launch" element={<SmartLaunchPage />} />
            <Route path="/smart/callback" element={<SmartCallbackPage />} />
            <Route path="/smart/record/:key" element={<EhrRecordPage />} />
            {oidc && <Route path={CALLBACK_PATH} element={<Navigate to={landing} replace />} />}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

/** How long the page keeps asking a silent API before it calls it unreachable. */
const STARTUP_PATIENCE_MS = 75_000;
const pause = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/** The API is taking a while to answer its first request: most likely starting up, so say so and wait with it. */
function Starting() {
  return (
    <div className="signin starting" role="status" aria-live="polite">
      <h1 className="page-title">Starting Caduceus</h1>
      <p className="lead">The server behind this demo stops when nobody is using it and starts again on the first
        visit, which takes ten seconds or so. The page will carry on by itself.</p>
      <div className="starting__bar" aria-hidden="true"><span /></div>
    </div>
  );
}

/** The API could not be reached to ask how to sign in: say so, and offer to try again. */
function Unreachable({ onRetry }: { onRetry: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const retry = async () => {
    setBusy(true);
    await onRetry();
    setBusy(false);
  };
  return (
    <div className="signin">
      <h1 className="page-title">Caduceus could not be reached</h1>
      <p className="lead" role="alert">Check your connection, then try again. If it keeps happening, call the support
        desk at extension 4100.</p>
      <button type="button" className="btn btn--primary btn--large signin__go" disabled={busy}
              data-busy={busy || undefined} onClick={() => void retry()}>Try again</button>
    </div>
  );
}
