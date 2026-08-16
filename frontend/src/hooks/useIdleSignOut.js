import { useEffect, useRef } from "react";

/**
 * Sign out after a stretch of no interaction.
 *
 * The server already ends an idle session, and that is the part that matters —
 * this cannot be trusted, because it runs on the client. What it adds is the
 * half the server cannot do: clearing results off the screen. A ward monitor
 * left on a patient's ferritin trend is a disclosure whether or not the token
 * behind it still works, and the server has no way to blank it.
 *
 * Fires a minute early so the screen clears before the API starts refusing —
 * a 401 mid-click reads as a bug, a lock screen reads as a lock screen.
 */

const ACTIVITY = ["pointerdown", "keydown", "wheel", "touchstart"];
const EARLY_MS = 60_000;

/**
 * How long to wait before locking, given the server's idle timeout.
 *
 * Exported because it is the one part of this hook that can be wrong in a way
 * nobody would notice until it mattered. The floor is doing real work: without
 * it a server timeout of a minute or less computes to zero or negative, which
 * `setTimeout` treats as "immediately" — the screen would lock the instant it
 * loaded, clear, reload, and lock again. A lock screen you cannot get past
 * looks like the session system is broken rather than like a short timeout.
 */
export function idleDelayMs(minutes) {
  return Math.max(minutes * 60_000 - EARLY_MS, 60_000);
}

export function useIdleSignOut(minutes, onIdle) {
  const fire = useRef(onIdle);
  fire.current = onIdle;

  useEffect(() => {
    if (!minutes) return undefined;
    const after = idleDelayMs(minutes);
    let timer;

    const reset = () => {
      clearTimeout(timer);
      timer = setTimeout(() => fire.current(), after);
    };

    reset();
    // Passive: none of these handlers do anything the browser should wait for.
    ACTIVITY.forEach((e) => window.addEventListener(e, reset, { passive: true }));
    // A tab restored from the background may have been idle the whole time it
    // was hidden, and no event fired while it was.
    document.addEventListener("visibilitychange", reset);

    return () => {
      clearTimeout(timer);
      ACTIVITY.forEach((e) => window.removeEventListener(e, reset));
      document.removeEventListener("visibilitychange", reset);
    };
  }, [minutes]);
}
