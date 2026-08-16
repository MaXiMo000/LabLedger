import { Component } from "react";

/**
 * Keeps a WebGL failure local.
 *
 * The scene is an illustration of the cascade, not the explanation of it — the
 * stage list beside it carries the whole argument on its own. So anything that
 * goes wrong inside the canvas (no WebGL, a driver fault, a renderer version
 * mismatch) must degrade to the still diagram rather than unmount the page.
 */
export default class SceneBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    // Surfaced for the developer; the user gets the fallback, not a stack.
    console.warn("[cascade] scene unavailable, using still diagram:", error?.message);
  }

  render() {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
