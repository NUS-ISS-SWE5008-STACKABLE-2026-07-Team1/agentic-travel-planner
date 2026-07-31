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
  if (form.id !== "travel-plan-form" && form.id !== "refinement-form" && form.checkValidity()) {
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

const planner = document.querySelector("#travel-plan-form");
if (planner) {
  const travelersInput = planner.querySelector("#travellers");
  const travelerDetails = planner.querySelector("#traveler-details");
  const addTravelerButton = planner.querySelector("#add-traveler");
  const result = document.querySelector("#plan-result");
  const refinementPanel = document.querySelector("#refinement-panel");
  const refinementForm = document.querySelector("#refinement-form");
  const refinementHistory = document.querySelector("#refinement-history");
  const refinementNotes = [];
  let latestPayload = null;

  const renderTravelerFields = () => {
    const previousAges = [...travelerDetails.querySelectorAll("[name='traveller_age']")].map((field) => field.value);
    const previousGenders = [...travelerDetails.querySelectorAll("[name='traveller_gender']")].map((field) => field.value);
    const previousAccessibility = [...travelerDetails.querySelectorAll("[name='traveller_accessibility']")].map((field) => field.value);
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
              <label class="form-label" for="traveller-accessibility-${index}">Accessibility needs <span class="text-body-secondary fw-normal">(optional)</span></label>
              <input class="form-control" id="traveller-accessibility-${index}" name="traveller_accessibility" placeholder="Step-free access, low walking distance">
              <div class="form-text">Separate needs with commas. These are treated as hard constraints.</div>
            </div>
          </div>
        </div>`;
      wrapper.querySelector("[name='traveller_age']").value = previousAges[index] || "";
      wrapper.querySelector("[name='traveller_gender']").value = previousGenders[index] || "";
      wrapper.querySelector("[name='traveller_accessibility']").value = previousAccessibility[index] || "";
      travelerDetails.append(wrapper);
    }
    addTravelerButton.disabled = count >= 20;
  };

  const buildPayload = () => {
    const data = new FormData(planner);
    const commaList = (name) => String(data.get(name) || "").split(",").map((item) => item.trim()).filter(Boolean);
    const perTravelerAccessibility = data.getAll("traveller_accessibility").map((value) =>
      String(value).split(",").map((item) => item.trim()).filter(Boolean)
    );
    const combinedAccessibility = perTravelerAccessibility.flatMap((needs, index) =>
      needs.map((need) => `Traveler ${index + 1}: ${need}`)
    );
    return {
      origin: data.get("origin"), destination: data.get("destination"),
      departure_date: data.get("departure_date"), return_date: data.get("return_date"),
      travellers: Number(data.get("travellers")), currency: "SGD",
      traveller_ages: data.getAll("traveller_age").map(Number),
      traveller_genders: data.getAll("traveller_gender"),
      traveller_accessibility_needs: perTravelerAccessibility,
      budget: Number(data.get("budget")),
      preferences: commaList("preferences"), accessibility_needs: combinedAccessibility,
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
    const accessibilityFields = [...travelerDetails.querySelectorAll("[name='traveller_accessibility']")];
    ageFields[index].removeAttribute("name");
    genderFields[index].removeAttribute("name");
    accessibilityFields[index].removeAttribute("name");
    removeButton.closest(".col-md-6").remove();
    travelersInput.value = String(Math.max(1, Number(travelersInput.value) - 1));
    renderTravelerFields();
  });
  renderTravelerFields();

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
  const result = document.querySelector("#chat-result");
  const refinementForm = document.querySelector("#chat-refinement-form");
  const seenEvents = new Set();
  let stopped = false;
  const homeButton = document.querySelector("#cancel-and-home");
  const cancelWarning = document.querySelector("#cancel-warning");
  const stayButton = document.querySelector("#stay-on-chat");
  const confirmCancelButton = document.querySelector("#confirm-cancel-home");

  const agentNames = {
    system: "Planning system", flight_agent: "Flight agent",
    hotel_transport_agent: "Hotel & transport agent",
    accessibility_agent: "Accessibility agent", risk_advisory_agent: "Risk & advisory agent",
    orchestrator_agent: "Orchestrator agent"
  };
  const eventLabels = {
    request_accepted: "accepted your travel request",
    agent_started: "started reviewing your requirements",
    agent_completed: "completed its review",
    agent_failed: "could not complete its review",
    assurance_completed: "completed the final safety checks"
  };

  const addActivity = (event) => {
    if (seenEvents.has(event.hash)) return;
    seenEvents.add(event.hash);
    const item = document.createElement("li");
    const time = new Date(event.timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    item.textContent = `${time} · ${agentNames[event.agent] || event.agent} ${eventLabels[event.event] || event.event}`;
    activityList.append(item);
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
    refinementForm.classList.remove("d-none");
    refinementForm.querySelector("#chat-message").focus();
  };

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
        renderPlan(job.response);
        return;
      }
      if (job.status === "failed") {
        stopped = true;
        statusLabel.textContent = "Planning stopped.";
        const alert = document.createElement("div");
        alert.className = "alert alert-danger";
        alert.textContent = job.error || "The AI team could not complete this plan. Please try again.";
        result.replaceChildren(alert);
        return;
      }
      if (job.status === "cancelled") {
        stopped = true;
        statusLabel.textContent = "Planning was cancelled.";
        return;
      }
    } catch (error) {
      statusLabel.textContent = error.message;
    }
    window.setTimeout(poll, 1000);
  };

  refinementForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!refinementForm.reportValidity()) return;
    const stored = sessionStorage.getItem("atlas-plan-payload");
    if (!stored) return;
    const payload = JSON.parse(stored);
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
      showPageLoader();
      window.location.assign(homeButton.dataset.homeUrl);
    }
  });

  poll();
}

const adminMonitor = document.querySelector("#admin-monitor");
if (adminMonitor) {
  const requestList = document.querySelector("#admin-requests");
  const responseGrid = document.querySelector("#admin-agent-responses");
  const selectedLabel = document.querySelector("#selected-request-label");
  const liveStatus = document.querySelector("#admin-live-status");
  let selectedRequestId = null;
  let requests = [];
  let tokenChart = null;
  let lastCompletionSignature = null;

  const agentNames = {
    flight_agent: "Flight", hotel_transport_agent: "Hotel & transport",
    accessibility_agent: "Accessibility", risk_advisory_agent: "Risk & advisory",
    orchestrator_agent: "Orchestrator"
  };
  const statusClass = (status) => ({
    completed: "text-bg-success", processing: "text-bg-primary",
    queued: "text-bg-secondary", failed: "text-bg-danger", cancelled: "text-bg-warning"
  }[status] || "text-bg-secondary");

  const renderDetails = () => {
    const request = requests.find((item) => item.request_id === selectedRequestId);
    if (!request) return;
    const completedAgents = request.agents.filter((agent) => agent.status === "completed");
    selectedLabel.textContent = `${request.request.origin} → ${request.request.destination} · ${request.request_id}`;
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
      tokenChart.update();
    } else if (window.Chart) {
      tokenChart = new Chart(document.querySelector("#agent-token-chart"), {
        type: "bar", data: chartData,
        options: {responsive: true, maintainAspectRatio: false, scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}, plugins: {legend: {position: "bottom"}}}
      });
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
      route.textContent = `${request.request.origin} → ${request.request.destination}`;
      const badge = document.createElement("span");
      badge.className = `badge ${statusClass(request.status)}`;
      badge.textContent = request.status;
      top.append(route, badge);
      const meta = document.createElement("div");
      meta.className = "small text-body-secondary mt-1";
      meta.textContent = `${request.name || request.email || "Unknown user"} · ${new Date(request.submitted_at + "Z").toLocaleString()}`;
      button.append(top, meta);
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

  const refreshAdmin = async () => {
    try {
      const response = await fetch(adminMonitor.dataset.endpoint, {cache: "no-store"});
      if (!response.ok) throw new Error("Monitoring data is unavailable.");
      requests = (await response.json()).requests;
      if (!selectedRequestId || !requests.some((item) => item.request_id === selectedRequestId)) {
        selectedRequestId = requests[0]?.request_id || null;
        lastCompletionSignature = null;
      }
      renderRequests();
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
