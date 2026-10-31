import {
  appendScan,
  getAllScansForFunction,
  getOrCreateDeviceId,
  loadManifest,
  saveManifest,
} from "./db.js";
import { syncPendingScans } from "./sync.js";
import { verifyPassToken } from "./verify.js";

let currentManifest = null;
let deviceId = null;

function setBanner(status, title, detail) {
  const banner = document.getElementById("gate-result-banner");
  if (!banner) return;
  banner.className = `card gate-banner gate-banner--${status}`;
  banner.dataset.status = status;
  banner.innerHTML = `<h2 style="margin:0 0 0.25rem 0;">${title}</h2><p style="margin:0;">${detail}</p>`;
}

async function updateLocalStats() {
  if (!currentManifest) return;
  const scans = await getAllScansForFunction(currentManifest.functionId);
  const totalArrivals = scans.reduce((acc, s) => acc + (s.seats || 0), 0);
  const unsentCount = scans.filter((s) => !s.sent).length;

  const arrEl = document.getElementById("gate-local-arrivals");
  const unsentEl = document.getElementById("gate-unsent-count");
  if (arrEl) arrEl.textContent = String(totalArrivals);
  if (unsentEl) unsentEl.textContent = String(unsentCount);
}

function renderHouseholdLookup(query = "") {
  const tbody = document.getElementById("gate-lookup-tbody");
  if (!tbody || !currentManifest) return;
  const q = query.trim().toLowerCase();
  const passes = (currentManifest.passes || []).filter((p) =>
    !q || p.displayName.toLowerCase().includes(q) || p.passId.toLowerCase().includes(q)
  );
  tbody.innerHTML = passes
    .map(
      (p) => `
      <tr>
        <td><strong>${p.displayName}</strong></td>
        <td><code>${p.passId}</code></td>
        <td>${p.seats}</td>
      </tr>`
    )
    .join("");
}

async function handleScanText(rawText) {
  if (!currentManifest) {
    setBanner("red", "Manifest not loaded", "Connect to the network once to load the event manifest.");
    return { status: "red", reason: "no_manifest" };
  }

  const claims = await verifyPassToken(
    String(rawText || ""),
    currentManifest.eventKey,
    currentManifest.publicKey
  );
  if (!claims) {
    setBanner("red", "Invalid or forged pass", "Please ask a family member at the desk.");
    return { status: "red", reason: "forged_or_expired" };
  }

  const manifestEntry = (currentManifest.passes || []).find((p) => p.passId === claims.passId);
  const existingScans = await getAllScansForFunction(currentManifest.functionId);
  const alreadyScannedSeats = existingScans
    .filter((s) => s.pass_id === claims.passId)
    .reduce((acc, s) => acc + (s.seats || 0), 0);

  if (!manifestEntry) {
    if (currentManifest.gateMode === "strict") {
      setBanner("red", "Unknown pass (strict mode)", "Please ask a family member at the desk.");
      return { status: "red", reason: "unknown_strict" };
    }
    await appendScan({
      device_id: deviceId,
      function_id: currentManifest.functionId,
      pass_id: claims.passId,
      seats: claims.seats,
      at: Date.now(),
    });
    await updateLocalStats();
    setBanner(
      "amber",
      `Unlisted pass — ${claims.seats} seats`,
      "Signed pass not in initial manifest (counted in soft mode)."
    );
    return { status: "amber", seats: claims.seats, passId: claims.passId };
  }

  const remainingBefore = manifestEntry.seats - alreadyScannedSeats;
  if (remainingBefore <= 0) {
    if (currentManifest.gateMode === "strict") {
      setBanner("red", "Pass already used", `${manifestEntry.displayName} (${manifestEntry.seats} seats) already checked in.`);
      return { status: "red", reason: "duplicate_strict" };
    }
    await appendScan({
      device_id: deviceId,
      function_id: currentManifest.functionId,
      pass_id: claims.passId,
      seats: claims.seats,
      at: Date.now(),
    });
    await updateLocalStats();
    setBanner(
      "amber",
      `Already scanned — ${manifestEntry.displayName}`,
      "Counted again in soft mode (flagged as possible duplicate)."
    );
    return { status: "amber", seats: claims.seats, passId: claims.passId };
  }

  const seatsToRecord = Math.min(claims.seats, remainingBefore);
  await appendScan({
    device_id: deviceId,
    function_id: currentManifest.functionId,
    pass_id: claims.passId,
    seats: seatsToRecord,
    at: Date.now(),
  });
  await updateLocalStats();
  setBanner(
    "green",
    `Welcome — ${seatsToRecord} seats`,
    `${manifestEntry.displayName} (Pass ${claims.passId})`
  );

  const root = document.getElementById("gate-app");
  if (root && navigator.onLine) {
    await syncPendingScans(
      root.dataset.eventKey,
      root.dataset.gateToken,
      currentManifest.functionId,
      () => updateLocalStats()
    );
  }
  return { status: "green", seats: seatsToRecord, passId: claims.passId };
}

async function initGate() {
  const root = document.getElementById("gate-app");
  if (!root) return;
  const eventKey = root.dataset.eventKey;
  const gateToken = root.dataset.gateToken;

  deviceId = await getOrCreateDeviceId();

  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/gate/sw.js", { scope: "/gate/" });
    } catch (_err) {
      // Service worker registration optional in constrained contexts
    }
  }

  try {
    const resp = await fetch(`/v1/gate/${eventKey}/manifest?t=${encodeURIComponent(gateToken)}`);
    if (resp.ok) {
      currentManifest = await resp.json();
      await saveManifest(eventKey, currentManifest);
    }
  } catch (_err) {
    // Offline fallback
  }

  if (!currentManifest) {
    currentManifest = await loadManifest(eventKey);
  }

  if (currentManifest) {
    const fnTitle = document.getElementById("gate-function-name");
    if (fnTitle) {
      fnTitle.textContent = `${currentManifest.functionName} (${currentManifest.gateMode} mode)`;
    }
    renderHouseholdLookup("");
    await updateLocalStats();
  }

  const searchInput = document.getElementById("gate-lookup-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => renderHouseholdLookup(e.target.value));
  }

  const manualScanForm = document.getElementById("gate-manual-scan-form");
  if (manualScanForm) {
    manualScanForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("gate-token-input");
      if (input && input.value.trim()) {
        await handleScanText(input.value.trim());
        input.value = "";
      }
    });
  }

  const triggerSync = async () => {
    if (!currentManifest) return;
    await syncPendingScans(eventKey, gateToken, currentManifest.functionId, () => updateLocalStats());
    await updateLocalStats();
  };

  window.addEventListener("online", triggerSync);
  setInterval(triggerSync, 30000);

  // Expose test and automation hooks
  window.__hostoraScan = handleScanText;
  window.__hostoraSync = triggerSync;
  window.__hostoraGateReady = true;
  root.dataset.ready = "true";
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initGate);
} else {
  initGate();
}
