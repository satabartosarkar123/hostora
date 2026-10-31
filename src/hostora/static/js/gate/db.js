const DB_NAME = "hostora_gate_v1";
const DB_VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("manifest")) {
        db.createObjectStore("manifest", { keyPath: "key" });
      }
      if (!db.objectStoreNames.contains("scans")) {
        db.createObjectStore("scans", { keyPath: "seq", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains("meta")) {
        db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function randomDeviceId() {
  const alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let id = "dev_";
  for (let i = 0; i < 12; i++) {
    id += alphabet[bytes[i] % alphabet.length];
  }
  return id;
}

export async function getOrCreateDeviceId() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("meta", "readwrite");
    const store = tx.objectStore("meta");
    const getReq = store.get("device_id");
    getReq.onsuccess = () => {
      if (getReq.result && getReq.result.value) {
        resolve(getReq.result.value);
      } else {
        const devId = randomDeviceId();
        store.put({ key: "device_id", value: devId });
        resolve(devId);
      }
    };
    getReq.onerror = () => reject(getReq.error);
  });
}

export async function saveManifest(eventKey, manifest) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("manifest", "readwrite");
    tx.objectStore("manifest").put({ key: eventKey, data: manifest });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function loadManifest(eventKey) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("manifest", "readonly");
    const req = tx.objectStore("manifest").get(eventKey);
    req.onsuccess = () => resolve(req.result ? req.result.data : null);
    req.onerror = () => reject(req.error);
  });
}

export async function appendScan(scanRecord) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("scans", "readwrite");
    const store = tx.objectStore("scans");
    const req = store.add({ ...scanRecord, sent: false });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function getAllScansForFunction(functionId) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("scans", "readonly");
    const req = tx.objectStore("scans").getAll();
    req.onsuccess = () => {
      const all = req.result || [];
      resolve(all.filter((s) => s.function_id === functionId));
    };
    req.onerror = () => reject(req.error);
  });
}

export async function getUnsentScans(functionId, limit = 200) {
  const all = await getAllScansForFunction(functionId);
  return all.filter((s) => !s.sent).slice(0, limit);
}

export async function markScansSent(seqs) {
  if (!seqs || !seqs.length) return;
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("scans", "readwrite");
    const store = tx.objectStore("scans");
    seqs.forEach((seq) => {
      const getReq = store.get(seq);
      getReq.onsuccess = () => {
        if (getReq.result) {
          getReq.result.sent = true;
          store.put(getReq.result);
        }
      };
    });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

