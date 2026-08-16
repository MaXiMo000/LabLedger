import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Hover state that survives the gap between two targets.
 *
 * Pointer events fire `out` on the element you are leaving before `over` on the
 * element you are entering. With a panel bound to that state, the null in
 * between renders for a frame or two and the panel visibly flickers as you
 * move along a list — which is exactly what it looks like when the cursor
 * crosses the plates in the cascade scene.
 *
 * Clearing is therefore deferred by one short beat and cancelled if anything
 * else claims the hover first. Setting stays immediate: only the *release* is
 * delayed, so the interface still feels instant.
 */
export default function useHoverTarget(delay = 90) {
  const [active, setActive] = useState(null);
  const timer = useRef(null);

  const clearTimer = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  const set = useCallback((id) => {
    clearTimer();
    if (id === null) {
      timer.current = setTimeout(() => setActive(null), delay);
    } else {
      setActive(id);
    }
  }, [delay]);

  useEffect(() => clearTimer, []);

  return [active, set];
}
