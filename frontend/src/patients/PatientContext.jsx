import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

const PatientContext = createContext(null);

const REMEMBERED = "labledger.patient";

/**
 * Which record is open.
 *
 * The single most dangerous failure in a multi-patient interface is charting
 * one person's results under another's name, so the active patient is explicit
 * state rather than something inferred from a URL or the last thing clicked.
 *
 * The choice is remembered across reloads — a clinician returning to the tab
 * they left should land where they left. Only the id is stored, never a name
 * or anything clinical: it is meaningless without a live session, and the API
 * still refuses it if the grant has since been revoked.
 */
export function PatientProvider({ children }) {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState(() => localStorage.getItem(REMEMBERED));

  const { data: patients = [], isPending } = useQuery({
    queryKey: ["patients"],
    queryFn: async () => (await api.get("/patients")).data,
  });

  // A remembered id can be stale: the grant may have been revoked, or the
  // record deleted. Fall back to the first reachable one rather than leaving
  // the app pointed at something that will 404 on every request.
  useEffect(() => {
    if (isPending || patients.length === 0) return;
    const stillThere = patients.some((p) => p.id === activeId);
    if (!stillThere) {
      const next = patients[0].id;
      setActiveId(next);
      localStorage.setItem(REMEMBERED, next);
    }
  }, [patients, isPending, activeId]);

  const select = useCallback((id) => {
    setActiveId(id);
    localStorage.setItem(REMEMBERED, id);
    // Nothing cached under the previous patient is valid for this one.
    // Removing rather than invalidating: an invalidated query briefly serves
    // the old patient's data while it refetches, which is exactly the failure
    // this whole model exists to prevent.
    ["panels", "series", "documents", "review", "provenance"].forEach((k) =>
      qc.removeQueries({ queryKey: [k] })
    );
  }, [qc]);

  const active = patients.find((p) => p.id === activeId) ?? null;

  const value = useMemo(
    () => ({ patients, active, activeId: active?.id ?? null, select, loading: isPending }),
    [patients, active, select, isPending]
  );

  return <PatientContext.Provider value={value}>{children}</PatientContext.Provider>;
}

export function usePatient() {
  const ctx = useContext(PatientContext);
  if (!ctx) throw new Error("usePatient must be used inside PatientProvider");
  return ctx;
}
