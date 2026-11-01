import { useEffect, useState, type FormEvent } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../api";

const SIZE_KEY = "caduceus.textSize";

function readSize(): "standard" | "larger" {
  try {
    return localStorage.getItem(SIZE_KEY) === "larger" ? "larger" : "standard";
  } catch {
    return "standard";
  }
}

export function useTextSize() {
  const [size, setSize] = useState(readSize);
  useEffect(() => {
    document.documentElement.dataset.textSize = size;
    try {
      localStorage.setItem(SIZE_KEY, size);
    } catch {
      /* the choice still applies for this visit */
    }
  }, [size]);
  return [size, setSize] as const;
}

export function searchTerm(state: unknown): string {
  return typeof state === "object" && state !== null && typeof (state as { q?: unknown }).q === "string"
    ? (state as { q: string }).q : "";
}

export function Header({ me, onSignOut }: { me: Me | null; onSignOut: () => void }) {
  const [size, setSize] = useTextSize();
  const location = useLocation();
  const [query, setQuery] = useState(searchTerm(location.state));
  const navigate = useNavigate();
  // The box follows navigation: it shows the current search, and empties on leaving the results.
  useEffect(() => { setQuery(searchTerm(location.state)); }, [location.key, location.state]);
  const canSearch = me?.role === "pa_specialist" || me?.role === "admin";

  function search(e: FormEvent) {
    e.preventDefault();
    // The term travels in history state, never the URL: a patient's name must not reach server or proxy logs.
    navigate("/", { state: query.trim() ? { q: query.trim() } : null });
  }

  return (
    <header className="header">
      <div className="header__inner">
        <Link to="/" className="header__brand">
          <span className="header__wordmark">Caduceus</span>{" "}
          <span className="header__org">Mesa Verde Health</span>
          <span className="visually-hidden">, home</span>
        </Link>
        {me && (
          <div className="header__user">
            <span className="header__name">{me.name}</span>
            <button type="button" className="btn--text" onClick={onSignOut}>Sign out</button>
          </div>
        )}
        <div className="header__tools">
          {canSearch && (
            <form className="header__search" role="search" onSubmit={search}>
              <label className="field-label" htmlFor="find-patient">Find a patient</label>
              <input id="find-patient" className="input" type="search" placeholder="Name or case number"
                     value={query} onChange={(e) => setQuery(e.target.value)} />
            </form>
          )}
          <nav className="header__links" aria-label="Pages">
            {me && me.role !== "clinician" && <NavLink to="/outcomes" className="header__link">Outcomes</NavLink>}
            {me && me.role !== "clinician" && <NavLink to="/policies" className="header__link">Policies</NavLink>}
            {me?.role === "admin" && <NavLink to="/admin" className="header__link">Administration</NavLink>}
            <NavLink to="/help" className="header__link">Help</NavLink>
          </nav>
          <div className="segmented" role="group" aria-labelledby="text-size-label">
            <span id="text-size-label" className="field-label">Text size</span>
            <div className="segmented__options">
              <button type="button" className="segmented__option" aria-pressed={size === "standard"}
                      onClick={() => setSize("standard")}>Standard</button>
              <button type="button" className="segmented__option" aria-pressed={size === "larger"}
                      onClick={() => setSize("larger")}>Larger</button>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
