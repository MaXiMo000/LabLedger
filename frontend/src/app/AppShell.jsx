import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import PatientSwitcher from "../patients/PatientSwitcher";
import "./AppShell.css";

const NAV = [
  { to: "/app", label: "Results", end: true },
  { to: "/app/reports", label: "Reports" },
  { to: "/app/review", label: "Review", end: true },
  { to: "/app/review/learned", label: "Learned" },
  { to: "/app/record", label: "Record" },
  { to: "/app/security", label: "Security" },
];

export default function AppShell() {
  const { user, signOut } = useAuth();

  return (
    <div className="shell">
      <header className="shell__bar">
        <div className="page shell__bar-inner">
          <NavLink to="/app" className="shell__mark">
            LabLedger
          </NavLink>

          <PatientSwitcher />

          <nav className="shell__nav" aria-label="Sections">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => `shell__link ${isActive ? "shell__link--on" : ""}`}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>

          <div className="shell__account">
            <span className="shell__who">{user?.name ?? user?.email}</span>
            <button className="shell__signout" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="page shell__main">
        <Outlet />
      </main>
    </div>
  );
}
