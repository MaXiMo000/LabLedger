/**
 * The cascade without WebGL.
 *
 * Same idea as the scene — plates receding, the token resting on the one that
 * holds it — drawn with transforms instead of a renderer. It is the fallback,
 * but it is not a placeholder: it carries the argument on its own, which is
 * the test for whether the 3D was ever load-bearing.
 */
export default function StillCascade({ path, depth, resolved, activeId, onHover }) {
  return (
    <div className="still" aria-hidden="true">
      <div className="still__space">
        {path.map((s, i) => (
          <div
            key={s.id}
            className={`still__plate still__plate--${s.state} ${
              activeId === s.id ? "still__plate--on" : ""
            }`}
            style={{
              "--i": i,
              "--depth": path.length,
            }}
            onMouseEnter={() => onHover(s.id)}
            onMouseLeave={() => onHover(null)}
          >
            <span className="still__plate-n num">{s.n}</span>
          </div>
        ))}

        <div
          className={`still__token ${resolved ? "" : "still__token--unresolved"}`}
          style={{ "--i": depth }}
        />
      </div>
    </div>
  );
}
