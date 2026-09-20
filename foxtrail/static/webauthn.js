/* Minimaler WebAuthn-Client. Erwartet Optionen im JSON-Format von
   py_webauthn (base64url-Strings) und schickt die Antwort im selben Format zurueck.
   Texte kommen uebersetzt aus der Seite (window.WA_TEXT, siehe _webauthn.html).

   Dazu die automatische Abfrage auf der Anmeldeseite: Wer sich einmal mit einem Passkey
   angemeldet (oder einen registriert) hat, bekommt ihn auf diesem Geraet beim naechsten Mal
   von selbst angeboten. Gemerkt wird das nur im Browser (localStorage), nie auf dem Server. */
(function () {
  "use strict";
  var T = window.WA_TEXT || {};
  var SCHL_GERAET = "foxtrail-passkey-geraet";      // dieses Geraet hat schon einen Passkey benutzt
  var SCHL_AUTO = "foxtrail-passkey-autofrage";     // automatische Abfrage ein/aus (pro Geraet)

  /* Die gerade laufende Abfrage. Chromium-Browser lehnen eine zweite Abfrage sofort ab, solange
     noch eine offen ist - das saehe aus wie ein Abbruch. Automatische Abfragen treten deshalb
     zurueck, ein Klick hat Vorrang und bricht die laufende sauber ab. */
  var laufend = null;
  var selbst_abgebrochen = false;                   // dann nicht mehr von selbst nachfragen
  var versteckt_seit = 0;                           // fuer "tote" Abfragen nach einem Seitenwechsel

  function b64uToBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) { s += "="; }
    var bin = atob(s), buf = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { buf[i] = bin.charCodeAt(i); }
    return buf.buffer;
  }
  function bufToB64u(buf) {
    var bytes = new Uint8Array(buf), s = "";
    for (var i = 0; i < bytes.length; i++) { s += String.fromCharCode(bytes[i]); }
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function say(el, txt, art) {
    if (el) { el.textContent = txt; el.className = "pkmsg" + (art ? " " + art : ""); }
  }
  function leeren(el) { say(el, ""); }

  function lesen(schluessel) {
    try { return localStorage.getItem(schluessel); } catch (e) { return null; }
  }
  function schreiben(schluessel, wert) {
    try { localStorage.setItem(schluessel, wert); } catch (e) { /* gesperrt (Privatmodus) */ }
  }
  function merken() { schreiben(SCHL_GERAET, "1"); }
  function bekannt() { return lesen(SCHL_GERAET) === "1"; }
  function autoAn() { return lesen(SCHL_AUTO) !== "0"; }

  /* Ohne sicheren Kontext blendet der Browser PublicKeyCredential ganz aus - das saehe sonst
     nach einem zu alten Browser aus, obwohl nur HTTPS fehlt. */
  function hindernis() {
    if (!window.isSecureContext) { return T.unsicher || "HTTPS"; }
    if (!window.PublicKeyCredential) { return T.kein || "?"; }
    return null;
  }

  /* Der Browser wirft englische DOMException-Texte; der haeufigste Fall ist der Abbruch durch
     den Benutzer. Unterschieden wird ueber err.name, der Text weicht je nach Browser ab. */
  function fehlertext(e) {
    switch (e && e.name) {
    case "NotAllowedError":
    case "AbortError": return T.abbruch || "Abbruch";
    case "InvalidStateError": return T.schon || (e.message || "");
    case "SecurityError": return T.adresse || (e.message || "");
    case "NotSupportedError": return T.kein || (e.message || "");
    }
    return (T.fehler || "Fehler:") + " " + ((e && e.message) || "");
  }

  async function getJSON(url) {
    var r = await fetch(url, { credentials: "same-origin" });
    var j = await r.json().catch(function () { return {}; });
    if (!r.ok || j.error) { throw new Error(j.error || ("HTTP " + r.status)); }
    return j;
  }
  async function postJSON(url, obj) {
    var r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj)
    });
    var j = await r.json().catch(function () { return {}; });
    if (!r.ok || j.error) { throw new Error(j.error || ("HTTP " + r.status)); }
    return j;
  }

  window.webauthnRegister = async function (optsUrl, verifyUrl, msgEl) {
    try {
      var halt = hindernis();
      if (halt) { throw new Error(halt); }
      say(msgEl, T.warten || "…");
      var opt = await getJSON(optsUrl);
      opt.challenge = b64uToBuf(opt.challenge);
      opt.user.id = b64uToBuf(opt.user.id);
      (opt.excludeCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      var cred = await navigator.credentials.create({ publicKey: opt });
      var j = await postJSON(verifyUrl, {
        id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
        clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
        response: {
          clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          attestationObject: bufToB64u(cred.response.attestationObject),
          transports: cred.response.getTransports ? cred.response.getTransports() : []
        }
      });
      merken();
      say(msgEl, T.gespeichert || "ok");
      location.href = j.weiter || "/";
    } catch (e) { say(msgEl, fehlertext(e), "err"); }
  };

  /* auto = true: die Abfrage lief beim Laden der Seite von selbst an, nicht auf Klick. */
  window.webauthnLogin = async function (optsUrl, verifyUrl, msgEl, auto) {
    if (auto && laufend) { return; }                  // automatisch funkt nie dazwischen
    if (laufend) {                                    // der Klick hat Vorrang
      laufend.abgeloest = true;
      try { laufend.abort(); } catch (e) { /* egal */ }
      laufend = null;
    }
    var abbruch = new AbortController();
    abbruch.start = Date.now();
    laufend = abbruch;
    var hatte_fokus = true;
    if (!auto) { leeren(msgEl); }
    try {
      var halt = hindernis();
      if (halt) { throw new Error(halt); }
      var opt = await getJSON(optsUrl);
      opt.challenge = b64uToBuf(opt.challenge);
      (opt.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      say(msgEl, T.warten || "…");
      // Ohne Fokus lehnt Chromium die Abfrage mit demselben Fehler ab wie ein Abbruch.
      hatte_fokus = document.hasFocus ? document.hasFocus() : true;
      var cred = await navigator.credentials.get({ publicKey: opt, signal: abbruch.signal });
      var j = await postJSON(verifyUrl, {
        id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
        clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
        response: {
          clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          authenticatorData: bufToB64u(cred.response.authenticatorData),
          signature: bufToB64u(cred.response.signature),
          userHandle: cred.response.userHandle ? bufToB64u(cred.response.userHandle) : null
        }
      });
      merken();
      say(msgEl, T.angemeldet || "ok");
      location.href = j.weiter || "/";
    } catch (e) {
      var weg = e && (e.name === "NotAllowedError" || e.name === "AbortError");
      if (abbruch.abgeloest) { return; }              // vom Klick abgeloest: keine Meldung
      if (auto && weg && !hatte_fokus) { leeren(msgEl); return; }   // nur kein Fokus, kein Abbruch
      if (auto && weg) {
        // Wer die Abfrage wegklickt, wollte sie nicht: fuer dieses Geraet abschalten.
        schreiben(SCHL_AUTO, "0");
        var box = document.getElementById("pk-autofrage-box");
        if (box) { box.checked = false; }
        say(msgEl, T.autoAus || "");
        return;
      }
      if (weg) { selbst_abgebrochen = true; }
      say(msgEl, fehlertext(e), weg ? "" : "err");
    } finally {
      if (laufend === abbruch) { laufend = null; }
    }
  };

  /* Kaestchen "Beim Öffnen automatisch fragen" - nur auf Geraeten, die schon einen Passkey
     benutzt haben; sonst waere es eine Einstellung fuer etwas, das hier nie passiert. */
  window.passkeyAutoFrage = function (wrap, box) {
    if (!wrap || !box || hindernis() || !bekannt()) { return; }
    wrap.hidden = false;
    box.checked = autoAn();
    box.addEventListener("change", function () { schreiben(SCHL_AUTO, box.checked ? "1" : "0"); });
  };

  /* "?abgemeldet=1" (vom Abmelden) unterdrueckt die Abfrage dieses eine Mal - sonst kaeme man
     nie von der Anmeldeseite weg. Der Parameter wird gleich aus der Adresse entfernt, sonst
     schleppt ihn ein Neuladen oder ein wiederhergestellter Tab fuer immer mit. */
  function abgemeldet() {
    var p = new URLSearchParams(location.search);
    if (!p.has("abgemeldet")) { return false; }
    p.delete("abgemeldet");
    try {
      var rest = p.toString();
      history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash);
    } catch (e) { /* dann bleibt der Parameter eben stehen */ }
    return true;
  }

  window.passkeyAutoLogin = function (optsUrl, verifyUrl, msgEl) {
    var eben_abgemeldet = abgemeldet();              // immer zuerst: Adresse aufraeumen
    if (eben_abgemeldet || selbst_abgebrochen || hindernis() || !bekannt() || !autoAn()) { return; }
    window.webauthnLogin(optsUrl, verifyUrl, msgEl, true);
  };

  /* Oeffnet man die Seite ueber eine Verknuepfung auf dem Startbildschirm, holt der Browser oft
     nur den offenen Tab nach vorne, statt neu zu laden - dann liefe der Startcode nie wieder.
     Deshalb zusaetzlich fragen, wenn die Seite wieder sichtbar wird. */
  window.passkeyBeiRueckkehr = function (optsUrl, verifyUrl, msgEl) {
    var geplant = false;
    function versuchen() {
      if (geplant) { return; }
      geplant = true;
      setTimeout(function () {
        geplant = false;
        if (document.visibilityState !== "visible") { return; }
        // Eine Abfrage von vor dem Verstecken gilt als tot: Android beendet ihr Promise oft nie,
        // die Sperre bliebe sonst fuer immer stehen.
        if (laufend && laufend.start < versteckt_seit) {
          laufend.abgeloest = true;
          try { laufend.abort(); } catch (e) { /* egal */ }
          laufend = null;
          leeren(msgEl);
        }
        window.passkeyAutoLogin(optsUrl, verifyUrl, msgEl);
      }, 200);
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") { versuchen(); } else { versteckt_seit = Date.now(); }
    });
    window.addEventListener("focus", versuchen);
    window.addEventListener("pageshow", function (e) { if (e && e.persisted) { versuchen(); } });
  };
})();
