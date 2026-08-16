import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Suspense, lazy } from "react";
import { Navigate, Route, BrowserRouter as Router, Routes, useLocation } from "react-router-dom";
import AppShell from "./app/AppShell.jsx";
import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { PatientProvider } from "./patients/PatientContext.jsx";
import Hero from "./sections/Hero.jsx";

// The landing page and the signed-in app are two different products sharing a
// bundle, and nobody needs both at once. Splitting per route keeps the landing
// page's first paint free of chart, table and query code, and keeps the app
// free of the cascade section's three.js.
const Cascade = lazy(() => import("./sections/cascade/Cascade.jsx"));
const SignIn = lazy(() => import("./screens/SignIn.jsx"));
const Trends = lazy(() => import("./screens/Trends.jsx"));
const Reports = lazy(() => import("./screens/Reports.jsx"));
const Upload = lazy(() => import("./screens/Upload.jsx"));
const Review = lazy(() => import("./screens/Review.jsx"));
const Record = lazy(() => import("./screens/Record.jsx"));
const Security = lazy(() => import("./screens/Security.jsx"));
const Invite = lazy(() => import("./screens/Invite.jsx"));
const Aliases = lazy(() => import("./screens/Aliases.jsx"));
const ResetPassword = lazy(() => import("./screens/ResetPassword.jsx"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // A 401 is handled by the client's refresh interceptor; retrying here
      // would just re-run a request that is already being replayed.
      retry: (count, err) => ![401, 403, 404].includes(err?.response?.status) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

function Marketing() {
  return (
    <>
      <a className="sr-only" href="#main">Skip to content</a>
      <main id="main">
        <Hero />
        {/* No fallback: an empty slot is quieter than a spinner for a section
            that is below the fold anyway. */}
        <Suspense fallback={null}>
          <Cascade />
        </Suspense>
      </main>
    </>
  );
}

/**
 * Auth has three states, not two. Rendering the sign-in screen while the
 * session is still being restored would bounce a signed-in user out on every
 * reload, because the access token is deliberately never persisted.
 */
function RequireAuth({ children }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <div className="boot" role="status" aria-live="polite">Restoring your session…</div>;
  }
  if (status === "out") {
    return <Navigate to="/signin" replace state={{ from: location.pathname }} />;
  }
  return children;
}

/** The OAuth callback lands here with only a cookie; the provider does the rest. */
function AuthCallback() {
  const { status } = useAuth();
  if (status === "loading") {
    return <div className="boot" role="status">Signing you in…</div>;
  }
  return <Navigate to={status === "in" ? "/app" : "/signin"} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <AuthProvider>
          <Suspense fallback={<div className="route-pending" role="status" aria-live="polite" />}>
          <Routes>
            <Route path="/" element={<Marketing />} />
            <Route path="/signin" element={<SignIn />} />
            <Route path="/auth/callback" element={<AuthCallback />} />
            {/* Public: the whole point is that the invitee has no access yet. */}
            <Route path="/invite/:token" element={<Invite />} />
            <Route path="/reset/:token" element={<ResetPassword />} />

            <Route
              path="/app"
              element={
                <RequireAuth>
                  {/* Inside the guard: the patient list is clinical data and
                      needs a session before it can be fetched. */}
                  <PatientProvider>
                    <AppShell />
                  </PatientProvider>
                </RequireAuth>
              }
            >
              <Route index element={<Trends />} />
              <Route path="reports" element={<Reports />} />
              <Route path="upload" element={<Upload />} />
              <Route path="review" element={<Review />} />
              <Route path="review/learned" element={<Aliases />} />
                <Route path="record" element={<Record />} />
              <Route path="security" element={<Security />} />
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </AuthProvider>
      </Router>
    </QueryClientProvider>
  );
}
