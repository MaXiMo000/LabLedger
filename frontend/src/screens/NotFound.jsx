import { Link } from "react-router-dom";

/**
 * A wrong path inside the app.
 *
 * `/app/typo` matches the `/app` route, and with no child to render the shell
 * drew its navigation and patient switcher around an empty column — signed in,
 * apparently working, and nothing on the page. This says what happened instead.
 *
 * It stays inside the guard on purpose. Redirecting to the marketing page would
 * be indistinguishable from being signed out, which is the one thing a
 * mistyped URL must not look like on a screen that holds clinical data.
 */
export default function NotFound() {
  return (
    <div className="empty">
      <h2 className="empty__title">That page is not here</h2>
      <p className="empty__body">
        The address is wrong, or whatever was here has moved. Your session is
        fine — nothing has signed you out.
      </p>
      <Link className="btn btn--primary" to="/app">See your results</Link>
    </div>
  );
}
