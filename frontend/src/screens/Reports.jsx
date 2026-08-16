import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ConfirmDialog } from "../components/Modal";
import { api } from "../api/client";
import { usePatient } from "../patients/PatientContext";
import "./Reports.css";

/**
 * Every report you have added, newest first.
 *
 * Ordered by collection date rather than upload date — the clinical fact is
 * when blood was drawn, not when you got round to scanning the page. Each row
 * carries how it was read and how many results came out, so a file that
 * produced nothing is visible instead of silently absent.
 */

const STATUS = {
  queued: "Queued",
  extracting: "Reading",
  mapping: "Resolving",
  needs_review: "Needs confirming",
  done: "Done",
  failed: "Failed",
};

function fmt(d) {
  return d ? new Date(d).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  }) : "Date not found";
}

export default function Reports() {
  const qc = useQueryClient();
  const [pendingDelete, setPendingDelete] = useState(null);
  const { activeId } = usePatient();

  const { data: docs, isPending, error } = useQuery({
    queryKey: ["documents", activeId],
    queryFn: async () => (await api.get(`/documents/${activeId}`)).data,
    enabled: Boolean(activeId),
  });

  const remove = useMutation({
    mutationFn: (id) => api.delete(`/documents/item/${id}`),
    onSuccess: () => {
      ["documents", "panels", "review"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] })
      );
      setPendingDelete(null);
    },
  });

  /**
   * Open the stored PDF.
   *
   * It cannot be a plain link: the access token is deliberately held in a
   * closure rather than a cookie or storage, so a browser navigation to the
   * file URL arrives with no credentials and the API answers "Not
   * authenticated" — correctly. Fetching through the client attaches the
   * token, and the blob URL is revoked once the tab has taken it.
   */
  const openPdf = useMutation({
    mutationFn: async (id) => {
      const { data } = await api.get(`/documents/item/${id}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const win = window.open(url, "_blank", "noopener");
      if (!win) throw new Error("popup-blocked");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
  });

  const reprocess = useMutation({
    mutationFn: (id) => api.post(`/documents/item/${id}/reprocess`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  if (!activeId || isPending) return <p className="muted">Loading reports…</p>;
  if (error) return <p className="muted">Could not load reports.</p>;

  if (!docs.length) {
    return (
      <div className="empty">
        <h2 className="empty__title">No reports yet</h2>
        <p className="empty__body">
          Add the PDFs your lab gave you and LabLedger will read them.
        </p>
        <Link className="btn btn--primary" to="/app/upload">Add a report</Link>
      </div>
    );
  }

  const results = docs.reduce((n, d) => n + d.row_count, 0);

  return (
    <>
      <header className="screen__head">
        <div>
          <p className="eyebrow">Reports</p>
          <h1 className="screen__title">
            {docs.length} report{docs.length > 1 ? "s" : ""} · {results} results
          </h1>
        </div>
        <Link className="btn btn--primary" to="/app/upload">Add a report</Link>
      </header>

      <ol className="reports">
        {docs.map((d) => (
          <li key={d.id} className={`report report--${d.status}`}>
            <div className="report__when">
              <span className="report__date num">{fmt(d.collected_at)}</span>
              <span className="report__src num">
                {d.date_source === "reported" ? "report date" : d.date_source === "none" ? "" : "collected"}
              </span>
            </div>

            <div className="report__what">
              <span className="report__lab">{d.lab_name ?? "Lab not identified"}</span>
              <span className="report__file num">{d.filename}</span>
              {d.error && <span className="report__error">{d.error}</span>}
            </div>

            <span className="report__rows num">
              {d.row_count > 0 ? `${d.row_count} results` : "—"}
            </span>

            <span className={`report__status report__status--${d.status} num`}>
              {STATUS[d.status] ?? d.status}
            </span>

            <div className="report__actions">
              <button
                className="report__link"
                onClick={() => openPdf.mutate(d.id)}
                disabled={openPdf.isPending}
              >
                {openPdf.isPending ? "Opening…" : "Open PDF"}
              </button>
              <button
                className="report__link"
                onClick={() => reprocess.mutate(d.id)}
                disabled={reprocess.isPending}
              >
                Re-read
              </button>
              <button
                className="report__link report__link--danger"
                onClick={() => setPendingDelete(d)}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ol>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this report?"
          description={`${pendingDelete.filename} and the ${pendingDelete.row_count} results read from it will be removed. Trends that used those results will lose those points. This cannot be undone.`}
          confirmLabel={`Delete ${pendingDelete.row_count} results`}
          busy={remove.isPending}
          onConfirm={() => remove.mutate(pendingDelete.id)}
          onClose={() => setPendingDelete(null)}
        />
      )}
    </>
  );
}
