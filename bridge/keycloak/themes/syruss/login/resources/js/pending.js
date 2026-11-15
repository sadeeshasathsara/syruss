// Every form on the Syruss sign-in pages: once sent, its button is disabled and says what is happening, and a second
// click does nothing (no double sign-ins, no second "forgot password" email). The button is disabled a moment after
// the browser has gathered the form, so the pressed button's name still reaches Keycloak (some pages have two, such as
// "Submit" and "Cancel"). Coming back with the browser's Back button finds the form usable again.
(function () {
  "use strict";
  var LABELS = {
    "kc-form-login": "Signing in…",
    "kc-register-form": "Creating your account…",
    "kc-otp-login-form": "Checking the code…",
    "kc-reset-password-form": "Sending the email…",
    "kc-totp-settings-form": "Setting it up…",
    "kc-passwd-update-form": "Saving your password…",
    "kc-update-profile-form": "Saving…",
    "kc-update-email-form": "Saving…",
    "kc-idp-review-profile-form": "Saving…",
    "kc-select-credential-form": "Continuing…",
    "kc-x509-login-info": "Signing in…"
  };

  function setLabel(button, text) {
    if (button.tagName === "INPUT") {
      button.dataset.syrussLabel = button.value;
      button.value = text;
    } else {
      button.dataset.syrussLabel = button.innerHTML;
      button.textContent = text;
    }
  }

  function restore(form) {
    delete form.dataset.syrussSent;
    form.removeAttribute("aria-busy");
    form.querySelectorAll("[data-syruss-label]").forEach(function (button) {
      if (button.tagName === "INPUT") button.value = button.dataset.syrussLabel;
      else button.innerHTML = button.dataset.syrussLabel;
      delete button.dataset.syrussLabel;
      button.disabled = false;
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
    if (form.dataset.syrussSent) { event.preventDefault(); return; }   // already on its way
    form.dataset.syrussSent = "1";
    form.setAttribute("aria-busy", "true");
    var pressed = event.submitter || form.querySelector("[type=submit]");
    var cancelling = pressed && /cancel/i.test(pressed.name || pressed.id || "");
    var label = cancelling ? "Cancelling…" : LABELS[form.id] || "Please wait…";
    // The sign-in form with no email field is confirming who is there (see login.ftl), not signing in
    if (form.id === "kc-form-login" && !form.querySelector("[name=username]")) label = "Checking…";
    setTimeout(function () {
      form.querySelectorAll("button[type=submit], input[type=submit], button:not([type])").forEach(function (button) {
        if (button === pressed) setLabel(button, label);
        button.disabled = true;
      });
    }, 0);
  }, false);

  // Back/forward cache: a page restored after leaving it shows its forms ready again
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) document.querySelectorAll("form[data-syruss-sent]").forEach(restore);
  });
})();
