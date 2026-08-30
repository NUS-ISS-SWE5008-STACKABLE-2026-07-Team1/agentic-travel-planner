"use strict";

const pageLoader = document.createElement("div");
pageLoader.className = "ai-page-loader";
pageLoader.setAttribute("role", "status");
pageLoader.setAttribute("aria-live", "polite");
pageLoader.setAttribute("aria-hidden", "true");
pageLoader.innerHTML = `
  <div class="ai-loader-core" aria-hidden="true">
    <span class="ai-loader-orbit orbit-one"></span>
    <span class="ai-loader-orbit orbit-two"></span>
    <span class="ai-loader-spark">✦</span>
  </div>
  <strong>AI workspace loading</strong>
  <span class="ai-loader-caption">Preparing your travel experience…</span>`;
document.body.append(pageLoader);

const showPageLoader = () => {
  pageLoader.classList.add("is-visible");
  pageLoader.setAttribute("aria-hidden", "false");
};
const hidePageLoader = () => {
  pageLoader.classList.remove("is-visible");
  pageLoader.setAttribute("aria-hidden", "true");
};

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href]");
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
      event.shiftKey || event.altKey || link.target === "_blank" || link.hasAttribute("download")) return;
  const destination = new URL(link.href, window.location.href);
  if (destination.origin === window.location.origin &&
      !(destination.pathname === window.location.pathname && destination.hash)) {
    showPageLoader();
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  const asynchronousForms = new Set([
    "travel-plan-form", "refinement-form", "chat-refinement-form",
    "negative-feedback-form", "admin-registration-form",
    "intent-form", "intake-card-form"
  ]);
  if (!asynchronousForms.has(form.id) && form.checkValidity()) {
    showPageLoader();
  }
}, true);
window.addEventListener("beforeunload", showPageLoader);
window.addEventListener("pageshow", hidePageLoader);

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = button.closest(".form-floating").querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    button.setAttribute("aria-pressed", String(show));
  });
});

// Route label for a stored request: city when one was chosen, country
// otherwise. Requests predating city intake have no city fields at all, so the
// fallback is the normal path for them, not an error case.
const routeEnd = (place, city) => (city ? `${city}, ${place}` : place || "-");
const routeLabel = (request) =>
  `${routeEnd(request.origin, request.origin_city)} → ${routeEnd(request.destination, request.destination_city)}`;

const planner = document.querySelector("#travel-plan-form");
if (planner) {
  const originCountry = planner.querySelector("#origin");
  const destinationCountry = planner.querySelector("#destination");
  const travelersInput = planner.querySelector("#travellers");
  const travelerDetails = planner.querySelector("#traveler-details");
  const addTravelerButton = planner.querySelector("#add-traveler");
  const result = document.querySelector("#plan-result");
  const refinementPanel = document.querySelector("#refinement-panel");
  const refinementForm = document.querySelector("#refinement-form");
  const refinementHistory = document.querySelector("#refinement-history");
  const refinementNotes = [];
  const departureDate = planner.querySelector("#departure_date");
  const returnDate = planner.querySelector("#return_date");
  let latestPayload = null;
  // Cities are embedded in the page as a JSON data block, so changing country
  // repopulates instantly with no request to fail or wait on.
  const cityOptionsNode = document.querySelector("#city-options");
  const cityOptions = cityOptionsNode ? JSON.parse(cityOptionsNode.textContent) : {};

  const populateCities = (countrySelect, citySelect) => {
    const previous = citySelect.value;
    const cities = cityOptions[countrySelect.value] || [];
    citySelect.replaceChildren();

    if (!countrySelect.value) {
      citySelect.append(new Option("Select a country first", "", true, true));
      citySelect.disabled = true;
    } else if (cities.length === 0) {
      // A country we have no mapped cities for. Say so rather than offering an
      // empty dropdown, and leave the field non-blocking so the trip can still
      // be submitted at country granularity.
      citySelect.append(new Option("No cities available for this country", "", true, true));
      citySelect.disabled = true;
    } else {
      citySelect.append(new Option("Select a city", "", true, true));
      for (const city of cities) {
        // `label` carries the airport codes ("Tokyo (NRT/HND)"); the posted
        // value is the plain name, which is what the resolver looks up.
        citySelect.append(new Option(city.label, city.name));
      }
      citySelect.disabled = false;
      // Keep the choice if the same city exists under the new country, so a
      // stray re-selection of the same country is not punished.
      if (previous && cities.some((city) => city.name === previous)) {
        citySelect.value = previous;
      }
    }
    // `required` only when there is something to choose, or the browser blocks
    // submission on a field the traveller cannot fill.
    citySelect.required = !citySelect.disabled;
  };

  const cityPairs = [...planner.querySelectorAll("[data-country-for]")].map((citySelect) => {
    const countrySelect = planner.querySelector(`#${citySelect.dataset.countryFor}`);
    countrySelect.addEventListener("change", () => populateCities(countrySelect, citySelect));
    return [countrySelect, citySelect];
  });
  // Run once at load: the departure country may be prefilled from the profile.
  cityPairs.forEach(([countrySelect, citySelect]) => populateCities(countrySelect, citySelect));

  departureDate.addEventListener("change", () => {
    if (!departureDate.value) return;
    returnDate.min = departureDate.value;
    if (!returnDate.value || returnDate.value < departureDate.value) {
      returnDate.value = departureDate.value;
    }
    returnDate.focus({preventScroll: true});
  });

  const renderTravelerFields = () => {
    const previousAges = [...travelerDetails.querySelectorAll("[name='traveller_age']")].map((field) => field.value);
    const previousGenders = [...travelerDetails.querySelectorAll("[name='traveller_gender']")].map((field) => field.value);
    const previousPreferences = [...travelerDetails.querySelectorAll("[name='traveller_preference']")].map((field) => field.value);
    const count = Math.min(20, Math.max(1, Number(travelersInput.value) || 1));
    travelerDetails.replaceChildren();
    for (let index = 0; index < count; index += 1) {
      const wrapper = document.createElement("div");
      wrapper.className = "col-md-6 col-lg-4";
      wrapper.innerHTML = `
        <div class="traveler-card">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <strong>Traveler ${index + 1}</strong>
            <button class="btn btn-link btn-sm text-danger p-0 remove-traveler" type="button" data-index="${index}" ${count === 1 ? "disabled" : ""}>Remove</button>
          </div>
          <div class="row g-2">
            <div class="col-5">
              <label class="form-label" for="traveller-age-${index}">Age</label>
              <input class="form-control" id="traveller-age-${index}" name="traveller_age" type="number" min="0" max="120" required>
            </div>
            <div class="col-7">
              <label class="form-label" for="traveller-gender-${index}">Gender</label>
              <select class="form-select" id="traveller-gender-${index}" name="traveller_gender" required>
                <option value="">Select</option><option value="female">Female</option>
                <option value="male">Male</option><option value="non_binary">Non-binary</option>
                <option value="prefer_not_to_say">Prefer not to say</option>
              </select>
            </div>
            <div class="col-12 mt-3">
              <label class="form-label" for="traveller-preference-${index}">Preferences and accessibility needs <span class="text-body-secondary fw-normal">(optional)</span></label>
              <textarea class="form-control" id="traveller-preference-${index}" name="traveller_preference" rows="3" maxlength="500" placeholder="Window seat, vegetarian meals, quiet room, step-free access"></textarea>
              <div class="form-text">Add any preferences or accessibility requirements specific to this traveler.</div>
            </div>
          </div>
        </div>`;
      wrapper.querySelector("[name='traveller_age']").value = previousAges[index] || "";
      wrapper.querySelector("[name='traveller_gender']").value = previousGenders[index] || "";
      wrapper.querySelector("[name='traveller_preference']").value = previousPreferences[index] || "";
      travelerDetails.append(wrapper);
    }
    addTravelerButton.disabled = count >= 20;
  };

  const buildPayload = () => {
    const data = new FormData(planner);
    const commaList = (name) => String(data.get(name) || "").split(",").map((item) => item.trim()).filter(Boolean);
    const travelerPreferences = data.getAll("traveller_preference")
      .map((value) => String(value).trim());
    const preferences = [
      ...commaList("preferences"),
      ...travelerPreferences.flatMap((preference, index) =>
        preference ? [`Traveler ${index + 1}: ${preference}`] : []
      )
    ];
    return {
      origin: data.get("origin"), destination: data.get("destination"),
      // Omitted rather than sent empty when a country has no mapped cities —
      // the API rejects a blank string but accepts an absent city, which the
      // flight adapter then resolves to the country's main gateway.
      ...(data.get("origin_city") ? {origin_city: data.get("origin_city")} : {}),
      ...(data.get("destination_city") ? {destination_city: data.get("destination_city")} : {}),
      departure_date: data.get("departure_date"), return_date: data.get("return_date"),
      travellers: Number(data.get("travellers")), currency: "SGD",
      traveller_ages: data.getAll("traveller_age").map(Number),
      traveller_genders: data.getAll("traveller_gender"),
      traveller_accessibility_needs: travelerPreferences.map(() => []),
      budget: Number(data.get("budget")),
      preferences, accessibility_needs: [],
      refinement_notes: [...refinementNotes]
    };
  };

  const requestPlan = async (payload, button, spinner = null) => {
    button.disabled = true;
    if (spinner) spinner.classList.remove("d-none");
    result.replaceChildren();
    try {
      const response = await fetch(planner.dataset.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("meta[name='csrf-token']").content
        },
        body: JSON.stringify(payload)
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Unable to build your travel plan.");
      latestPayload = payload;
      sessionStorage.setItem("atlas-plan-payload", JSON.stringify(payload));
      window.location.assign(body.chat_url);
      return true;
    } catch (error) {
      const alert = document.createElement("div");
      alert.className = "alert alert-danger";
      alert.setAttribute("role", "alert");
      alert.textContent = error.message;
      result.append(alert);
      return false;
    } finally {
      button.disabled = false;
      if (spinner) spinner.classList.add("d-none");
    }
  };

  travelersInput.addEventListener("input", renderTravelerFields);
  addTravelerButton.addEventListener("click", () => {
    const count = Math.min(20, (Number(travelersInput.value) || 1) + 1);
    travelersInput.value = String(count);
    renderTravelerFields();
  });
  travelerDetails.addEventListener("click", (event) => {
    const removeButton = event.target.closest(".remove-traveler");
    if (!removeButton) return;
    const index = Number(removeButton.dataset.index);
    const ageFields = [...travelerDetails.querySelectorAll("[name='traveller_age']")];
    const genderFields = [...travelerDetails.querySelectorAll("[name='traveller_gender']")];
    const preferenceFields = [...travelerDetails.querySelectorAll("[name='traveller_preference']")];
    ageFields[index].removeAttribute("name");
    genderFields[index].removeAttribute("name");
    preferenceFields[index].removeAttribute("name");
    removeButton.closest(".col-md-6").remove();
    travelersInput.value = String(Math.max(1, Number(travelersInput.value) - 1));
    renderTravelerFields();
  });
  renderTravelerFields();

  // "Go back" on the chat page returns here with the trip intact. buildPayload()
  // flattens per-traveller preferences into "Traveler N: ..." entries in one
  // list, so restoring splits them back out by that same prefix — the two
  // functions are inverses and must stay that way.
  //
  // Defensive throughout: a stored payload is data from a previous session and
  // may predate any field added since. Anything missing or unparseable leaves
  // the form exactly as it loaded rather than half-filled.
  const restoreStoredTrip = () => {
    let payload;
    try {
      payload = JSON.parse(sessionStorage.getItem("atlas-plan-payload") || "null");
    } catch (error) {
      return;
    }
    if (!payload) return;
    const setValue = (name, value) => {
      const field = planner.querySelector(`[name='${name}']`);
      if (field && value !== undefined && value !== null && value !== "") field.value = value;
    };
    setValue("origin", payload.origin);
    setValue("destination", payload.destination);
    // Cities are populated from the country, so the country must be set first.
    cityPairs.forEach(([countrySelect, citySelect]) => populateCities(countrySelect, citySelect));
    setValue("origin_city", payload.origin_city);
    setValue("destination_city", payload.destination_city);
    setValue("departure_date", payload.departure_date);
    if (payload.departure_date) returnDate.min = payload.departure_date;
    setValue("return_date", payload.return_date);
    setValue("budget", payload.budget);

    const perTraveller = /^Traveler (\d+): (.*)$/;
    const general = [];
    const byTraveller = {};
    (payload.preferences || []).forEach((entry) => {
      const match = perTraveller.exec(String(entry));
      if (match) {
        const index = Number(match[1]) - 1;
        byTraveller[index] = byTraveller[index] ? `${byTraveller[index]}, ${match[2]}` : match[2];
      } else {
        general.push(entry);
      }
    });
    setValue("preferences", general.join(", "));

    if (payload.travellers) {
      travelersInput.value = String(payload.travellers);
      renderTravelerFields();
      const ages = travelerDetails.querySelectorAll("[name='traveller_age']");
      const genders = travelerDetails.querySelectorAll("[name='traveller_gender']");
      const notes = travelerDetails.querySelectorAll("[name='traveller_preference']");
      ages.forEach((field, index) => { field.value = (payload.traveller_ages || [])[index] ?? ""; });
      genders.forEach((field, index) => { field.value = (payload.traveller_genders || [])[index] || ""; });
      notes.forEach((field, index) => { field.value = byTraveller[index] || ""; });
    }
  };

  restoreStoredTrip();

  planner.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!planner.reportValidity()) return;
    refinementNotes.length = 0;
    refinementHistory.replaceChildren();
    const button = planner.querySelector("button[type='submit']");
    await requestPlan(buildPayload(), button, button.querySelector(".spinner-border"));
  });

  refinementForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!refinementForm.reportValidity() || !latestPayload) return;
    const input = refinementForm.querySelector("#refinement-message");
    const message = input.value.trim();
    refinementNotes.push(message);
    const entry = document.createElement("div");
    entry.className = "refinement-message mb-2";
    entry.textContent = `You: ${message}`;
    refinementHistory.append(entry);
    input.value = "";
    const refinedPayload = {...latestPayload, refinement_notes: [...refinementNotes]};
    const succeeded = await requestPlan(refinedPayload, refinementForm.querySelector("button"));
    if (succeeded) {
      const confirmation = document.createElement("div");
      confirmation.className = "small text-success mb-2";
      confirmation.textContent = "The plan was regenerated with your refinement.";
      refinementHistory.append(confirmation);
    }
  });
}

const agentChat = document.querySelector("#agent-chat");
if (agentChat) {
  const activityList = document.querySelector("#agent-activity");
  const statusLabel = document.querySelector("#job-status");
  const result = document.querySelector("#plan-content");
  const refinementForm = document.querySelector("#chat-refinement-form");
  const planNavigation = document.querySelector("#plan-navigation");
  const goBackButton = document.querySelector("#go-back");
  const startOverButton = document.querySelector("#start-over");
  const feedbackSection = document.querySelector("#plan-feedback");
  const negativeFeedbackForm = document.querySelector("#negative-feedback-form");
  const feedbackComment = document.querySelector("#negative-feedback-comment");
  const feedbackMessage = document.querySelector("#feedback-message");
  const seenEvents = new Set();
  let stopped = false;
  const homeButton = document.querySelector("#cancel-and-home");
  const cancelWarning = document.querySelector("#cancel-warning");
  const stayButton = document.querySelector("#stay-on-chat");
  const confirmCancelButton = document.querySelector("#confirm-cancel-home");
  const progressWrap = document.querySelector("#planning-progress-wrap");
  const progressTrack = progressWrap.querySelector("[role='progressbar']");
  const progressBar = document.querySelector("#planning-progress-bar");
  const progressPercent = document.querySelector("#planning-progress-percent");
  const timeEstimate = document.querySelector("#planning-time-estimate");
  const progressStartedAt = Date.now();
  const completedMilestones = new Set();
  const activeAgents = new Set();
  const travelIcon = document.querySelector("#planning-travel-icon");
  const endRequestSession = () => fetch(agentChat.dataset.sessionEndUrl, {
    method: "POST",
    headers: {"X-CSRFToken": document.querySelector("meta[name='csrf-token']").content},
    keepalive: true
  }).catch(() => {});

  const agentNames = {
    system: "Planning system", flight_agent: "Flight agent",
    hotel_transport_agent: "Hotel & transport agent",
    accessibility_agent: "Accessibility agent", risk_advisory_agent: "Risk & advisory agent",
    orchestrator_agent: "Orchestrator agent"
  };
  const agentIcons = {
    system: "✦", flight_agent: "✈", hotel_transport_agent: "🏨",
    accessibility_agent: "♿", risk_advisory_agent: "🛡", orchestrator_agent: "🧭"
  };

  const showActiveAgentIcon = (preferredAgent = null) => {
    const agent = preferredAgent || activeAgents.values().next().value || "system";
    travelIcon.textContent = agentIcons[agent] || "✦";
    travelIcon.title = `${agentNames[agent] || agent} is working`;
  };
  const eventLabels = {
    request_accepted: "accepted your travel request",
    agent_started: "started reviewing your requirements",
    agent_completed: "completed its review",
    agent_failed: "could not complete its review",
    assurance_completed: "completed the final safety checks"
  };

  const updatePlanningProgress = (complete = false) => {
    const elapsedSeconds = Math.max(0, (Date.now() - progressStartedAt) / 1000);
    const timedProgress = Math.min(85, 5 + (elapsedSeconds / 75) * 80);
    const milestoneProgress = Math.min(95, 5 + completedMilestones.size * 14);
    const progress = complete ? 100 : Math.round(Math.max(timedProgress, milestoneProgress));
    progressBar.style.width = `${progress}%`;
    progressTrack.setAttribute("aria-valuenow", String(progress));
    progressPercent.textContent = `${progress}% complete`;
    if (complete) {
      timeEstimate.textContent = "Plan ready";
      progressWrap.classList.add("is-complete");
      travelIcon.textContent = "✓";
      travelIcon.title = "Planning complete";
    } else {
      const remaining = Math.max(5, Math.ceil((75 * (100 - progress) / 100) / 5) * 5);
      timeEstimate.textContent = `About ${remaining} seconds remaining`;
    }
  };

  const addActivity = (event) => {
    if (seenEvents.has(event.hash)) return;
    seenEvents.add(event.hash);
    if (event.event === "agent_started") {
      activeAgents.add(event.agent);
      showActiveAgentIcon(event.agent);
    } else if (["agent_completed", "agent_failed"].includes(event.event)) {
      activeAgents.delete(event.agent);
      showActiveAgentIcon();
    } else if (["request_accepted", "assurance_completed"].includes(event.event)) {
      showActiveAgentIcon("system");
    }
    if (["request_accepted", "agent_completed", "agent_failed", "assurance_completed"].includes(event.event)) {
      completedMilestones.add(event.hash);
      updatePlanningProgress();
    }
    const item = document.createElement("li");
    const time = new Date(event.timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    item.textContent = `${time} · ${agentNames[event.agent] || event.agent} ${eventLabels[event.event] || event.event}`;
    if (activityList) activityList.append(item);
  };

  const renderPlan = (response) => {
    const plan = response.plan;
    result.replaceChildren();
    const appendListSection = (label, items) => {
      if (!items || items.length === 0) return;
      const sectionHeading = document.createElement("h4");
      sectionHeading.className = "h6 mt-4";
      sectionHeading.textContent = label;
      const list = document.createElement("ul");
      items.forEach((value) => {
        const item = document.createElement("li");
        item.textContent = value;
        list.append(item);
      });
      result.append(sectionHeading, list);
    };
    const title = document.createElement("h3");
    title.className = "h4";
    title.textContent = plan.title;
    const summary = document.createElement("p");
    summary.className = "text-body-secondary";
    summary.textContent = plan.summary;
    const heading = document.createElement("h4");
    heading.className = "h6 mt-4";
    heading.textContent = "Suggested itinerary";
    const itinerary = document.createElement("ol");
    itinerary.className = "plan-itinerary";
    plan.itinerary.forEach((step) => {
      const item = document.createElement("li");
      item.textContent = step;
      itinerary.append(item);
    });
    result.append(title, summary, heading, itinerary);
    if (plan.estimated_total_cost !== null) {
      const cost = document.createElement("p");
      cost.className = "fw-semibold";
      cost.textContent = `Estimated total: ${plan.currency || ""} ${plan.estimated_total_cost}`;
      result.append(cost);
    }
    appendListSection("Why this plan was recommended", plan.rationale);
    appendListSection("Alternatives", plan.alternatives);
    appendListSection("Sources to verify", plan.sources);
    appendListSection("Assumptions", plan.assumptions);
    appendListSection("Limitations", plan.limitations);
    if (plan.safety) {
      appendListSection("Safety and verification notes", plan.safety.warnings);
    }
    const ready = document.createElement("div");
    ready.className = "alert alert-success mt-4 mb-0";
    ready.textContent = "Your recommendation is ready. Use the chat box below to fine-tune any requirement.";
    result.append(ready);
    result.append(feedbackSection);
    refinementForm.classList.remove("d-none");
    planNavigation.classList.remove("d-none");
    feedbackSection.classList.remove("d-none");
    refinementForm.querySelector("#chat-message").focus();
  };

  const feedbackWordCount = () => feedbackComment.value.trim().split(/\s+/).filter(Boolean).length;
  const submitFeedback = async (rating, comment = "") => {
    const response = await fetch(agentChat.dataset.feedbackUrl, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": document.querySelector("meta[name='csrf-token']").content},
      body: JSON.stringify({rating, comment})
    });
    const data = await response.json();
    feedbackMessage.className = `mt-3 alert ${response.ok ? "alert-success" : "alert-danger"}`;
    feedbackMessage.textContent = response.ok ? "Thank you. Your feedback has been recorded." : data.error;
    if (response.ok) {
      document.querySelector("#feedback-actions").classList.add("d-none");
      negativeFeedbackForm.classList.add("d-none");
    }
    return response.ok;
  };

  document.querySelectorAll(".feedback-rating").forEach((button) => button.addEventListener("click", async () => {
    if (button.dataset.rating === "up") await submitFeedback("up");
    else {
      negativeFeedbackForm.classList.remove("d-none");
      feedbackComment.focus();
    }
  }));
  feedbackComment.addEventListener("input", () => {
    document.querySelector("#feedback-word-count").textContent = `${feedbackWordCount()} of 10 required words`;
  });
  negativeFeedbackForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (feedbackWordCount() < 10) {
      feedbackMessage.className = "mt-3 alert alert-danger";
      feedbackMessage.textContent = "Please provide at least 10 words of feedback.";
      feedbackComment.focus();
      return;
    }
    await submitFeedback("down", feedbackComment.value.trim());
  });

  const poll = async () => {
    if (stopped) return;
    try {
      const [traceResponse, statusResponse] = await Promise.all([
        fetch(agentChat.dataset.traceUrl), fetch(agentChat.dataset.statusUrl)
      ]);
      if (traceResponse.ok) {
        const trace = await traceResponse.json();
        trace.events.forEach(addActivity);
      }
      if (!statusResponse.ok) throw new Error("Planning job could not be found.");
      const job = await statusResponse.json();
      statusLabel.textContent = job.status === "queued" ? "Waiting for the AI team…" : "Agents are analyzing your trip…";
      if (job.status === "completed") {
        const finalTraceResponse = await fetch(agentChat.dataset.traceUrl);
        if (finalTraceResponse.ok) {
          const finalTrace = await finalTraceResponse.json();
          finalTrace.events.forEach(addActivity);
        }
        stopped = true;
        statusLabel.textContent = "All agents completed their work.";
        updatePlanningProgress(true);
        renderPlan(job.response);
        return;
      }
      if (job.status === "failed") {
        stopped = true;
        statusLabel.textContent = "Planning stopped.";
        timeEstimate.textContent = "Planning stopped";
        progressWrap.classList.add("is-stopped");
        const alert = document.createElement("div");
        alert.className = "alert alert-danger";
        alert.textContent = job.error || "The AI team could not complete this plan. Please try again.";
        result.replaceChildren(alert);
        return;
      }
      if (job.status === "cancelled") {
        stopped = true;
        statusLabel.textContent = "Planning was cancelled.";
        timeEstimate.textContent = "Planning cancelled";
        progressWrap.classList.add("is-stopped");
        return;
      }
    } catch (error) {
      statusLabel.textContent = error.message;
    }
    updatePlanningProgress();
    window.setTimeout(poll, 1000);
  };

  refinementForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!refinementForm.reportValidity()) return;
    const stored = sessionStorage.getItem("atlas-plan-payload");
    if (!stored) return;
    const payload = JSON.parse(stored);
    // `_request_id` links ONE intake conversation to ONE plan submission, and
    // the first submission moves that row out of `intake` status. Replaying it
    // makes owns_intake_request() fail and the endpoint answer 404, which is
    // why refinement stopped working after the first plan. Refining is a new
    // submission of the same trip, not a second use of the intake.
    delete payload._request_id;
    const message = refinementForm.querySelector("#chat-message").value.trim();
    payload.refinement_notes = [...(payload.refinement_notes || []), message];
    const button = refinementForm.querySelector("button");
    button.disabled = true;
    const response = await fetch(agentChat.dataset.submitUrl, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": document.querySelector("meta[name='csrf-token']").content},
      body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (response.ok) {
      sessionStorage.setItem("atlas-plan-payload", JSON.stringify(payload));
      window.location.assign(body.chat_url);
    } else {
      button.disabled = false;
      statusLabel.textContent = body.error || "Unable to refine the plan.";
    }
  });

  // Both of these are deliberately non-destructive to the server. The plan is
  // already finished by the time they appear, so there is nothing to cancel —
  // unlike Home, which stops an in-flight run and warns before doing it.
  goBackButton.addEventListener("click", () => {
    // The stored payload is left intact on purpose: /main reads it back so the
    // traveller lands on their own trip details and can edit rather than retype.
    window.location.assign(goBackButton.dataset.mainUrl);
  });

  startOverButton.addEventListener("click", () => {
    // Starting from first means the next intake must not inherit this trip.
    // Clearing the payload is what makes it a fresh conversation rather than
    // one pre-filled with the old answers.
    sessionStorage.removeItem("atlas-plan-payload");
    window.location.assign(startOverButton.dataset.intakeUrl);
  });

  homeButton.addEventListener("click", () => {
    cancelWarning.showModal();
  });
  stayButton.addEventListener("click", () => cancelWarning.close());
  confirmCancelButton.addEventListener("click", async () => {
    confirmCancelButton.disabled = true;
    stayButton.disabled = true;
    homeButton.disabled = true;
    statusLabel.textContent = "Stopping the AI team…";
    try {
      await fetch(homeButton.dataset.cancelUrl, {
        method: "POST",
        headers: {"X-CSRFToken": document.querySelector("meta[name='csrf-token']").content}
      });
    } finally {
      stopped = true;
      await endRequestSession();
      showPageLoader();
      window.location.assign(homeButton.dataset.homeUrl);
    }
  });

  window.addEventListener("pagehide", endRequestSession);

  poll();
}

const adminMonitor = document.querySelector("#admin-monitor");
if (adminMonitor) {
  const requestList = document.querySelector("#admin-requests");
  const responseGrid = document.querySelector("#admin-agent-responses");
  const selectedLabel = document.querySelector("#selected-request-label");
  const liveStatus = document.querySelector("#admin-live-status");
  const tokenTotals = document.querySelector("#admin-token-totals");
  const userConsumption = document.querySelector("#admin-user-consumption");
  const liveRequestLabel = document.querySelector("#live-request-label");
  const liveRequestStatus = document.querySelector("#live-request-status");
  const liveRequestConversation = document.querySelector("#live-request-conversation");
  const liveAgentStages = document.querySelector("#live-agent-stages");
  const liveProcessingLog = document.querySelector("#live-processing-log");
  const processingTimelineDialog = document.querySelector("#processing-timeline-dialog");
  const agentWorkflowGraph = document.querySelector("#agent-workflow-graph");
  const agentWorkflowLegend = document.querySelector("#agent-workflow-legend");
  let selectedRequestId = null;
  let requests = [];
  let consumption = {totals: {}, users: []};
  let platform = {};
  let logs = [];
  let prompts = [];
  let administrators = [];
  let feedbackEntries = [];
  let lastLogsSignature = null;
  let chartPeriod = "daily";
  let tokenChart = null;
  let adoptionChart = null;
  let engagementChart = null;
  let agentTrendChart = null;
  let agentPerformanceChart = null;
  let requestPage = 1;
  const requestsPerPage = 10;
  const expandedLogIds = new Set();
  let logPage = 1;
  const logsPerPage = 20;
  let lastCompletionSignature = null;

  const agentNames = {
    flight_agent: "Flight", hotel_transport_agent: "Hotel & transport",
    accessibility_agent: "Accessibility", risk_advisory_agent: "Risk & advisory",
    orchestrator_agent: "Orchestrator"
  };
  const statusClass = (status) => ({
    completed: "text-bg-success", processing: "text-bg-primary",
    intake: "text-bg-info", queued: "text-bg-secondary",
    failed: "text-bg-danger", cancelled: "text-bg-warning"
  }[status] || "text-bg-secondary");

  const renderAgentWorkflow = (request) => {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const retainedAccessibility = [...agentWorkflowGraph.children].filter((child) => ["title", "desc"].includes(child.tagName));
    agentWorkflowGraph.replaceChildren(...retainedAccessibility);

    const definition = document.createElementNS(svgNamespace, "defs");
    const marker = document.createElementNS(svgNamespace, "marker");
    marker.setAttribute("id", "workflow-arrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9"); marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "7"); marker.setAttribute("markerHeight", "7");
    marker.setAttribute("orient", "auto-start-reverse");
    const arrow = document.createElementNS(svgNamespace, "path");
    arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z"); arrow.setAttribute("fill", "context-stroke");
    marker.append(arrow); definition.append(marker); agentWorkflowGraph.append(definition);

    const runStatus = new Map((request.agents || []).map((agent) => [agent.agent, agent.status]));
    const requestEvents = logs.filter((entry) => entry.request_id === request.request_id);
    const eventNames = new Set(requestEvents.map((entry) => entry.event));
    const hasStarted = (request.agents || []).length > 0;
    const intakeCompleted = hasStarted || eventNames.has("orchestrator_validation_completed");
    const nodes = [
      {id: "submitted", label: "User submission", x: 15, y: 188, status: "completed"},
      {id: "intake", label: "Orchestrator intake", x: 185, y: 188, status: intakeCompleted ? "completed" : "processing"},
      {id: "start", label: "Request validated", x: 355, y: 188, status: hasStarted ? "completed" : "queued"},
      {id: "flight_agent", label: "Flight", x: 545, y: 28, status: runStatus.get("flight_agent") || "queued"},
      {id: "hotel_transport_agent", label: "Hotel & transport", x: 545, y: 128, status: runStatus.get("hotel_transport_agent") || "queued"},
      {id: "accessibility_agent", label: "Accessibility", x: 545, y: 228, status: runStatus.get("accessibility_agent") || "queued"},
      {id: "risk_advisory_agent", label: "Risk & advisory", x: 545, y: 328, status: runStatus.get("risk_advisory_agent") || "queued"},
      {id: "orchestrator_agent", label: "Orchestrator", x: 900, y: 188, status: runStatus.get("orchestrator_agent") || "queued"},
      {id: "end", label: "Final itinerary", x: 1095, y: 188, status: request.status === "completed" ? "completed" : request.status === "failed" ? "failed" : "queued"}
    ];
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const specialists = ["flight_agent", "hotel_transport_agent", "accessibility_agent", "risk_advisory_agent"];
    const edges = [["submitted", "intake"], ["intake", "start"]]
      .concat(specialists.flatMap((agent) => [["start", agent], [agent, "orchestrator_agent"]]))
      .concat([["orchestrator_agent", "end"]]);
    edges.forEach(([sourceId, targetId]) => {
      const source = byId.get(sourceId); const target = byId.get(targetId);
      const path = document.createElementNS(svgNamespace, "path");
      const startX = source.x + 140; const startY = source.y + 27; const endX = target.x; const endY = target.y + 27;
      const bend = (startX + endX) / 2;
      path.setAttribute("d", `M ${startX} ${startY} C ${bend} ${startY}, ${bend} ${endY}, ${endX} ${endY}`);
      const edgeStatus = source.status === "completed" && target.status === "completed" ? "completed"
        : [source.status, target.status].includes("processing") ? "processing" : "queued";
      path.setAttribute("class", `agent-workflow-edge ${edgeStatus}`);
      path.setAttribute("marker-end", "url(#workflow-arrow)");
      agentWorkflowGraph.append(path);
    });
    nodes.forEach((node) => {
      const group = document.createElementNS(svgNamespace, "g");
      group.setAttribute("class", `agent-workflow-node ${node.status}`);
      group.setAttribute("transform", `translate(${node.x} ${node.y})`);
      const rect = document.createElementNS(svgNamespace, "rect");
      rect.setAttribute("width", "140"); rect.setAttribute("height", "54"); rect.setAttribute("rx", "13");
      const name = document.createElementNS(svgNamespace, "text");
      name.setAttribute("class", "node-name"); name.setAttribute("x", "70"); name.setAttribute("y", "23"); name.textContent = node.label;
      const status = document.createElementNS(svgNamespace, "text");
      status.setAttribute("class", "node-status"); status.setAttribute("x", "70"); status.setAttribute("y", "41"); status.textContent = node.status;
      group.append(rect, name, status); agentWorkflowGraph.append(group);
    });

    agentWorkflowLegend.replaceChildren();
    [["completed", "Completed"], ["processing", "Processing"], ["failed", "Failed"], ["queued", "Queued"]].forEach(([status, label]) => {
      const item = document.createElement("span"); item.className = status;
      const dot = document.createElement("i"); dot.setAttribute("aria-hidden", "true");
      item.append(dot, label); agentWorkflowLegend.append(item);
    });
  };

  const renderLiveProcessing = () => {
    const selected = requests.find((item) => item.request_id === selectedRequestId);
    liveAgentStages.replaceChildren();
    liveProcessingLog.replaceChildren();
    liveRequestConversation.replaceChildren();
    if (!selected) {
      liveRequestLabel.textContent = "Select a request above to inspect its processing stages.";
      liveRequestStatus.className = "badge text-bg-secondary";
      liveRequestStatus.textContent = "Waiting";
      return;
    }
    renderAgentWorkflow(selected);
    const liveRoute = selected.request.origin && selected.request.destination
      ? `${selected.request.origin} → ${selected.request.destination}` : "Trip details being collected";
    liveRequestLabel.textContent = `${liveRoute} · ${selected.request_id}`;
    liveRequestStatus.className = `badge ${statusClass(selected.status)}`;
    liveRequestStatus.textContent = selected.status;
    (selected.conversation || []).forEach((entry) => {
      const message = document.createElement("div");
      message.className = `admin-conversation-message ${entry.role}`;
      const speaker = entry.role === "user" ? "Traveller" : "Orchestrator";
      const label = document.createElement("div");
      label.className = "small fw-semibold mb-1";
      label.textContent = `${speaker} · ${new Date(entry.created_at + "Z").toLocaleTimeString()}`;
      const content = document.createElement("div");
      content.textContent = entry.content;
      message.append(label, content); liveRequestConversation.append(message);
    });
    if (!(selected.conversation || []).length) {
      liveRequestConversation.textContent = "No orchestrator conversation was recorded for this request.";
    }
    selected.agents.forEach((agent) => {
      const column = document.createElement("div");
      column.className = "col-sm-6 col-xl-4";
      const stage = document.createElement("div");
      stage.className = "live-agent-stage";
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between gap-2";
      const name = document.createElement("strong");
      name.textContent = agentNames[agent.agent] || agent.agent;
      const badge = document.createElement("span");
      badge.className = `badge ${statusClass(agent.status)}`;
      badge.textContent = agent.status;
      top.append(name, badge);
      const timing = document.createElement("div");
      timing.className = "small text-body-secondary mt-2";
      timing.textContent = agent.completed_at
        ? `Completed ${new Date(agent.completed_at + "Z").toLocaleTimeString()}`
        : agent.started_at ? `Started ${new Date(agent.started_at + "Z").toLocaleTimeString()}` : "Queued";
      stage.append(top, timing); column.append(stage); liveAgentStages.append(column);
    });
    if (!selected.agents.length) liveAgentStages.textContent = selected.status === "intake"
      ? "Orchestrator intake is collecting and validating the required trip details."
      : "Waiting for the first agent stage to start…";

    const requestLogs = logs.filter((entry) => entry.request_id === selected.request_id).reverse();
    requestLogs.forEach((entry) => {
      const item = document.createElement("li");
      const time = document.createElement("span");
      time.className = "small text-body-secondary me-2";
      time.textContent = new Date(entry.timestamp).toLocaleTimeString();
      const message = document.createElement("span");
      message.textContent = `${agentNames[entry.agent] || entry.agent}: ${entry.event.replaceAll("_", " ")}`;
      item.append(time, message); liveProcessingLog.append(item);
    });
    if (!requestLogs.length) liveProcessingLog.textContent = "No processing events recorded yet.";
  };

  const renderDetails = () => {
    const request = requests.find((item) => item.request_id === selectedRequestId);
    if (!request) return;
    const completedAgents = request.agents.filter((agent) => agent.status === "completed");
    selectedLabel.textContent = `${routeLabel(request.request)} · ${request.request_id}`;
    const labels = completedAgents.map((agent) => agentNames[agent.agent] || agent.agent);
    const chartData = {
      labels,
      datasets: [
        {label: "Input tokens", data: completedAgents.map((agent) => agent.input_tokens), backgroundColor: "#195ee7"},
        {label: "Output tokens", data: completedAgents.map((agent) => agent.output_tokens), backgroundColor: "#5ee6c2"}
      ]
    };
    if (tokenChart) {
      tokenChart.data = chartData;
      tokenChart.update("none");
    } else if (window.Chart) {
      tokenChart = new Chart(document.querySelector("#agent-token-chart"), {
        type: "bar", data: chartData,
        options: {responsive: true, maintainAspectRatio: false, animation: false, scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}, plugins: {legend: {position: "bottom"}}}
      });
    }

    if (!responseGrid) {
      renderAllAgentTokens();
      return;
    }
    responseGrid.replaceChildren();
    completedAgents.forEach((agent) => {
      const column = document.createElement("div");
      column.className = "col-lg-6";
      const card = document.createElement("article");
      card.className = "border rounded-4 p-3 h-100";
      const header = document.createElement("div");
      header.className = "d-flex justify-content-between gap-2 mb-2";
      const name = document.createElement("strong");
      name.textContent = agentNames[agent.agent] || agent.agent;
      const badge = document.createElement("span");
      badge.className = `badge ${statusClass(agent.status)}`;
      badge.textContent = agent.status;
      header.append(name, badge);
      const usage = document.createElement("p");
      usage.className = "small text-body-secondary";
      usage.textContent = `${agent.total_tokens.toLocaleString()} total tokens (${agent.input_tokens.toLocaleString()} input + ${agent.output_tokens.toLocaleString()} output)`;
      const response = document.createElement("pre");
      response.className = "agent-response-json mb-0";
      response.textContent = agent.response ? JSON.stringify(agent.response, null, 2) : (agent.error_type || "Waiting for response…");
      card.append(header, usage, response);
      column.append(card);
      responseGrid.append(column);
    });
    if (completedAgents.length === 0) {
      responseGrid.textContent = "Waiting for an agent to complete its response…";
    }
  };

  const completionSignature = () => {
    const request = requests.find((item) => item.request_id === selectedRequestId);
    if (!request) return "none";
    return request.agents
      .filter((agent) => agent.status === "completed")
      .map((agent) => `${agent.agent}:${agent.completed_at}:${agent.total_tokens}`)
      .sort()
      .join("|");
  };

  const renderRequests = () => {
    requestList.replaceChildren();
    requests.forEach((request) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `list-group-item list-group-item-action admin-request p-3 ${request.request_id === selectedRequestId ? "active" : ""}`;
      const top = document.createElement("div");
      top.className = "d-flex justify-content-between gap-2";
      const route = document.createElement("strong");
      route.textContent = routeLabel(request.request);
      const badge = document.createElement("span");
      badge.className = `badge ${statusClass(request.status)}`;
      badge.textContent = request.status;
      top.append(route, badge);
      const meta = document.createElement("div");
      meta.className = "small text-body-secondary mt-1";
      meta.textContent = `${request.name || request.email || "Unknown user"} · ${new Date(request.submitted_at + "Z").toLocaleString()}`;
      const usage = document.createElement("div");
      usage.className = "small fw-semibold mt-1";
      usage.textContent = `${(request.usage?.total_tokens || 0).toLocaleString()} tokens (${(request.usage?.input_tokens || 0).toLocaleString()} input + ${(request.usage?.output_tokens || 0).toLocaleString()} output)`;
      button.append(top, meta, usage);
      button.addEventListener("click", () => {
        selectedRequestId = request.request_id;
        lastCompletionSignature = null;
        renderRequests();
        renderDetails();
        lastCompletionSignature = completionSignature();
      });
      requestList.append(button);
    });
    if (requests.length === 0) requestList.textContent = "No planning requests recorded yet.";
  };

  const renderConsumption = () => {
    const totals = consumption.totals || {};
    tokenTotals.replaceChildren();
    [["Requests", totals.request_count], ["Input tokens", totals.input_tokens],
      ["Output tokens", totals.output_tokens], ["Total tokens", totals.total_tokens]].forEach(([label, value]) => {
      const column = document.createElement("div");
      column.className = "col-6 col-lg-3";
      const box = document.createElement("div");
      box.className = "border rounded-3 p-3";
      const caption = document.createElement("div");
      caption.className = "small text-body-secondary";
      caption.textContent = label;
      const number = document.createElement("div");
      number.className = "h4 mb-0";
      number.textContent = (value || 0).toLocaleString();
      box.append(caption, number);
      column.append(box);
      tokenTotals.append(column);
    });
    userConsumption.replaceChildren();
    (consumption.users || []).forEach((user) => {
      const row = document.createElement("tr");
      const values = [user.name || user.email || "Unknown user", user.request_count,
        user.input_tokens, user.output_tokens, user.total_tokens,
        user.last_request_at ? new Date(user.last_request_at + "Z").toLocaleString() : "-"];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = index > 0 && index < 5 ? (value || 0).toLocaleString() : value;
        row.append(cell);
      });
      userConsumption.append(row);
    });
    if (!(consumption.users || []).length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.className = "text-body-secondary";
      cell.textContent = "No token consumption recorded yet.";
      row.append(cell);
      userConsumption.append(row);
    }
  };

  const renderAgentAnalytics = () => {
    const allDays = (chartPeriod === "monthly" ? (platform.monthly_trend || []) : (platform.trend || [])).map((item) => item.date);
    const trendRows = chartPeriod === "monthly" ? (consumption.agent_trend_monthly || []) : (consumption.agent_trend || []);
    const firstAgentIndex = allDays.findIndex((day) => trendRows.some((row) => row.date === day));
    const days = firstAgentIndex < 0 ? [] : allDays.slice(firstAgentIndex);
    const labels = days.map((day) => chartPeriod === "monthly"
      ? new Date(`${day}-01T00:00:00`).toLocaleDateString(undefined, {month: "short", year: "numeric"})
      : new Date(`${day}T00:00:00`).toLocaleDateString(undefined, {month: "short", day: "numeric"}));
    const agents = [...new Set(trendRows.map((row) => row.agent))];
    const colors = ["#195ee7", "#198754", "#fd7e14", "#5e35b1", "#dc3545", "#0dcaf0"];
    const trendData = {labels, datasets: agents.map((agent, index) => {
      const values = new Map(trendRows.filter((row) => row.agent === agent).map((row) => [row.date, row.total_tokens]));
      const first = days.findIndex((day) => values.has(day));
      return {label: agentNames[agent] || agent, data: days.map((day, index) => index < first || first < 0 ? null : (values.get(day) || 0)), borderColor: colors[index % colors.length], tension: .2};
    })};
    document.querySelector("#agent-trend-description").textContent = chartPeriod === "monthly"
      ? "Monthly token consumption from the first recorded month within the last 12 months."
      : "Daily token consumption from the first recorded day within the last 31 days.";
    const staticLineOptions = {responsive: true, maintainAspectRatio: false, animation: false, scales: {y: {beginAtZero: true, title: {display: true, text: "Total Tokens"}}}, plugins: {legend: {position: "bottom"}}};
    if (agentTrendChart) { agentTrendChart.data = trendData; agentTrendChart.update("none"); }
    else if (window.Chart) agentTrendChart = new Chart(document.querySelector("#agent-token-trend-chart"), {type: "line", data: trendData, options: staticLineOptions});

    const performance = consumption.agent_performance || [];
    const performanceData = {labels: performance.map((item) => agentNames[item.agent] || item.agent), datasets: [
      {type: "bar", label: "Completion Rate (%)", data: performance.map((item) => item.completion_rate), backgroundColor: "#19875499", yAxisID: "rate"},
      {type: "line", label: "Average Tokens", data: performance.map((item) => item.average_tokens), borderColor: "#195ee7", backgroundColor: "#195ee7", yAxisID: "tokens"}
    ]};
    const performanceOptions = {responsive: true, maintainAspectRatio: false, animation: false, scales: {
      rate: {beginAtZero: true, max: 100, position: "left", title: {display: true, text: "Completion Rate (%)"}},
      tokens: {beginAtZero: true, position: "right", grid: {drawOnChartArea: false}, title: {display: true, text: "Average Tokens"}}
    }, plugins: {legend: {position: "bottom"}}};
    if (agentPerformanceChart) { agentPerformanceChart.data = performanceData; agentPerformanceChart.update("none"); }
    else if (window.Chart) agentPerformanceChart = new Chart(document.querySelector("#agent-performance-chart"), {data: performanceData, options: performanceOptions});
  };

  const renderAllAgentTokens = () => {
    const totals = consumption.agent_totals || [];
    selectedLabel.textContent = "Cumulative token usage across all recorded requests.";
    const chartData = {
      labels: totals.map((agent) => agentNames[agent.agent] || agent.agent),
      datasets: [
        {label: "Input Tokens", data: totals.map((agent) => agent.input_tokens), backgroundColor: "#195ee7"},
        {label: "Output Tokens", data: totals.map((agent) => agent.output_tokens), backgroundColor: "#5ee6c2"}
      ]
    };
    if (tokenChart) {
      tokenChart.data = chartData;
      tokenChart.update("none");
    } else if (window.Chart) {
      tokenChart = new Chart(document.querySelector("#agent-token-chart"), {
        type: "bar", data: chartData,
        options: {responsive: true, maintainAspectRatio: false, animation: false,
          scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true, title: {display: true, text: "Total Tokens"}}},
          plugins: {legend: {position: "bottom"}}}
      });
    }
  };

  const renderRequestTable = () => {
    requestList.replaceChildren();
    const pageCount = Math.max(1, Math.ceil(requests.length / requestsPerPage));
    requestPage = Math.min(requestPage, pageCount);
    requests.slice((requestPage - 1) * requestsPerPage, requestPage * requestsPerPage).forEach((item) => {
      const row = document.createElement("tr");
      row.className = item.request_id === selectedRequestId ? "table-primary admin-request" : "admin-request";
      row.tabIndex = 0;
      const values = [new Date(item.submitted_at + "Z").toLocaleString(), item.name || item.email || "Unknown user",
        routeLabel(item.request), item.display_status,
        item.feedback_rating === "up" ? "👍 Good Plan" : item.feedback_rating === "down" ? "👎 Needs Improvement" : "Not Rated",
        item.usage?.input_tokens || 0,
        item.usage?.output_tokens || 0, item.usage?.total_tokens || 0];
      values[2] = item.request.origin && item.request.destination ? values[2] : "Details pending";
      const sessionState = item.session_status === "ended" ? "Ended" : "Active";
      values.splice(4, 0, sessionState);
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        if (index === 3 && item.status === "completed") {
          const statusButton = document.createElement("button");
          statusButton.type = "button";
          statusButton.className = "badge text-bg-success admin-status-button";
          statusButton.textContent = value;
          statusButton.setAttribute("aria-label", `View completed agent workflow for ${routeLabel(item.request)}`);
          statusButton.addEventListener("click", (event) => { event.stopPropagation(); selectRequest(); });
          cell.append(statusButton);
        } else {
          cell.textContent = index >= 6 ? value.toLocaleString() : value;
        }
        if (index === 4) cell.className = sessionState === "Active" ? "text-success fw-semibold" : "text-body-secondary";
        row.append(cell);
      });
      const selectRequest = () => {
        selectedRequestId = item.request_id;
        lastCompletionSignature = null;
        renderRequestTable();
        renderDetails();
        renderLiveProcessing();
        lastCompletionSignature = completionSignature();
        if (!processingTimelineDialog.open) processingTimelineDialog.showModal();
      };
      row.addEventListener("click", selectRequest);
      row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") selectRequest(); });
      requestList.append(row);
    });
    if (!requests.length) requestList.innerHTML = '<tr><td colspan="9" class="text-body-secondary">No user requests recorded yet.</td></tr>';
    document.querySelector("#request-page-status").textContent = `Page ${requestPage} of ${pageCount} · ${requests.length} requests`;
    document.querySelector("#request-page-previous").disabled = requestPage === 1;
    document.querySelector("#request-page-next").disabled = requestPage === pageCount;
  };
  document.querySelector("#request-page-previous").addEventListener("click", () => { requestPage -= 1; renderRequestTable(); });
  document.querySelector("#request-page-next").addEventListener("click", () => { requestPage += 1; renderRequestTable(); });
  document.querySelector("#close-processing-timeline").addEventListener("click", () => processingTimelineDialog.close());
  processingTimelineDialog.addEventListener("click", (event) => {
    if (event.target === processingTimelineDialog) processingTimelineDialog.close();
  });

  const renderPlatform = () => {
    const kpis = document.querySelector("#platform-kpis");
    kpis.replaceChildren();
    [["Registered users", platform.registered_users, "bi-people"], ["Total requests", platform.total_requests, "bi-send"],
      ["Completion rate", `${platform.completion_rate || 0}%`, "bi-check-circle"], ["Failed requests", platform.failed_requests, "bi-exclamation-triangle"]]
      .forEach(([label, value, icon]) => {
        const column = document.createElement("div");
        column.className = "col-sm-6 col-xl-3";
        column.innerHTML = `<div class="card border-0 shadow-sm h-100"><div class="card-body p-4"><i class="bi ${icon} fs-3 text-primary"></i><div class="small text-body-secondary mt-2"></div><div class="h3 mb-0"></div></div></div>`;
        column.querySelector(".small").textContent = label;
        column.querySelector(".h3").textContent = value || 0;
        kpis.append(column);
      });
    document.querySelector("#adoption-details").textContent = `${platform.adoption_rate || 0}% overall adoption · ${platform.active_users || 0} active of ${platform.registered_users || 0} registered users`;
    const goodPlan = platform.average_feedback;
    const needsImprovement = platform.needs_improvement_rate;
    const satisfaction = goodPlan === null ? "No completed plans yet" : `Good Plan: ${goodPlan}% · Needs Improvement: ${needsImprovement}%`;
    document.querySelector("#engagement-details").textContent = `${platform.engagement_score || 0}% current engagement · ${satisfaction} (${platform.feedback_count || 0} rated requests)`;
    const fullTrend = chartPeriod === "monthly" ? (platform.monthly_trend || []) : (platform.trend || []);
    const firstApplicationIndex = fullTrend.findIndex((item) => Number(item.requests || 0) > 0);
    const trend = firstApplicationIndex < 0 ? [] : fullTrend.slice(firstApplicationIndex);
    const labels = trend.map((item) => chartPeriod === "monthly"
      ? new Date(`${item.date}-01T00:00:00`).toLocaleDateString(undefined, {month: "short", year: "numeric"})
      : new Date(`${item.date}T00:00:00`).toLocaleDateString(undefined, {month: "short", day: "numeric"}));
    const maskBefore = (key, evidenceKey) => {
      const first = trend.findIndex((item) => Number(item[evidenceKey] || 0) > 0);
      return trend.map((item, index) => first < 0 || index < first ? null : item[key]);
    };
    const adoptionData = {labels, datasets: [
      {label: "Registered Users", data: maskBefore("registered_users", "registered_users"), borderColor: "#195ee7", backgroundColor: "#195ee722", fill: true, tension: .25},
      {label: chartPeriod === "monthly" ? "Monthly Active Users" : "Daily Active Users", data: maskBefore("active_users", "requests"), borderColor: "#5e35b1", tension: .25}
    ]};
    const engagementData = {labels, datasets: [
      {label: "Engagement Score (%)", data: maskBefore("engagement_score", "requests"), borderColor: "#195ee7", backgroundColor: "#195ee722", fill: false, tension: .25, spanGaps: true},
      {label: "Good Plan (%)", data: maskBefore("good_feedback_score", "completed_requests"), borderColor: "#198754", backgroundColor: "#198754", pointRadius: 6, pointHoverRadius: 8, clip: false, tension: .25, spanGaps: true},
      {label: "Needs Improvement (%)", data: maskBefore("bad_feedback_score", "completed_requests"), borderColor: "#dc3545", backgroundColor: "#dc3545", pointRadius: 6, pointHoverRadius: 8, clip: false, tension: .25, spanGaps: true}
    ]};
    document.querySelector("#adoption-chart-heading").textContent = `User Access And Adoption · ${chartPeriod === "monthly" ? "12 Months" : "31 Days"}`;
    document.querySelector("#engagement-chart-heading").textContent = `Feedback And Engagement · ${chartPeriod === "monthly" ? "12 Months" : "31 Days"}`;
    const chartOptions = {responsive: true, maintainAspectRatio: false, animation: false, scales: {y: {beginAtZero: true}}, plugins: {legend: {position: "bottom"}}};
    if (adoptionChart) { adoptionChart.data = adoptionData; adoptionChart.update("none"); }
    else if (window.Chart) adoptionChart = new Chart(document.querySelector("#adoption-trend-chart"), {type: "line", data: adoptionData, options: chartOptions});
    if (engagementChart) { engagementChart.data = engagementData; engagementChart.update("none"); }
    else if (window.Chart) engagementChart = new Chart(document.querySelector("#engagement-trend-chart"), {type: "line", data: engagementData, options: {...chartOptions,
      scales: {y: {beginAtZero: true, suggestedMax: 100, grace: "5%", title: {display: true, text: "Score (%)"}, ticks: {callback: (value) => `${value}%`}}},
      plugins: {legend: {position: "bottom"}, tooltip: {callbacks: {label: (context) => `${context.dataset.label}: ${context.parsed.y}%`}}}
    }});
  };

  const renderFeedback = () => {
    const body = document.querySelector("#admin-feedback-rows");
    body.replaceChildren();
    feedbackEntries.forEach((entry) => {
      const row = document.createElement("tr");
      const values = [new Date(entry.updated_at + "Z").toLocaleString(), entry.name || entry.email,
        routeLabel(entry), entry.rating === "up" ? "👍 Good" : "👎 Needs Improvement",
        entry.comment || "No written comment"];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 4) cell.className = "text-break";
        row.append(cell);
      });
      body.append(row);
    });
    if (!feedbackEntries.length) body.innerHTML = '<tr><td colspan="5" class="text-body-secondary">No plan feedback has been submitted yet.</td></tr>';
  };

  const renderLogs = () => {
    const body = document.querySelector("#system-log-rows");
    body.replaceChildren();
    const pageCount = Math.max(1, Math.ceil(logs.length / logsPerPage));
    logPage = Math.min(logPage, pageCount);
    logs.slice((logPage - 1) * logsPerPage, logPage * logsPerPage).forEach((log) => {
      const row = document.createElement("tr");
      [new Date(log.timestamp).toLocaleString(), log.request_id, log.email || "Unknown user"].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 1) cell.className = "small font-monospace text-break";
        row.append(cell);
      });
      const component = document.createElement("td");
      const componentButton = document.createElement("button");
      componentButton.type = "button";
      componentButton.className = "btn btn-sm btn-link p-0 text-start";
      componentButton.textContent = log.agent;
      componentButton.disabled = !log.agent_response;
      componentButton.title = log.agent_response ? "Show recorded agent response" : "No agent response recorded for this event";
      component.append(componentButton);
      row.append(component);
      [log.event, log.transaction_status || "-", JSON.stringify(log.details)].forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 2) cell.className = "small font-monospace text-break";
        row.append(cell);
      });
      body.append(row);
      const responseRow = document.createElement("tr");
      responseRow.className = `agent-audit-response ${expandedLogIds.has(log.id) ? "" : "d-none"}`;
      const responseCell = document.createElement("td");
      responseCell.colSpan = 7;
      const response = document.createElement("pre");
      response.className = "agent-response-json mb-0 p-3";
      response.textContent = log.agent_response ? JSON.stringify(log.agent_response, null, 2) : "No response recorded.";
      responseCell.append(response); responseRow.append(responseCell); body.append(responseRow);
      componentButton.addEventListener("click", () => {
        const isOpening = responseRow.classList.contains("d-none");
        responseRow.classList.toggle("d-none");
        if (isOpening) expandedLogIds.add(log.id);
        else expandedLogIds.delete(log.id);
      });
    });
    if (!logs.length) body.innerHTML = '<tr><td colspan="7" class="text-body-secondary">No processing events recorded yet.</td></tr>';
    document.querySelector("#log-page-status").textContent = `Page ${logPage} of ${pageCount} · ${logs.length} events`;
    document.querySelector("#log-page-previous").disabled = logPage === 1;
    document.querySelector("#log-page-next").disabled = logPage === pageCount;
  };
  document.querySelector("#log-page-previous").addEventListener("click", () => { logPage -= 1; renderLogs(); });
  document.querySelector("#log-page-next").addEventListener("click", () => { logPage += 1; renderLogs(); });

  const renderPrompts = () => {
    const container = document.querySelector("#prompt-cards");
    container.replaceChildren();
    prompts.forEach((prompt) => {
      const column = document.createElement("div");
      column.className = "col-lg-6";
      const card = document.createElement("article");
      card.className = "card border-0 shadow-sm h-100";
      const body = document.createElement("div");
      body.className = "card-body p-4";
      const title = document.createElement("h2");
      title.className = "h5";
      title.textContent = prompt.agent;
      const instruction = document.createElement("pre");
      instruction.className = "prompt-instruction mb-0";
      instruction.textContent = prompt.instruction;
      body.append(title, instruction); card.append(body); column.append(card); container.append(column);
    });
  };

  const renderAdministrators = () => {
    const list = document.querySelector("#administrator-list");
    list.replaceChildren();
    administrators.forEach((admin) => {
      const item = document.createElement("div");
      item.className = "list-group-item px-0 d-flex justify-content-between gap-3";
      item.innerHTML = '<div><strong></strong><div class="small text-body-secondary"></div></div><i class="bi bi-shield-check text-success"></i>';
      item.querySelector("strong").textContent = admin.name || admin.email;
      item.querySelector(".small").textContent = admin.email;
      list.append(item);
    });
    if (!administrators.length) list.textContent = "Configured .env administrators are not listed here.";
  };

  const tabCopy = {
    critical: ["Application Dashboard", "Platform performance, access, adoption, and engagement."],
    performance: ["Agent Performance", "Agent execution, request logs, and token consumption."],
    logs: ["System Processing Logs", "End-to-end processing events for every transaction."],
    prompts: ["Prompts And Guardrails", "Current agent instructions and deterministic safeguards."],
    administrators: ["Administrator Registration", "Create or promote database-managed administrators."]
  };
  const setChartPeriod = (period) => {
    chartPeriod = period;
    const daily = document.querySelector("#chart-period-daily");
    const monthly = document.querySelector("#chart-period-monthly");
    daily.className = `btn ${period === "daily" ? "btn-primary" : "btn-outline-primary"}`;
    monthly.className = `btn ${period === "monthly" ? "btn-primary" : "btn-outline-primary"}`;
    daily.setAttribute("aria-pressed", String(period === "daily"));
    monthly.setAttribute("aria-pressed", String(period === "monthly"));
    renderPlatform();
    renderAgentAnalytics();
  };
  document.querySelector("#chart-period-daily").addEventListener("click", () => setChartPeriod("daily"));
  document.querySelector("#chart-period-monthly").addEventListener("click", () => setChartPeriod("monthly"));
  document.querySelectorAll("[data-admin-tab]").forEach((tab) => tab.addEventListener("click", () => {
    const selected = tab.dataset.adminTab;
    document.querySelectorAll("[data-admin-tab]").forEach((item) => { item.classList.toggle("active", item === tab); item.setAttribute("aria-selected", item === tab); });
    document.querySelectorAll("[data-panel]").forEach((panel) => panel.classList.toggle("d-none", panel.dataset.panel !== selected));
    document.querySelector("#admin-page-title").textContent = tabCopy[selected][0];
    document.querySelector("#admin-page-description").textContent = tabCopy[selected][1];
    if (selected === "performance" && tokenChart) tokenChart.resize();
  }));
  document.querySelector("#sidebar-toggle").addEventListener("click", (event) => {
    const collapsed = document.querySelector("#admin-shell").classList.toggle("sidebar-collapsed");
    event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
    event.currentTarget.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  });

  document.querySelector("#admin-registration-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.querySelector("#admin-registration-message");
    const form = new FormData(event.currentTarget);
    const response = await fetch(adminMonitor.dataset.registerEndpoint, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": document.querySelector("meta[name='csrf-token']").content}, body: JSON.stringify(Object.fromEntries(form))});
    const data = await response.json();
    message.className = `mt-3 alert ${response.ok ? "alert-success" : "alert-danger"}`;
    message.textContent = response.ok ? `${data.administrator.email} is now an administrator.` : data.error;
    if (response.ok) { event.currentTarget.reset(); administrators.unshift(data.administrator); renderAdministrators(); }
  });

  const refreshAdmin = async () => {
    try {
      const response = await fetch(adminMonitor.dataset.endpoint, {cache: "no-store"});
      if (!response.ok) throw new Error("Monitoring data is unavailable.");
      const data = await response.json();
      requests = data.requests;
      consumption = data.consumption;
      platform = data.platform;
      logs = data.logs;
      prompts = data.prompts;
      administrators = data.admins;
      feedbackEntries = data.feedback;
      if (!selectedRequestId || !requests.some((item) => item.request_id === selectedRequestId)) {
        selectedRequestId = requests[0]?.request_id || null;
        lastCompletionSignature = null;
      }
      renderRequestTable();
      renderConsumption();
      renderPlatform();
      renderAgentAnalytics();
      renderAllAgentTokens();
      const logsSignature = logs.map((log) => `${log.id}:${log.transaction_status}:${Boolean(log.agent_response)}`).join("|");
      if (logsSignature !== lastLogsSignature) {
        renderLogs();
        lastLogsSignature = logsSignature;
      }
      renderPrompts();
      renderAdministrators();
      renderFeedback();
      renderLiveProcessing();
      const nextSignature = completionSignature();
      if (nextSignature !== lastCompletionSignature) {
        renderDetails();
        lastCompletionSignature = nextSignature;
      }
      liveStatus.className = "badge text-bg-success";
      liveStatus.textContent = "Live · updated now";
    } catch (error) {
      liveStatus.className = "badge text-bg-danger";
      liveStatus.textContent = error.message;
    }
    window.setTimeout(refreshAdmin, 2000);
  };
  refreshAdmin();
}
