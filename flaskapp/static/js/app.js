"use strict";

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
  planner.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!planner.reportValidity()) return;

    const button = planner.querySelector("button[type='submit']");
    const spinner = button.querySelector(".spinner-border");
    const result = document.querySelector("#plan-result");
    const data = new FormData(planner);
    const commaList = (name) => String(data.get(name) || "").split(",").map((item) => item.trim()).filter(Boolean);
    const payload = {
      origin: data.get("origin"), destination: data.get("destination"),
      departure_date: data.get("departure_date"), return_date: data.get("return_date"),
      travellers: Number(data.get("travellers")), currency: "SGD",
      preferences: commaList("preferences"), accessibility_needs: commaList("accessibility_needs")
    };
    if (data.get("budget")) payload.budget = Number(data.get("budget"));

    button.disabled = true;
    spinner.classList.remove("d-none");
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
      const card = document.createElement("article");
      card.className = "card plan-output shadow-sm";
      const cardBody = document.createElement("div");
      cardBody.className = "card-body p-4";
      const title = document.createElement("h2");
      title.className = "h4";
      title.textContent = body.plan.title;
      const summary = document.createElement("p");
      summary.className = "text-body-secondary mb-0";
      summary.textContent = body.plan.summary;
      cardBody.append(title, summary);
      card.append(cardBody);
      result.append(card);
    } catch (error) {
      const alert = document.createElement("div");
      alert.className = "alert alert-danger";
      alert.setAttribute("role", "alert");
      alert.textContent = error.message;
      result.append(alert);
    } finally {
      button.disabled = false;
      spinner.classList.add("d-none");
    }
  });
}
