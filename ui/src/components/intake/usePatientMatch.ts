import { useState } from "react";
import { api, ApiError, type PatientMatch } from "../../api";

/** The fax's patient looked up in the chart, and the record a person confirmed as them. */
export function usePatientMatch(faxId: string | undefined) {
  const [match, setMatch] = useState<PatientMatch | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const lookUp = async (name: string, born: string, ref?: string) => {
    setLooking(true);
    setFailure(null);
    try {
      // The name goes in the request body, never the address: addresses reach logs.
      setMatch(await api<PatientMatch>(`/api/intake/${faxId}/match`, { method: "POST", body: JSON.stringify({ name, birth_date: born, ref }) }));
      setConfirmed(null);
    } catch (e) {
      setFailure((e as ApiError).message);
    } finally {
      setLooking(false);
    }
  };

  return { match, confirmed, setConfirmed, looking, failure, lookUp };
}
