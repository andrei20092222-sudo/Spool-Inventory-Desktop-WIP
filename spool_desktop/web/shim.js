/* Lets the Home Assistant panel run on its own: a tiny `hass` object whose callWS and subscribeMessage talk to the
 * local server of the app. Everything stays on this computer (127.0.0.1). */
(() => {
  const HEADERS = { "Content-Type": "application/json", "X-Spool-Desktop": "1" };

  async function callWS(msg) {
    let res;
    try {
      res = await fetch("/api", { method: "POST", headers: HEADERS, body: JSON.stringify(msg) });
    } catch (err) {
      throw { message: "The app is not running any more. Start it again and reload this page." };
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw { message: body.error || `Error ${res.status}` };
    return body.result;
  }

  function subscribeMessage(callback, _msg) {
    const source = new EventSource("/events");
    source.onmessage = (ev) => { try { callback(JSON.parse(ev.data)); } catch (err) { console.error(err); } };
    return Promise.resolve(() => source.close());
  }

  const hass = {
    user: { is_admin: true, name: "Local" },
    language: navigator.language,
    connection: { subscribeMessage },
    callWS,
  };

  async function start() {
    await customElements.whenDefined("spool-inventory-panel");
    const panel = document.createElement("spool-inventory-panel");
    panel.style.display = "block";
    panel.panel = { config: { static_url: "/static", version: (document.querySelector('meta[name="spool-version"]') || {}).content || "" } };
    panel.narrow = window.matchMedia("(max-width: 700px)").matches;
    panel.hass = hass;
    document.getElementById("app").append(panel);
    window.matchMedia("(max-width: 700px)").addEventListener("change", (e) => { panel.narrow = e.matches; });
    document.getElementById("quit").addEventListener("click", async () => {
      if (!confirm("Close Spool Inventory? Your data is saved.")) return;
      try { await callWS({ type: "spool_inventory/quit" }); } catch (err) { /* the server is already gone */ }
      document.body.textContent = "Spool Inventory is closed. You can close this tab.";
    });
  }
  start();
})();
