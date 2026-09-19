/* Minimaler WebAuthn-Client. Erwartet Optionen im JSON-Format von
   py_webauthn (base64url-Strings) und schickt die Antwort im selben Format zurueck.
   Texte kommen uebersetzt aus der Seite: window.WA_TEXT = {warten, gespeichert, angemeldet, fehler}. */
(function () {
  "use strict";
  var T = window.WA_TEXT || {};

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
  function say(el, txt, err) {
    if (el) { el.textContent = txt; el.className = "pkmsg" + (err ? " err" : ""); }
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
      say(msgEl, T.gespeichert || "ok");
      location.href = j.weiter || "/";
    } catch (e) { say(msgEl, (T.fehler || "Error:") + " " + e.message, true); }
  };

  window.webauthnLogin = async function (optsUrl, verifyUrl, msgEl) {
    try {
      say(msgEl, T.warten || "…");
      var opt = await getJSON(optsUrl);
      opt.challenge = b64uToBuf(opt.challenge);
      (opt.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      var cred = await navigator.credentials.get({ publicKey: opt });
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
      say(msgEl, T.angemeldet || "ok");
      location.href = j.weiter || "/";
    } catch (e) { say(msgEl, (T.fehler || "Error:") + " " + e.message, true); }
  };
})();
