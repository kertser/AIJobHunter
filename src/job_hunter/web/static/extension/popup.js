/* AI Job Hunter — LinkedIn Cookie Helper extension (popup logic) */

const statusEl = document.getElementById("status");
const sendBtn = document.getElementById("send-btn");
const copyBtn = document.getElementById("copy-btn");

function showStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = "status " + cls;
  statusEl.style.display = "";
}

/**
 * Extract the li_at cookie from the browser's cookie store.
 * Returns the cookie value string, or null if not found.
 */
async function getLiAt() {
  return new Promise((resolve) => {
    chrome.cookies.get(
      { url: "https://www.linkedin.com", name: "li_at" },
      (cookie) => {
        if (cookie && cookie.value) {
          resolve(cookie.value);
        } else {
          resolve(null);
        }
      }
    );
  });
}

/* ── Send to server ── */
sendBtn.addEventListener("click", async () => {
  sendBtn.disabled = true;
  sendBtn.textContent = "⏳ Extracting…";
  statusEl.style.display = "none";

  try {
    const value = await getLiAt();
    if (!value) {
      showStatus(
        "No li_at cookie found. Make sure you are logged in to linkedin.com.",
        "err"
      );
      return;
    }

    const server = document.getElementById("server").value.trim() || "http://localhost:8000";
    sendBtn.textContent = "⏳ Sending…";

    const resp = await fetch(server + "/api/settings/cookies-paste", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ li_at: value }),
    });

    if (resp.ok) {
      showStatus("✅ Cookie sent successfully! You can close this popup.", "ok");
    } else {
      const data = await resp.json().catch(() => ({}));
      showStatus("❌ Server error: " + (data.error || resp.statusText), "err");
    }
  } catch (err) {
    showStatus("❌ Could not reach the server. Is AI Job Hunter running?\n" + err.message, "err");
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "🔑 Extract & Send Cookie";
  }
});

/* ── Copy to clipboard ── */
copyBtn.addEventListener("click", async () => {
  copyBtn.disabled = true;
  statusEl.style.display = "none";

  try {
    const value = await getLiAt();
    if (!value) {
      showStatus(
        "No li_at cookie found. Make sure you are logged in to linkedin.com.",
        "err"
      );
      return;
    }

    await navigator.clipboard.writeText(value);
    showStatus("📋 Cookie copied to clipboard! Paste it into the AI Job Hunter settings.", "ok");
  } catch (err) {
    showStatus("❌ Failed to copy: " + err.message, "err");
  } finally {
    copyBtn.disabled = false;
  }
});

/* ── Restore last-used server URL ── */
chrome.storage?.local?.get("server", (items) => {
  if (items && items.server) {
    document.getElementById("server").value = items.server;
  }
});

document.getElementById("server").addEventListener("change", (e) => {
  chrome.storage?.local?.set({ server: e.target.value.trim() });
});

