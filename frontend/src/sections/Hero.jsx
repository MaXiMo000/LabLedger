import IntervalRail from "../components/IntervalRail";
import "./Hero.css";

/**
 * The hero states the problem by demonstrating it.
 *
 * Three real spellings of one test, as three labs actually print them,
 * converging on a single LOINC code with its units reconciled. No claim the
 * page makes is stronger than watching that happen, so the page makes no
 * other claim.
 *
 * The entrance is one orchestrated CSS sequence driven by a --i index per
 * element. It was framer-motion, which is 5.7 MB installed and was earning
 * that on a single animation that runs once and never again; keyframes with a
 * delay do the same job and reduced-motion is handled globally.
 */

const SOURCES = [
  { lab: "Quest Diagnostics", printed: "FERRITIN, SERUM", value: "18", unit: "ng/mL" },
  { lab: "LabCorp",           printed: "Ferritin (S)",    value: "22", unit: "ng/mL" },
  { lab: "Sutter Health",     printed: "FERRTN SER",      value: "40", unit: "µg/L" },
];

export default function Hero() {
  return (
    <header className="hero">
      <div className="page hero__grid">
        <div className="hero__lede">
          <p className="eyebrow rise" style={{ "--d": "0ms" }}>
            Lab result normalization
          </p>

          <h1 className="hero__title rise" style={{ "--d": "60ms" }}>
            Every lab spells it differently.
            <span className="hero__title-em"> Your chart shouldn&rsquo;t care.</span>
          </h1>

          <p className="hero__sub rise" style={{ "--d": "140ms" }}>
            LabLedger reads the PDFs you already have, resolves each test to its
            standard code, converts the units, and puts years of results on one
            line. Anything it isn&rsquo;t certain about, it asks you about.
          </p>

          <div className="hero__actions rise" style={{ "--d": "220ms" }}>
            <a className="btn btn--primary" href="/signin">Add your first report</a>
            <a className="btn btn--quiet" href="#cascade">
              See how it resolves
              <span className="btn__arrow" aria-hidden="true">↓</span>
            </a>
          </div>
        </div>

        {/* The demonstration. Deliberately typeset as a report fragment
            rather than a card: this is the artifact the product works on. */}
        <figure
          className="convergence rise"
          style={{ "--d": "200ms" }}
          aria-label="Three labs print the same test three ways; LabLedger resolves all three to LOINC 2276-4, ferritin in serum."
        >
          <figcaption className="convergence__cap eyebrow">
            As printed, three labs
          </figcaption>

          <ul className="convergence__in">
            {SOURCES.map((s, i) => (
              <li key={s.lab} className="src slide" style={{ "--d": `${260 + i * 70}ms` }}>
                <span className="src__lab">{s.lab}</span>
                <span className="src__printed num">{s.printed}</span>
                <span className="src__val num">
                  {s.value}<span className="src__unit"> {s.unit}</span>
                </span>
              </li>
            ))}
          </ul>

          {/* Three rules merging into one: the resolution, drawn. */}
          <div className="converge" aria-hidden="true">
            <svg viewBox="0 0 240 56" preserveAspectRatio="xMidYMid meet">
              {[8, 28, 48].map((y, i) => (
                <path
                  key={y}
                  className="converge__line"
                  style={{ "--d": `${520 + i * 60}ms` }}
                  d={`M0 ${y} H96 C126 ${y} 126 28 156 28 H240`}
                  fill="none"
                  stroke="var(--rule-strong)"
                  strokeWidth="1"
                />
              ))}
            </svg>
          </div>

          <div className="resolved rise" style={{ "--d": "780ms" }}>
            <div className="resolved__head">
              <span className="resolved__code num">2276-4</span>
              <span className="resolved__stage">stage 1 · exact · 0.95</span>
            </div>
            <p className="resolved__name">Ferritin [Mass/volume] in Serum or Plasma</p>

            <div className="resolved__reading">
              <span className="resolved__value num">18</span>
              <span className="resolved__unit num">ng/mL</span>
              <IntervalRail
                value={18}
                low={24}
                high={336}
                flag="low"
                size="panel"
                label="18 nanograms per millilitre, reference 24 to 336, low"
              />
              <span className="flag flag--low">Low</span>
            </div>
            <p className="resolved__ref num">Reference 24–336 ng/mL · from the report</p>
          </div>
        </figure>
      </div>
    </header>
  );
}
