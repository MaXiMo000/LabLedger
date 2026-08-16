import { Suspense, lazy, useMemo, useState } from "react";
import useHoverTarget from "../../hooks/useHoverTarget";
import SceneBoundary from "../../components/SceneBoundary";
import StillCascade from "./StillCascade";
import { REVIEW, SAMPLES, STAGES, pathFor } from "./stages";
import "./Cascade.css";

// Three.js is ~235 kB gzipped and nothing above the fold needs it, so the
// scene is a separate chunk that never blocks first paint.
const CascadeScene = lazy(() => import("./CascadeScene"));

// react-three-fiber 9.7 reaches React internals through `its-fine`; that access
// broke on the React 19.2 line despite the peer range claiming support, so
// react is pinned to 19.0.0 (the version fiber 9.7 was built against). The
// boundary below keeps any remaining WebGL fault local to this figure.
const USE_WEBGL = true;

export default function Cascade() {
  const [sampleIdx, setSampleIdx] = useState(0);
  // Deferred release: moving between plates must not flash the detail panel.
  const [activeId, setActiveId] = useHoverTarget();

  const sample = SAMPLES[sampleIdx];
  const path = useMemo(() => pathFor(sample), [sample]);
  const depth = sample.stops
    ? STAGES.findIndex((s) => s.id === sample.stops)
    : STAGES.length - 1;

  const detail = STAGES.find((s) => s.id === activeId);

  const sceneProps = {
    path,
    depth,
    resolved: Boolean(sample.code),
    activeId,
    onHover: setActiveId,
  };

  return (
    <section className="cascade" id="cascade">
      <div className="page">
        <header className="cascade__head">
          <p className="eyebrow">How a name resolves</p>
          <h2 className="cascade__title">
            Five stages, cheapest first.
            <span className="cascade__title-em"> The model only sees what is left.</span>
          </h2>
          <p className="cascade__lede">
            Each printed name is tested against one stage at a time and stops at
            the first that holds it. Pick a name to follow it through — these are
            real strings, and these are the paths the running cascade takes.
          </p>
        </header>

        <div className="cascade__samples" role="group" aria-label="Choose a printed lab name">
          {SAMPLES.map((s, i) => (
            <button
              key={s.printed}
              className={`chip num ${i === sampleIdx ? "chip--on" : ""}`}
              aria-pressed={i === sampleIdx}
              onClick={() => setSampleIdx(i)}
            >
              {s.printed}
            </button>
          ))}
        </div>

        <div className="cascade__body">
          <div className="cascade__stage">
            {USE_WEBGL ? (
              <SceneBoundary fallback={<StillCascade {...sceneProps} />}>
                <Suspense
                  fallback={<div className="cascade__loading eyebrow">Loading scene</div>}
                >
                  <CascadeScene {...sceneProps} />
                </Suspense>
              </SceneBoundary>
            ) : (
              <StillCascade {...sceneProps} />
            )}

            <figcaption className="cascade__caption">
              <span className="num">{sample.printed}</span>
              <span className="cascade__caption-meta num">
                {sample.specimen} · {sample.unit}
              </span>
            </figcaption>
          </div>

          {/* The list is the accessible surface: keyboard-reachable, and the
              same hover state drives the plates. */}
          <ol className="stages">
            {path.map((s) => (
              <li key={s.id}>
                <button
                  className={`stage stage--${s.state} ${activeId === s.id ? "stage--on" : ""}`}
                  onMouseEnter={() => setActiveId(s.id)}
                  onMouseLeave={() => setActiveId(null)}
                  onFocus={() => setActiveId(s.id)}
                  onBlur={() => setActiveId(null)}
                  aria-describedby={activeId === s.id ? "stage-detail" : undefined}
                >
                  <span className="stage__n num">{s.n}</span>
                  <span className="stage__name">{s.name}</span>
                  <span className="stage__kicker">{s.kicker}</span>
                  <span className="stage__mark num">
                    {s.state === "caught" ? "held" : "passed"}
                  </span>
                </button>
              </li>
            ))}

            <li className={`review ${sample.review ? "review--on" : ""}`}>
              <span className="stage__n num">—</span>
              <span className="stage__name">{REVIEW.name}</span>
              <span className="stage__kicker">{REVIEW.what}</span>
              <span className="stage__mark num">
                {sample.review ? "you confirm" : "skipped"}
              </span>
            </li>
          </ol>
        </div>

        {/* One panel, two states: the stage you are inspecting, or the outcome
            for the name you picked. Never both. */}
        <div className="cascade__detail" id="stage-detail" aria-live="polite">
          {detail ? (
            <>
              <p className="eyebrow">
                Stage {detail.n} · {detail.name} · {detail.cost}
              </p>
              <p className="cascade__what">{detail.what}</p>
              <p className="cascade__why">{detail.why}</p>
            </>
          ) : (
            <>
              <p className="eyebrow">
                {sample.code
                  ? `Resolved · ${sample.code} · confidence ${sample.confidence.toFixed(2)}`
                  : "Unresolved · sent to you"}
              </p>
              <p className="cascade__what">
                {sample.display ?? "No candidate was correct."}
              </p>
              <p className="cascade__why">{sample.note}</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
