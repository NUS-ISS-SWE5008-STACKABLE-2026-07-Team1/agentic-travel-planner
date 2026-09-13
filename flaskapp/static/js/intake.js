/* Conversational trip intake.
 *
 * Every message is read by the orchestrator and merged into the confirmed trip
 * brief. Specialists start only after deterministic gap checks pass.
 */
const intentForm = document.querySelector("#intent-form");

if (intentForm) {
  const isIntakeChat = intentForm.dataset.autoStart === "true";
  const conversation = document.querySelector("#intent-conversation");
  const promptInput = document.querySelector("#intent-prompt");
  const submitButton = intentForm.querySelector("button[type='submit']");
  const spinner = submitButton.querySelector(".spinner-border");
  const countries = JSON.parse(document.querySelector("#country-options").textContent || "[]");
  const genders = [
    ["female", "Female"], ["male", "Male"],
    ["non_binary", "Non-binary"], ["prefer_not_to_say", "Prefer not to say"]
  ];
  // The three dispatch choices, in the order offered. "Flights and hotel" is
  // first because it is the default and the common case: a traveller who does
  // not care picks the first option and still gets every specialist.
  const scopes = [
    ["both", "Flights and hotel"], ["flights", "Flights only"],
    // "and transport": this agent books airport transfers and local transport
    // as well as accommodation, and "Hotel only" understates what is included.
    ["hotel", "Hotel and transport only"]
  ];
  let intakeState = null;
  let sessionHandoff = false;

  const csrf = () => document.querySelector("meta[name='csrf-token']").content;
  const endSession = () => {
    const requestId = intakeState?.request_id;
    if (!requestId) return Promise.resolve();
    return fetch(`${intentForm.dataset.sessionEndBase}/${requestId}/session/end`, {
      method: "POST", headers: {"X-CSRFToken": csrf()}, keepalive: true
    }).catch(() => {});
  };
  const scrollConversationToLatest = () => {
    conversation.scrollTop = conversation.scrollHeight;
  };

  const post = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(body)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Something went wrong. Please try again.");
    return payload;
  };

  const bubble = (className, text) => {
    const element = document.createElement("div");
    element.className = `intake-bubble ${className}`;
    element.textContent = text;
    conversation.append(element);
    scrollConversationToLatest();
    return element;
  };

  const showError = (message) => {
    const alert = document.createElement("div");
    alert.className = "alert alert-danger mt-3";
    alert.setAttribute("role", "alert");
    alert.textContent = message;
    conversation.append(alert);
  };

  const renderProgress = (state) => {
    conversation.querySelector(".intake-progress")?.remove();
    if (state.complete) return;
    const progress = document.createElement("div");
    progress.className = "intake-progress";
    const title = document.createElement("div");
    title.className = "intake-progress-title";
    title.textContent = `${state.missing.length} detail${state.missing.length === 1 ? "" : "s"} still needed`;
    progress.append(title);
    const chips = document.createElement("div");
    chips.className = "intake-gap-chips";
    for (const field of state.missing) {
      const chip = document.createElement("span");
      chip.textContent = field.traveller_index === null
        ? field.label
        : `Traveller ${field.traveller_index + 1}: ${field.label}`;
      chips.append(chip);
    }
    progress.append(chips);
    conversation.append(progress);
    scrollConversationToLatest();
  };

  /* One input per gap, typed by the server's `input` kind. */
  const renderField = (field) => {
    const wrapper = document.createElement("div");
    wrapper.className = "col-md-6";
    const id = `gap-${field.name}-${field.traveller_index ?? "x"}`;
    const label = document.createElement("label");
    label.className = "form-label";
    label.setAttribute("for", id);
    label.textContent = field.traveller_index === null
      ? field.label
      : `Traveller ${field.traveller_index + 1} · ${field.label}`;
    wrapper.append(label);

    let input;
    if (field.input === "country" || field.input === "gender" || field.input === "scope") {
      input = document.createElement("select");
      input.className = "form-select";
      const options = field.input === "country"
        ? countries.map((country) => [country, country])
        : field.input === "scope" ? scopes : genders;
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = field.input === "country" ? "Select a country" : "Select";
      blank.disabled = true;
      blank.selected = true;
      input.append(blank);
      for (const [value, text] of options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      }
    } else {
      input = document.createElement("input");
      input.className = "form-control";
      input.type = {date: "date", number: "number", text: "text"}[field.input] || "text";
      if (field.input === "number") input.min = "0";
    }
    input.id = id;
    input.dataset.key = field.traveller_index === null
      ? field.name
      : `${field.name}.${field.traveller_index}`;
    /* Accessibility needs are answerable as "none", so blank is a real answer
       there and the field must not be required. */
    input.required = field.name !== "traveller_accessibility_needs";
    wrapper.append(input);

    if (field.hint) {
      const hint = document.createElement("div");
      hint.className = "form-text";
      hint.textContent = field.hint;
      wrapper.append(hint);
    }
    return wrapper;
  };

  const renderCard = (state) => {
    const card = document.createElement("form");
    card.id = "intake-card-form";
    card.className = "intake-card card border-0 shadow-sm mt-3";
    const body = document.createElement("div");
    body.className = "card-body p-4";
    const heading = document.createElement("p");
    heading.className = "fw-semibold mb-3";
    heading.textContent = "A few more details and I'll brief the team:";
    body.append(heading);

    const grid = document.createElement("div");
    grid.className = "row g-3";
    for (const field of state.missing) grid.append(renderField(field));
    body.append(grid);

    const actions = document.createElement("div");
    actions.className = "d-flex justify-content-end mt-4";
    const submit = document.createElement("button");
    submit.className = "btn btn-primary px-4";
    submit.type = "submit";
    submit.textContent = "Continue";
    actions.append(submit);
    body.append(actions);
    card.append(body);
    conversation.append(card);
    scrollConversationToLatest();
    card.querySelector("input, select")?.focus();

    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!card.reportValidity()) return;
      const answers = {};
      for (const input of card.querySelectorAll("[data-key]")) {
        /* Presence is what marks a field answered. Accessibility needs are sent
           even when blank, which is how "no needs" reaches the server. */
        if (input.value !== "" || input.dataset.key.startsWith("traveller_accessibility_needs")) {
          answers[input.dataset.key] = input.value;
        }
      }
      submit.disabled = true;
      conversation.querySelectorAll(".alert").forEach((alert) => alert.remove());
      try {
        const next = await post(intentForm.dataset.resolveEndpoint, {
          extracted: state.extracted, answers
        });
        /* The card is handed to advance() and removed only once the next step
           has actually succeeded, so a rejected request never costs the
           traveller the answers they already typed. */
        await advance(next, card);
      } catch (error) {
        submit.disabled = false;
        showError(error.message);
      }
    });
  };

  /* Complete means we have a TravelRequest; hand it to the unchanged planning
     endpoint and follow it to the chat page. */
  const advance = async (state, previousCard = null) => {
    if (!state.complete) {
      intakeState = state;
      renderProgress(state);
      promptInput.value = "";
      promptInput.placeholder = "Reply with the missing details…";
      promptInput.focus();
      return;
    }
    state.request._request_id = state.request_id;
    const job = await post(intentForm.dataset.planEndpoint, state.request);
    previousCard?.remove();
    bubble("intake-bubble-assistant", "Thanks — briefing the specialist agents now.");
    sessionStorage.setItem("atlas-plan-payload", JSON.stringify(state.request));
    sessionHandoff = true;
    window.location.assign(job.chat_url);
  };

  const submitPrompt = async (prompt) => {
    submitButton.disabled = true;
    spinner.classList.remove("d-none");
    try {
      bubble("intake-bubble-user", prompt);
      const state = await post(intentForm.dataset.intentEndpoint, {
        prompt,
        extracted: intakeState?.extracted || {},
        request_id: intakeState?.request_id || null
      });
      if (state.question) bubble("intake-bubble-assistant", state.question);
      await advance(state);
    } catch (error) {
      showError(error.message);
    } finally {
      submitButton.disabled = false;
      spinner.classList.add("d-none");
    }
  };

  intentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!intentForm.reportValidity()) return;
    const prompt = promptInput.value.trim();
    if (!isIntakeChat) {
      sessionStorage.setItem("atlas-intake-prompt", prompt);
      window.location.assign(intentForm.dataset.chatUrl);
      return;
    }
    await submitPrompt(prompt);
  });

  if (isIntakeChat) {
    const initialPrompt = sessionStorage.getItem("atlas-intake-prompt");
    if (initialPrompt) {
      sessionStorage.removeItem("atlas-intake-prompt");
      promptInput.value = initialPrompt;
      submitPrompt(initialPrompt);
    } else {
      bubble("intake-bubble-assistant", "Tell me what you have in mind. I’ll make sure the trip brief is complete before involving the specialist agents.");
      promptInput.focus();
    }
  }
  // Keep Home as a normal link. The pagehide handler below sends the session
  // cleanup request with `keepalive`, so navigation must not wait for that
  // request to finish (or a slow response can leave the user stuck here).
  window.addEventListener("pagehide", () => {
    if (!sessionHandoff) endSession();
  });
}
