import { getUnsentScans, markScansSent } from "./db.js";

export async function syncPendingScans(eventKey, gateToken, functionId, onSynced) {
  if (!navigator.onLine) return null;
  const unsent = await getUnsentScans(functionId, 200);
  if (!unsent.length) return null;

  const scansPayload = unsent.map((s) => ({
    deviceId: s.device_id,
    seq: s.seq,
    passId: s.pass_id,
    seats: s.seats,
    at: s.at,
  }));

  try {
    const resp = await fetch(`/v1/gate/${eventKey}/scans:batch?t=${encodeURIComponent(gateToken)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        functionId,
        scans: scansPayload,
      }),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    await markScansSent(unsent.map((s) => s.seq));
    if (typeof onSynced === "function") {
      onSynced(data);
    }
    return data;
  } catch (_err) {
    return null;
  }
}

