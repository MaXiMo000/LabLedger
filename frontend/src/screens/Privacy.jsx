import { Link } from "react-router-dom";
import "./Privacy.css";

/**
 * What happens to a report after it is uploaded, stated as facts about the
 * code rather than as policy. Each line here describes behaviour that exists
 * in the backend; if that behaviour changes, this page changes with it.
 */

const SECTIONS = [
  {
    title: "What is kept",
    items: [
      "The PDF you upload, the text read from it, and each result row: the name, value, unit and range exactly as printed, plus the code and flag LabLedger resolved.",
      "For each record: a display name and, if you enter them, date of birth, sex at birth and a record number. Date of birth and sex select the reference range.",
      "For your account: email, name, a password hash, and one entry per signed-in device.",
    ],
  },
  {
    title: "What is encrypted in the database",
    items: [
      "The PDF, the text read from it, date of birth, sex at birth, record number and the authenticator secret, each with AES-256-GCM.",
      "Result rows are not encrypted field by field. They are what every chart queries, and they are protected by the access checks below and by whatever disk encryption the database host provides.",
    ],
  },
  {
    title: "What leaves this server",
    items: [
      "When no fixed rule can identify a test, its printed name, unit and specimen are sent to Google's Gemini API with a list of candidate codes. The value, the date and anything identifying the person are never sent. If the server has no API key, nothing is sent and the row waits for you in Review.",
      "Password-reset and invitation emails go through EmailJS, which receives the recipient's address and the link.",
      "If you sign in with Google, Google learns that you did. The page loads its typeface from Google Fonts, so your browser contacts Google when it opens.",
      "Nothing else. There is no analytics, advertising or tracking script.",
    ],
  },
  {
    title: "Who can see a record",
    items: [
      "Only accounts its owner has granted access to, each with a role, and the grant can expire. An account without access gets the same answer as for a record that does not exist.",
      "Every time a record is opened, downloaded or changed it is written to a log its owner can read on the Record screen. The log cannot be edited.",
      "Server logs hold identifiers only: never a file name, a test name or a value.",
    ],
  },
  {
    title: "Taking it with you, and removing it",
    items: [
      "The Record screen downloads every result as a FHIR R4 bundle.",
      "Deleting a report removes the PDF and every result read from it.",
      "Deleting your account removes it, its sessions, and every record that no other account can reach. The access log is kept.",
      "Stored PDFs are kept until you delete them, unless the operator of this server has set a retention period, after which only the PDF is disposed of and the results stay.",
    ],
  },
];

export default function Privacy() {
  return (
    <main className="page privacy">
      <Link to="/" className="privacy__mark">LabLedger</Link>
      <p className="eyebrow">Privacy</p>
      <h1 className="privacy__title">What happens to your reports</h1>
      <p className="privacy__lead">
        LabLedger is software somebody runs. Whoever operates this server holds
        the data and is responsible for it; this page describes what the
        software itself does.
      </p>

      {SECTIONS.map((s) => (
        <section key={s.title} className="privacy__block">
          <h2 className="privacy__h">{s.title}</h2>
          <ul className="privacy__list">
            {s.items.map((text) => <li key={text}>{text}</li>)}
          </ul>
        </section>
      ))}

      <p className="privacy__lead">
        LabLedger is not a medical device and gives no advice. It shows what
        was printed, what it resolved that to, and why.
      </p>
    </main>
  );
}
