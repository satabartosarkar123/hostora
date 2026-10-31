// Firebase modular ESM sign-in for Hostora host app (pinned to 11.0.1)
const FIREBASE_VERSION = "11.0.1";

function showError(msg) {
  const errEl = document.getElementById("login-error");
  if (!errEl) return;
  errEl.textContent = msg;
  errEl.style.display = "block";
}

async function handleGoogleSignIn(btn) {
  const apiKey = btn.dataset.apiKey || "";
  const authDomain = btn.dataset.authDomain || "";
  const projectId = btn.dataset.projectId || "";
  const appId = btn.dataset.appId || "";

  if (!apiKey || !projectId) {
    showError("Firebase authentication is not configured in this environment. Use Try demo if enabled.");
    return;
  }

  try {
    const [{ initializeApp }, { getAuth, GoogleAuthProvider, signInWithPopup }] = await Promise.all([
      import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app.js`),
      import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-auth.js`),
    ]);

    const app = initializeApp({ apiKey, authDomain, projectId, appId });
    const auth = getAuth(app);
    const provider = new GoogleAuthProvider();
    const cred = await signInWithPopup(auth, provider);
    const idToken = await cred.user.getIdToken();

    const resp = await fetch("/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken }),
      credentials: "same-origin",
    });

    if (!resp.ok) {
      showError("Sign-in verification failed. Please try again.");
      return;
    }
    window.location.assign("/app");
  } catch (err) {
    showError(err && err.message ? err.message : "Google sign-in could not complete.");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("google-signin-btn");
  if (btn) {
    btn.addEventListener("click", () => handleGoogleSignIn(btn));
  }
});
