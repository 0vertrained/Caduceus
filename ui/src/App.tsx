import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, currentUser, setCurrentUser, type Me } from "./api";
import { DemoBanner } from "./components/DemoBanner";
import { Header } from "./components/Header";
import { AttestListPage, AttestPage } from "./pages/Attest";
import { CaseReviewPage } from "./pages/CaseReview";
import { HelpPage } from "./pages/Help";
import { QueuePage } from "./pages/Queue";
import { SignInPage } from "./pages/SignIn";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const navigate = useNavigate();

  const load = async () => {
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

  useEffect(() => { void load(); }, []);

  const signIn = (user: string) => {
    setCurrentUser(user);
    void load().then(() => navigate("/"));
  };
  const signOut = () => {
    setCurrentUser(null);
    setMe(null);
    navigate("/");
  };

  return (
    <div className="app">
      <a href="#main" className="skip-link">Skip to main content</a>
      <DemoBanner me={me} />
      <Header me={me} onSignOut={signOut} />
      <main id="main" className="app__main" tabIndex={-1}>
        {!checked ? null : !me ? (
          <Routes>
            <Route path="/help" element={<HelpPage />} />
            <Route path="*" element={<SignInPage onSignIn={signIn} />} />
          </Routes>
        ) : (
          <Routes>
            <Route path="/" element={me.role === "clinician" ? <AttestListPage /> : <QueuePage me={me} />} />
            <Route path="/cases/:id" element={<CaseReviewPage />} />
            <Route path="/attest/:id" element={<AttestPage />} />
            <Route path="/help" element={<HelpPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
