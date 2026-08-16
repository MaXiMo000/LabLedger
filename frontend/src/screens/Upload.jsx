import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, messageFor } from "../api/client";
import { usePatient } from "../patients/PatientContext";
import "./Upload.css";

/**
 * Add a report.
 *
 * Processing is asynchronous, so the screen's real job is telling the truth
 * about where a file has got to. Each upload shows its own stage — queued,
 * reading, resolving, done — rather than one spinner covering everything, and
 * a failure names what went wrong on that file without taking the others down.
 */

const STAGE_LABEL = {
  queued: "Queued",
  extracting: "Reading the PDF",
  mapping: "Resolving tests",
  needs_review: "Ready — some need confirming",
  done: "Done",
  failed: "Could not read this file",
};

const TERMINAL = ["done", "needs_review", "failed"];

export default function Upload() {
  const [items, setItems] = useState([]); // {key, name, status, row_count, error, id}
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { active, activeId } = usePatient();

  const update = (key, patch) =>
    setItems((list) => list.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  /** Poll one document until it stops moving. */
  async function follow(key, id) {
    for (let i = 0; i < 150; i++) {
      const { data } = await api.get(`/documents/item/${id}`);
      // Rows extracted is not the useful number — rows that resolved to a test
      // is. A file can yield twenty tabular lines and nothing recognisable.
      const resolved = (data.observations ?? []).filter((o) => o.loinc_code).length;
      const pending = (data.observations ?? []).filter((o) => o.review_status === "pending").length;
      update(key, {
        status: data.status,
        row_count: data.row_count,
        resolved,
        pending,
        error: data.error,
        lab: data.lab_name,
      });
      if (TERMINAL.includes(data.status)) {
        qc.invalidateQueries({ queryKey: ["panels"] });
        qc.invalidateQueries({ queryKey: ["documents"] });
        qc.invalidateQueries({ queryKey: ["review"] });
        return;
      }
      await new Promise((r) => setTimeout(r, 1200));
    }
    update(key, { status: "failed", error: "Still processing. Check Reports in a moment." });
  }

  const send = useMutation({
    mutationFn: async (file) => {
      const key = `${file.name}-${Date.now()}-${Math.random()}`;
      setItems((list) => [{ key, name: file.name, status: "queued" }, ...list]);

      const body = new FormData();
      body.append("file", file);
      try {
        const { data } = await api.post(`/documents/${activeId}`, body, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        update(key, { id: data.id, status: data.status });
        await follow(key, data.id);
      } catch (err) {
        update(key, { status: "failed", error: messageFor(err) });
      }
    },
  });

  function accept(fileList) {
    const files = [...fileList].filter((f) => f.type === "application/pdf" || f.name.endsWith(".pdf"));
    const rejected = fileList.length - files.length;
    if (rejected > 0) {
      setItems((list) => [
        { key: `rej-${Date.now()}`, name: `${rejected} file${rejected > 1 ? "s" : ""} skipped`,
          status: "failed", error: "Only PDFs can be read." },
        ...list,
      ]);
    }
    files.forEach((f) => send.mutate(f));
  }

  const resolvedTotal = items.reduce((n, i) => n + (i.resolved ?? 0), 0);
  const pendingTotal = items.reduce((n, i) => n + (i.pending ?? 0), 0);
  const allFailed = items.length > 0 && items.every((i) => i.status === "failed");

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Reports</p>
          <h1 className="screen__title">Add a report</h1>
        </div>
      </header>

      <p className="upload__lede">
        Drop the PDFs your lab gave you. LabLedger reads them here — the file is
        encrypted before it is stored, and nothing but the test name, unit and
        specimen ever leaves this machine.
      </p>

      {/* The label is the drop target, so keyboard and pointer reach the same
          control and the file input stays a real input. */}
      <label
        className={`drop ${dragging ? "drop--over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          accept(e.dataTransfer.files);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          className="sr-only"
          onChange={(e) => { accept(e.target.files); e.target.value = ""; }}
        />
        <span className="drop__title">Drop PDFs here, or choose files</span>
        <span className="drop__meta num">PDF · up to 25 MB · 100 pages</span>
      </label>

      {items.length > 0 && (
        <ol className="uploads">
          {items.map((i) => (
            <li key={i.key} className={`up up--${i.status}`}>
              <span className="up__name num">{i.name}</span>
              <span className="up__status">
                {STAGE_LABEL[i.status] ?? i.status}
                {i.lab ? ` · ${i.lab}` : ""}
              </span>
              <span className="up__rows num">
                {i.resolved != null
                  ? `${i.resolved} test${i.resolved === 1 ? "" : "s"}`
                  : i.row_count != null
                    ? `${i.row_count} rows`
                    : ""}
              </span>
              {i.error && <span className="up__error">{i.error}</span>}
            </li>
          ))}
        </ol>
      )}

      {allFailed && (
        <p className="upload__none">
          Nothing readable came out of that. LabLedger looks for a table of test
          names, values and units — try the PDF your lab issued rather than a
          scan or a summary letter.
        </p>
      )}

      {resolvedTotal > 0 && (
        <div className="upload__next">
          <button className="btn btn--primary" onClick={() => navigate("/app")}>
            See your results
          </button>
          {pendingTotal > 0 && (
            <button className="btn btn--quiet" onClick={() => navigate("/app/review")}>
              Confirm {pendingTotal} uncertain result{pendingTotal === 1 ? "" : "s"}
            </button>
          )}
        </div>
      )}
    </>
  );
}
