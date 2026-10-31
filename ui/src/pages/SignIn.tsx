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
