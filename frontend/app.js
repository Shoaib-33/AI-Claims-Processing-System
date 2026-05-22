const form = document.querySelector("#claim-form");
const invoiceInput = document.querySelector("#invoice");
const uploadStatus = document.querySelector("#upload-status");
const submitBtn = document.querySelector("#submit-btn");
const backBtn = document.querySelector("#back-btn");
let invoiceHash = "";

const fields = {
  patient_name: document.querySelector("#patient_name"),
  patient_address: document.querySelector("#patient_address"),
  claim_item: document.querySelector("#claim_item"),
  date_of_treatment: document.querySelector("#date_of_treatment"),
  medical_facility: document.querySelector("#medical_facility"),
  claim_reason: document.querySelector("#claim_reason"),
  claim_amount: document.querySelector("#claim_amount"),
};

window.addEventListener("pageshow", () => {
  form.reset();
  invoiceHash = "";
  uploadStatus.textContent = "";
});

invoiceInput.addEventListener("change", async () => {
  const file = invoiceInput.files[0];
  if (!file) return;

  uploadStatus.textContent = "Extracting invoice fields...";
  const data = new FormData();
  data.append("invoice", file);

  try {
    const response = await fetch("/api/extract-invoice", {
      method: "POST",
      body: data,
    });
    if (!response.ok) throw new Error("Invoice extraction failed");
    const payload = await response.json();
    invoiceHash = payload.invoice_hash;
    const extracted = payload.fields;

    if (extracted.patient_name) fields.patient_name.value = extracted.patient_name;
    if (extracted.address) fields.patient_address.value = extracted.address;
    if (extracted.date_of_treatment) fields.date_of_treatment.value = normalizeDate(extracted.date_of_treatment);
    if (extracted.medical_facility) fields.medical_facility.value = extracted.medical_facility;
    if (extracted.diagnosis) fields.claim_reason.value = extracted.diagnosis;
    if (extracted.amount_payable) fields.claim_amount.value = extracted.amount_payable;

    uploadStatus.textContent = "Invoice extracted. Please review the fields before submitting.";
  } catch (error) {
    uploadStatus.textContent = "Could not extract invoice automatically. Please enter the details manually.";
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  document.querySelector("#result").hidden = true;
  submitBtn.disabled = true;
  submitBtn.textContent = "Processing...";

  const data = new FormData();
  data.append("patient_name", fields.patient_name.value);
  data.append("patient_address", fields.patient_address.value);
  data.append("claim_item", fields.claim_item.value);
  data.append("date_of_treatment", fields.date_of_treatment.value);
  data.append("medical_facility", fields.medical_facility.value);
  data.append("claim_reason", fields.claim_reason.value);
  data.append("claim_amount", fields.claim_amount.value);
  data.append("invoice_hash", invoiceHash);

  if (invoiceInput.files[0]) {
    data.append("invoice", invoiceInput.files[0]);
  }

  try {
    const response = await fetch("/api/process-claim", {
      method: "POST",
      body: data,
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(errorPayload.detail || "Claim processing failed");
    }
    const result = await response.json();
    if (!matchesSubmittedClaim(result)) {
      throw new Error("The server returned a stale or mismatched claim result. Please refresh and try again.");
    }
    renderResult(result);
  } catch (error) {
    alert(error.message || "Claim processing failed. Please check the backend logs.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Process Claim";
  }
});

function matchesSubmittedClaim(result) {
  const expectedName = normalizeText(fields.patient_name.value);
  const expectedFacility = normalizeText(fields.medical_facility.value);
  const expectedAmount = Number(fields.claim_amount.value);
  return (
    normalizeText(result.patient_name) === expectedName &&
    normalizeText(result.medical_facility) === expectedFacility &&
    Number(result.total_claim_amount) === expectedAmount
  );
}

backBtn.addEventListener("click", () => {
  document.querySelector("#result").hidden = true;
  form.hidden = false;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
});

function renderResult(result) {
  const panel = document.querySelector("#result");

  document.querySelector("#r-name").textContent = result.patient_name;
  document.querySelector("#r-address").textContent = result.patient_address;
  document.querySelector("#r-claim-item").textContent = result.claim_item;
  document.querySelector("#r-facility").textContent = result.medical_facility;
  document.querySelector("#r-date").textContent = result.date_of_treatment;
  document.querySelector("#r-amount").textContent = result.total_claim_amount;
  document.querySelector("#r-executive-summary").textContent = result.executive_summary;
  document.querySelector("#r-introduction").textContent = result.introduction;
  document.querySelector("#r-description").textContent = result.claim_description;
  document.querySelector("#r-verification").textContent = result.document_verification;
  document.querySelector("#r-summary").textContent = result.document_summary;
  document.querySelector("#r-conclusion").textContent = result.conclusion;

  const citations = document.querySelector("#r-citations");
  citations.innerHTML = "";
  result.citations.forEach((citation) => {
    const node = document.createElement("div");
    node.className = "citation";
    node.innerHTML = `
      <strong>${escapeHtml(citation.section)} - ${escapeHtml(citation.title)} · page ${escapeHtml(citation.page)}</strong>
      <span>${escapeHtml(citation.excerpt)}</span>
    `;
    citations.appendChild(node);
  });

  panel.hidden = false;
  form.hidden = true;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function normalizeDate(value) {
  const trimmed = value.trim();
  const iso = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) return trimmed;
  const slash = trimmed.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!slash) return trimmed;
  const day = slash[1].padStart(2, "0");
  const month = slash[2].padStart(2, "0");
  return `${slash[3]}-${month}-${day}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}
