"use strict";

// Live feedback for new-password fields on /register and /forgot-password.
//
// The server decides: `strong_password` in flaskapp/forms.py. This mirrors it
// for two reasons. A traveller sees each rule tick off as it is met, and a red
// error left over from a failed submit clears once the field is actually
// fixed. Before this, Bootstrap's server-rendered error stayed on screen until
// the next submit, so a valid password still looked rejected.
//
// Keep RULES in step with `strong_password`. tests/e2e/test_password_rules.py
// compares this verdict with the server's for the same inputs.
(() => {
  const RULES = {
    // Python's len() counts code points and so does spreading a string, so an
    // emoji counts as one character in both places. `.length` would count two.
    length: (value) => [...value].length >= 12,
    lower: (value) => /[a-z]/.test(value),
    upper: (value) => /[A-Z]/.test(value),
    // Python's `\d` matches any Unicode decimal digit, not only 0-9.
    digit: (value) => /\p{Nd}/u.test(value),
    special: (value) => /[^A-Za-z0-9]/.test(value),
  };
  const meetsAll = (value) => Object.values(RULES).every((rule) => rule(value));

  const clearError = (field) => {
    if (field) field.classList.remove("is-invalid");
  };

  document.querySelectorAll("[data-password-rules-for]").forEach((list) => {
    const form = list.closest("form");
    const password = form.querySelector(`#${list.dataset.passwordRulesFor}`);
    const confirm = form.querySelector("#confirm_password");
    if (!password) return;

    const update = () => {
      const value = password.value;
      list.querySelectorAll("[data-rule]").forEach((item) => {
        const met = RULES[item.dataset.rule](value);
        item.classList.toggle("is-met", met);
        // Read out by screen readers alongside the rule; hidden on screen.
        item.querySelector(".visually-hidden").textContent = met ? " (done)" : " (not yet)";
      });
      if (meetsAll(value)) clearError(password);
      if (confirm && confirm.value && confirm.value === value) clearError(confirm);
    };

    password.addEventListener("input", update);
    if (confirm) confirm.addEventListener("input", update);
    list.classList.add("is-live");
    update();
  });

  // Every other field that came back with a server error: clear it as soon as
  // the traveller edits it. The server checks again on the next submit, so an
  // edit that is still wrong is reported then, next to the field.
  document.querySelectorAll("form .is-invalid").forEach((field) => {
    if (field.matches("#password, #confirm_password")) return;
    const clear = () => clearError(field);
    field.addEventListener("input", clear, { once: true });
    field.addEventListener("change", clear, { once: true });
  });
})();
