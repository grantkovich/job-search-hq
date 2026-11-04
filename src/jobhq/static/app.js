// Job Search HQ browser script. Loaded from this site only (the CSP forbids inline script).
(function () {
  "use strict";
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  const store = {
    get(key) { try { const v = sessionStorage.getItem(key); sessionStorage.removeItem(key); return v ? JSON.parse(v) : null; } catch (_) { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode: nothing is carried over */ } },
  };
  const ready = (fn) => (document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", fn) : fn());

  // Every htmx request carries the CSRF token in a header.
  document.addEventListener("htmx:configRequest", (e) => { e.detail.headers["X-CSRF-Token"] = csrf(); });

  // ------------------------------------------------------------------ toasts
  // The server sends HX-Trigger {"jobhq:toast": "..."} or {"jobhq:error": "..."}. Messages pause while
  // hovered or focused; errors stay until closed, so there is time to read them.
  let reloading = false;
  function toast(message, bad) {
    const box = document.getElementById("toasts");
    if (!box || !message || reloading) return;
    // Screen readers hear the message once, from a live region that is always on the page.
    const say = document.getElementById(bad ? "announce-alert" : "announce");
    if (say) { say.textContent = ""; setTimeout(() => { say.textContent = message; }, 50); }
    const el = document.createElement("div");
    el.className = "toast" + (bad ? " bad" : "");
    const text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss message");
    close.addEventListener("click", () => el.remove());
    el.append(text, close);
    box.appendChild(el);
    if (bad) return;
    let left = Math.max(4500, message.length * 70), started = Date.now(), timer = null;
    const run = () => { started = Date.now(); timer = setTimeout(() => el.remove(), left); };
    const pause = () => { if (timer) { clearTimeout(timer); timer = null; left -= Date.now() - started; } };
    el.addEventListener("mouseenter", pause);
    el.addEventListener("focusin", pause);
    el.addEventListener("mouseleave", () => { if (!el.contains(document.activeElement) && !timer) run(); });
    el.addEventListener("focusout", (ev) => { if (!el.contains(ev.relatedTarget) && !el.matches(":hover") && !timer) run(); });
    run();
  }
  document.addEventListener("jobhq:toast", (e) => toast(e.detail.value, false));
  document.addEventListener("jobhq:error", (e) => toast(e.detail.value, true));
  document.addEventListener("htmx:responseError", (e) => {
    if (e.detail.xhr.getResponseHeader("HX-Trigger")) return;
    const s = e.detail.xhr.status;
    toast(s >= 502 && s <= 504 ? "The server isn't responding; it may be restarting. Try again in a minute." : "Something went wrong (" + s + "). Try again.", true);
  });
  document.addEventListener("htmx:sendError", () => toast("Can't reach the server. Check your connection.", true));

  // ------------------------------------------- field errors from the server
  // HX-Trigger {"jobhq:fields": {"fields": {"email": "problem"}}}: mark each field and say what's wrong under it.
  function clearFieldErrors(form) {
    form.querySelectorAll(".field-error").forEach((n) => n.remove());
    form.querySelectorAll("[aria-invalid]").forEach((n) => {
      n.removeAttribute("aria-invalid");
      const ids = (n.getAttribute("aria-describedby") || "").split(" ").filter((id) => id && !id.startsWith("err-"));
      ids.length ? n.setAttribute("aria-describedby", ids.join(" ")) : n.removeAttribute("aria-describedby");
    });
  }
  document.addEventListener("jobhq:fields", (e) => {
    const form = e.target.closest ? e.target.closest("form") : null;
    const fields = (e.detail && e.detail.fields) || {};
    if (!form) return;
    clearFieldErrors(form);
    let first = null;
    for (const [name, problem] of Object.entries(fields)) {
      const field = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (!field) continue;
      const note = document.createElement("span");
      note.className = "field-error";
      note.id = "err-" + name;
      note.textContent = problem;
      field.insertAdjacentElement("afterend", note);
      field.setAttribute("aria-invalid", "true");
      field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), note.id].filter(Boolean).join(" "));
      first = first || field;
    }
    if (first) first.focus();
  });
  document.addEventListener("htmx:beforeRequest", (e) => { const f = e.detail.elt; if (f && f.tagName === "FORM") clearFieldErrors(f); });

  // ------------------------------------------- full reloads keep their place
  // Many actions reload the page (HX-Refresh). Before the reload, remember the message, which <details>
  // were open and which control was used; after it, show the message and put focus back on that control
  // (or the nearest heading if it's gone).
  function remember(elt, message, bad, samePage) {
    const ids = [];
    for (let n = elt; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) if (n.id && n.id !== "main") ids.push(n.id);
    store.set("jobhq:after", {
      path: samePage ? location.pathname + location.search : null, message, bad: !!bad, ids,
      post: elt && elt.getAttribute ? elt.getAttribute("hx-post") : null, vals: elt && elt.getAttribute ? elt.getAttribute("hx-vals") : null,
      open: [...document.querySelectorAll("main details[id][open]")].map((d) => d.id),
    });
  }
  function focusOn(el) {
    if (!el) return;
    if (!el.matches("a[href], button, input, select, textarea, summary, [tabindex]")) el.setAttribute("tabindex", "-1");
    el.focus();
  }
  function restore() {
    const after = store.get("jobhq:after");
    if (!after) return;
    if (after.path === location.pathname + location.search) {
      (after.open || []).forEach((id) => { const d = document.getElementById(id); if (d && d.tagName === "DETAILS") d.open = true; });
      const box = after.ids.map((id) => document.getElementById(id)).find(Boolean);
      let target = null;
      if (box && after.post) {
        target = [...box.querySelectorAll("[hx-post]")].find((n) => n.getAttribute("hx-post") === after.post && (n.getAttribute("hx-vals") || null) === after.vals);
        if (target && target.tagName === "FORM") target = target.querySelector("button:not([type=button]), [type=submit]") || target.querySelector("input:not([type=hidden]), select, textarea");
      }
      if (target && target.closest("details:not([open])")) target.closest("details").open = true;
      if (!target && box) target = box.matches("details") ? box.querySelector("summary") : (box.querySelector("h1, h2, h3, summary") || box);
      if (target) focusOn(target);
    }
    if (after.message) toast(after.message, after.bad);
  }
  document.addEventListener("htmx:beforeOnLoad", (e) => {
    const xhr = e.detail.xhr;
    const refresh = xhr.getResponseHeader("HX-Refresh") === "true", go = xhr.getResponseHeader("HX-Redirect");
    if (!refresh && !go) return;
    let message = "", bad = false;
    try {
      const t = JSON.parse(xhr.getResponseHeader("HX-Trigger") || "{}");
      message = t["jobhq:toast"] || t["jobhq:error"] || "";
      bad = !!t["jobhq:error"];
    } catch (_) { /* not JSON: no message */ }
    remember(e.detail.elt, message, bad, refresh && !go);
    reloading = true;
  });
  ready(restore);

  // --------------------------- focus survives swaps that remove its element
  // When a swap deletes or replaces the focused item (Approve, Dismiss, Skip, Undo), focus moves to what
  // took its place, the next item, or the section heading, instead of falling to the top of the page.
  document.addEventListener("htmx:beforeSwap", (e) => {
    const target = e.detail.target;
    const active = document.activeElement;
    if (!target || !active || active === document.body || !target.contains(active)) return;
    const parent = target.parentElement, index = parent ? [...parent.children].indexOf(target) : -1;
    const next = target.nextElementSibling, prev = target.previousElementSibling;
    const section = target.closest("section, details, .card") || document.getElementById("main");
    setTimeout(() => {
      if (document.activeElement && document.activeElement !== document.body && document.activeElement.isConnected) return;
      const same = parent && parent.isConnected && index >= 0 ? parent.children[index] : null;
      const heading = section && section.isConnected ? section.querySelector("h1, h2, h3, summary") : document.querySelector("main h1");
      const pick = [same, next, prev].find((n) => n && n.isConnected) || heading || document.getElementById("main");
      focusOn(pick);
    }, 60);
  });

  // ------------------------------------------------------------------ chat
  // Enter sends, Shift+Enter is a new line; clear the box after sending; keep the newest message in view.
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.matches && t.matches("textarea[data-enter-sends]") && e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      t.form.requestSubmit();
    }
  });
  document.addEventListener("htmx:afterRequest", (e) => {
    const form = e.detail.elt;
    if (form.matches && form.matches("form[data-reset-on-send]") && e.detail.successful) form.reset();
  });
  const scrollChat = () => { const m = document.getElementById("messages"); if (m) m.scrollTop = m.scrollHeight; };
  document.addEventListener("htmx:afterSettle", scrollChat);
  ready(scrollChat);
  // The list of past chats starts closed on small screens.
  ready(() => { if (window.matchMedia("(max-width: 760px)").matches) document.querySelectorAll("details[data-open-wide]").forEach((d) => { d.open = false; }); });

  // Copy buttons: data-copy points at the field to copy.
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest && e.target.closest("[data-copy]");
    if (!btn) return;
    e.preventDefault();
    const field = document.querySelector(btn.dataset.copy);
    if (!field) return;
    try { await navigator.clipboard.writeText(field.value || field.textContent || ""); toast("Copied.", false); }
    catch (_) { field.select && field.select(); toast("Couldn't copy automatically. The text is selected: copy it with your keyboard or the menu.", true); }
  });

  // Typed practice answers: time from first keystroke to submit, for words per minute.
  document.addEventListener("input", (e) => {
    const form = e.target.closest && e.target.closest("form[data-timer]");
    if (form && !form.dataset.started) form.dataset.started = String(Date.now());
  });
  document.addEventListener("htmx:configRequest", (e) => {
    const form = e.detail.elt;
    if (form.matches && form.matches("form[data-timer]") && form.dataset.started) {
      e.detail.parameters.seconds = String(Math.round((Date.now() - Number(form.dataset.started)) / 1000));
    }
  });

  // ------------------------------------------- service worker and push
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});

  const IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const BLOCKED = "Notifications are blocked for this site. To allow them: in Chrome or Edge, click the icon left of the address, then Site settings → Notifications → Allow. " +
    "On iPhone or iPad: Settings → Notifications → Job Search HQ. On Android: long-press the app icon → App info → Notifications. Then reload this page.";
  const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function b64ToBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(body), credentials: "same-origin" });
    const trig = r.headers.get("HX-Trigger");
    if (trig) { const t = JSON.parse(trig); toast(t["jobhq:toast"] || t["jobhq:error"], !!t["jobhq:error"]); }
    else if (!r.ok) toast(r.status >= 502 ? "The server isn't responding; it may be restarting. Try again in a minute." : "Something went wrong (" + r.status + "). Try again.", true);
    return r.ok;
  }
  async function pushState() {
    const el = document.querySelector("[data-push-state]");
    if (!el) return;
    if (!pushSupported()) {
      document.querySelectorAll("[data-push], [data-push-test]").forEach((b) => { b.disabled = true; });
      el.textContent = IOS ? "On iPhone or iPad, add Job Search HQ to your Home Screen first (Share → Add to Home Screen), then open it from there."
        : "This browser can't show notifications. Try Chrome, Edge, Firefox or Safari, and turn off private browsing.";
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    el.textContent = sub ? "Notifications are on for this device." : (Notification.permission === "denied" ? BLOCKED : "Notifications are off for this device.");
  }
  function pushProblem(err) {
    const name = err && err.name;
    if (name === "NotAllowedError") return BLOCKED;
    if (name === "AbortError" || name === "InvalidStateError")
      return "The browser's notification service didn't answer. In Brave, turn on \"Use Google services for push messaging\" in brave://settings/privacy. Otherwise try again, or use Chrome, Edge, Firefox or Safari.";
    return "Couldn't change notifications in this browser. Reload the page and try again.";
  }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest && e.target.closest("[data-push]");
    if (!btn) return;
    e.preventDefault();
    if (!pushSupported()) { pushState(); return; }
    try {
      const reg = await navigator.serviceWorker.ready;
      if (btn.dataset.push === "on") {
        const perm = await Notification.requestPermission();
        if (perm !== "granted") { toast(perm === "denied" ? BLOCKED : "Notifications weren't allowed. Choose Allow when the browser asks.", true); pushState(); return; }
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(btn.dataset.key) });
        await post("/push/subscribe", sub.toJSON());
      } else {
        const sub = await reg.pushManager.getSubscription();
        if (sub) { await post("/push/unsubscribe", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
        else toast("Notifications were already off on this device.", false);
      }
    } catch (err) { toast(pushProblem(err), true); }
    pushState();
  });
  ready(pushState);

  // ------------------------------------------------------- voice answers
  // Record in the browser and upload; the server transcribes and deletes the audio.
  const LIMIT = 300000, WARN = 30000;
  function micProblem(err) {
    const name = err && err.name;
    if (name === "NotAllowedError" || name === "SecurityError")
      return "The microphone is blocked for this site. Allow it in the browser's site settings (the icon left of the address; on iPhone, Settings → Safari → Microphone), then try again. Or type your answer below.";
    if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found. Plug one in, or type your answer below.";
    if (name === "NotReadableError") return "Another app is using the microphone. Close it and try again, or type your answer below.";
    return "Couldn't start recording in this browser. Type your answer below instead.";
  }
  function recorderParts(box) {
    return { start: box.querySelector("[data-rec-start]"), stop: box.querySelector("[data-rec-stop]"),
      status: box.querySelector("[data-rec-status]"), live: box.querySelector("[data-rec-live]") };
  }
  function recReset(box, message) {
    const p = recorderParts(box);
    clearInterval(box._tick); clearTimeout(box._timer); clearTimeout(box._warn);
    box._rec = null;
    p.start.disabled = false;
    p.stop.disabled = true;
    if (message) { p.status.textContent = message; p.live.textContent = message; }
    p.start.focus();
  }
  async function upload(box, rec, chunks) {
    const p = recorderParts(box);
    p.status.textContent = "Transcribing and grading…";
    p.live.textContent = "Stopped. Transcribing and grading. This can take a minute.";
    const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
    let r;
    try {
      r = await fetch(box.dataset.upload, { method: "POST", body: blob, headers: { "Content-Type": blob.type, "X-CSRF-Token": csrf() }, credentials: "same-origin" });
    } catch (_) {
      recReset(box, "Couldn't upload. Check your connection and record again.");
      return;
    }
    let t = {};
    try { t = JSON.parse(r.headers.get("HX-Trigger") || "{}"); } catch (_) { t = {}; }
    if (t["jobhq:error"] || !r.ok) {
      const msg = t["jobhq:error"] || (r.status >= 502 ? "The server isn't responding; it may be restarting. Record again in a minute." : "Couldn't upload (" + r.status + "). Record again.");
      recReset(box, msg);
      return;
    }
    remember(box, t["jobhq:toast"] || "Answer saved. Grading…", false, true);
    window.location.reload();
  }
  document.addEventListener("click", async (e) => {
    const start = e.target.closest && e.target.closest("[data-rec-start]");
    const stop = e.target.closest && e.target.closest("[data-rec-stop]");
    const box = (start || stop) && (start || stop).closest("[data-recorder]");
    if (!box) return;
    e.preventDefault();
    const p = recorderParts(box);
    if (start) {
      if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || !window.MediaRecorder) {
        recReset(box, "This browser can't record audio here. Type your answer below instead.");
        return;
      }
      p.start.disabled = true;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) { recReset(box, micProblem(err)); return; }
      try {
        const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
        const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
        const chunks = [];
        rec.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
        rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); upload(box, rec, chunks); };
        rec.onerror = () => { stream.getTracks().forEach((t) => t.stop()); recReset(box, "Recording stopped unexpectedly. Try again, or type your answer below."); };
        box._rec = rec;
        rec.start();
        const stopNow = () => { if (rec.state === "recording") { clearInterval(box._tick); p.stop.disabled = true; rec.stop(); } };
        box._timer = setTimeout(stopNow, LIMIT);
        box._warn = setTimeout(() => { p.live.textContent = "30 seconds left. Recording stops at 5 minutes."; }, LIMIT - WARN);
        p.stop.disabled = false;
        p.stop.focus();
        p.live.textContent = "Recording. Press Stop and grade when you're done.";
        const began = Date.now();
        box._tick = setInterval(() => {
          const s = Math.round((Date.now() - began) / 1000);
          p.status.textContent = "Recording… " + s + "s" + (LIMIT / 1000 - s <= WARN / 1000 ? " (stops at 5:00)" : "");
        }, 500);
      } catch (err) {
        stream.getTracks().forEach((t) => t.stop());
        recReset(box, micProblem(err));
      }
    } else if (stop && box._rec) {
      clearInterval(box._tick); clearTimeout(box._timer); clearTimeout(box._warn);
      stop.disabled = true;
      box._rec.stop();
    }
  });
})();
