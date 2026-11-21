/* All research text is rendered with textContent. No model-generated HTML. */
"use strict";

const $ = (selector) => document.querySelector(selector);
const stages = ["understanding", "planning", "searching", "reviewing", "analyzing", "preparing"];
let activeRequest = null;
let currentReport = null;
let lastQuestion = "";
let toastTimer;
const scrollBehavior = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

function icon(name) {
  const node = el("i");
  node.dataset.lucide = name;
  node.setAttribute("aria-hidden", "true");
  return node;
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function showView(id) {
  for (const view of ["landing", "workspace", "report-view"]) $(`#${view}`).hidden = view !== id;
  document.body.dataset.view = id;
  $("#view-label").textContent = { landing: "New session", workspace: "In progress", "report-view": "Research brief" }[id];
}

function setSourcesOpen(open, { scroll = false } = {}) {
  $("#sources-panel").hidden = !open;
  $("#sources-toggle").setAttribute("aria-expanded", String(open));
  $("#report-grid").classList.toggle("sources-hidden", !open);
  if (open && scroll && window.matchMedia("(max-width: 1000px)").matches) {
    $("#sources-panel").scrollIntoView({ behavior: scrollBehavior(), block: "start" });
  }
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4000);
}

function resetResearch({ keepQuestion = false } = {}) {
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  currentReport = null;
  $("#start-button").disabled = false;
  $("#form-error").hidden = true;
  $("#question").removeAttribute("aria-invalid");
  lastQuestion = "";
  if (location.hash.startsWith("#source-")) history.replaceState(null, "", location.pathname + location.search);
  if (!keepQuestion) $("#question").value = "";
  showView("landing");
  $("#question").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
}

function setStage(stage, message) {
  const index = stages.indexOf(stage);
  if (index < 0) return;
  for (const [i, row] of [...document.querySelectorAll(".progress-timeline li")].entries()) {
    row.classList.toggle("completed", i < index);
    row.classList.toggle("active", i === index);
    row.classList.remove("failed");
    if (i === index) row.setAttribute("aria-current", "step");
    else row.removeAttribute("aria-current");
  }
  $("#activity-message").textContent = message;
}

function logActivity(message, iconName = "search") {
  const entry = el("p");
  entry.append(icon(iconName), el("span", "", message));
  $("#search-log").append(entry);
  refreshIcons();
}

function externalUrl(value) {
  try {
    const url = new URL(value);
    if (["https:", "http:"].includes(url.protocol) && !url.username && !url.password) return url.href;
  } catch { /* Invalid URLs never become links. */ }
  return null;
}

function sourceCard(source, { final = false, quotes = [] } = {}) {
  const card = el("div", "source-card");
  if (final) {
    card.id = `source-${source.id}`;
    card.tabIndex = -1;
  }
  const domain = el("div", "source-domain");
  domain.append(el("span", "source-number", final ? String(source.id) : ""));
  if (!final) domain.firstChild.append(icon("globe-2"));
  domain.append(el("span", "", source.domain));
  const url = externalUrl(source.url);
  const title = el(url ? "a" : "span", "source-title", source.title);
  if (url) {
    title.href = url;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    title.append(icon("arrow-up-right"));
  }
  card.append(domain, title, el("p", "", source.summary));
  if (final && url) card.append(el("span", "print-url", url));
  if (final && quotes.length) {
    const details = el("details", "source-excerpts");
    details.append(el("summary", "", "View supporting excerpts"));
    for (const quote of [...new Set(quotes)].slice(0, 6)) details.append(el("blockquote", "", quote));
    card.append(details);
  }
  return card;
}

function handleEvent(event) {
  if (event.type === "progress") {
    setStage(event.stage, event.message);
    if (event.query) logActivity(event.query);
  } else if (event.type === "plan") {
    $("#plan-preview").hidden = false;
    $("#plan-objective").textContent = event.plan.objective;
    $("#plan-questions").replaceChildren(...event.plan.subquestions.map((q) => el("li", "", q)));
  } else if (event.type === "sources") {
    $("#live-source-count").textContent = String(event.count);
    if (event.sources.length) $("#live-sources").replaceChildren(...event.sources.map((s) => sourceCard(s)));
    else $("#live-sources").replaceChildren(el("p", "sources-caption", "No relevant sources found yet. Reviewing the search coverage."));
    refreshIcons();
  } else if (event.type === "review") {
    logActivity(event.reasoning, "scan-search");
  } else if (event.type === "notice") {
    logActivity(event.message, "info");
  } else if (event.type === "error") {
    throw new Error(event.message || "Research was interrupted. Please try again.");
  } else if (event.type === "result") {
    renderReport(event.report);
    return true;
  }
  return false;
}

async function consumeStream(response, controller) {
  if (!response.body) throw new Error("This browser could not open the research stream. Try a current browser.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedReport = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (activeRequest !== controller) return;
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > 2_000_000) throw new Error("The response was too large to display. Try a narrower question.");
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.trim()) receivedReport = handleEvent(JSON.parse(line)) || receivedReport;
      }
      if (done) break;
    }
    if (buffer.trim()) receivedReport = handleEvent(JSON.parse(buffer)) || receivedReport;
    if (!receivedReport) throw new Error("The connection ended before your report was ready. Please try again.");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function showResearchError(message) {
  showView("workspace");
  $("#workspace").classList.add("is-error");
  $("#activity-title").textContent = "Research paused";
  $("#activity-message").textContent = "We couldn’t complete this research.";
  $("#workspace-error p").textContent = message;
  $("#workspace-error").hidden = false;
  $("#cancel-button").hidden = true;
  const active = $(".progress-timeline .active");
  if (active) active.classList.add("failed");
  $("#retry-button").focus({ preventScroll: true });
}

async function startResearch(question) {
  if (activeRequest) return;
  const controller = new AbortController();
  activeRequest = controller;
  currentReport = null;
  lastQuestion = question;
  $("#start-button").disabled = true;
  $("#form-error").hidden = true;
  $("#workspace-error").hidden = true;
  $("#plan-preview").hidden = true;
  $("#plan-preview").open = false;
  $(".activity-details").open = false;
  $("#cancel-button").hidden = false;
  $("#workspace").classList.remove("is-error");
  $("#workspace-title").textContent = question;
  $("#activity-title").textContent = "Following the evidence";
  $("#activity-message").textContent = "Starting your research…";
  $("#search-log").replaceChildren();
  $("#live-source-count").textContent = "0";
  const placeholder = el("div", "source-placeholder");
  placeholder.append(icon("book-open"), el("p", "", "Finding a place to start."), el("span", "", "Sources appear here as they’re discovered."));
  $("#live-sources").replaceChildren(placeholder);
  for (const row of document.querySelectorAll(".progress-timeline li")) {
    row.classList.remove("completed", "active", "failed");
    row.removeAttribute("aria-current");
  }
  showView("workspace");
  refreshIcons();
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
  $("#workspace-title").focus({ preventScroll: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 210_000);
  try {
    const response = await fetch("/api/research", {
      method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/x-ndjson" },
      body: JSON.stringify({ question }), signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `The research service could not start (${response.status}). Please try again.`);
    }
    await consumeStream(response, controller);
  } catch (error) {
    if (activeRequest !== controller) return;
    if (timedOut) showResearchError("Research took too long to respond. Please try a more focused question.");
    else if (error.name !== "AbortError") {
      const message = error instanceof SyntaxError ? "The server returned an unreadable response. Please try again." :
        error instanceof TypeError ? "We couldn’t reach the research service. Check your connection and try again." : error.message;
      showResearchError(message);
    }
  } finally {
    clearTimeout(timeout);
    if (activeRequest === controller) {
      activeRequest = null;
      $("#start-button").disabled = false;
    }
  }
}

function renderClaim(claim, tag = "p") {
  const node = el(tag);
  if (claim.kind !== "evidence") node.append(el("span", `claim-label ${claim.kind}`, claim.kind));
  node.append(document.createTextNode(claim.text));
  const used = new Set();
  for (const reference of claim.citations) {
    if (used.has(reference.source_id)) continue;
    used.add(reference.source_id);
    const source = currentReport.sources.find((s) => s.id === reference.source_id);
    if (!source) continue;
    const link = el("a", "citation", String(reference.source_id));
    link.href = `#source-${reference.source_id}`;
    link.title = reference.quote;
    link.setAttribute("aria-label", `Source ${reference.source_id}: ${source.title}`);
    node.append(link);
  }
  return node;
}

function reportSection(title, claims, { list = false, summary = false } = {}) {
  if (!claims.length) return null;
  const section = el("section", `report-section${summary ? " summary-section" : ""}`);
  const heading = el("h2");
  if (summary) heading.append(icon("sparkles"));
  heading.append(document.createTextNode(title));
  section.append(heading);
  const parent = list ? el("ul") : section;
  for (const claim of claims) parent.append(renderClaim(claim, list ? "li" : "p"));
  if (list) section.append(parent);
  return section;
}

function allClaims(report) {
  return [...report.executive_summary, ...report.key_findings,
    ...report.detailed_analysis.flatMap((s) => s.paragraphs),
    ...report.comparisons.map((c) => c.assessment), ...report.conflicting_evidence, ...report.conclusion];
}

function renderReport(report) {
  currentReport = report;
  const documentNode = $("#report-document");
  documentNode.replaceChildren();
  const kicker = el("div", "report-kicker");
  const logo = el("img");
  logo.src = "/static/assets/logo.svg";
  logo.alt = "";
  kicker.append(logo, document.createTextNode("SourcePilot Research Brief"));
  const title = el("h1", "", report.title);
  title.tabIndex = -1;
  const date = new Date(report.generated_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const meta = el("div", "report-meta");
  for (const [name, label] of [["calendar-days", date], ["files", `${report.sources.length} cited sources`], ["search", `${report.research.search_count} web searches`]]) {
    const item = el("span");
    item.append(icon(name), document.createTextNode(label));
    meta.append(item);
  }
  documentNode.append(kicker, title, meta, el("p", "report-question", `Your question: ${report.question}`));
  documentNode.append(reportSection("Executive summary", report.executive_summary, { summary: true }));
  documentNode.append(reportSection("Key findings", report.key_findings, { list: true }));
  const analysis = el("section", "report-section");
  analysis.append(el("h2", "", "Detailed analysis"));
  for (const section of report.detailed_analysis) {
    analysis.append(el("h3", "", section.heading));
    analysis.append(...section.paragraphs.map((claim) => renderClaim(claim)));
  }
  documentNode.append(analysis);
  if (report.comparisons.length) {
    const section = el("section", "report-section");
    section.append(el("h2", "", "At a glance"));
    const table = el("table", "comparison-table");
    const head = el("thead");
    const headRow = el("tr");
    for (const label of ["Option", "Evidence & trade-offs"]) {
      const th = el("th", "", label); th.scope = "col"; headRow.append(th);
    }
    head.append(headRow);
    const body = el("tbody");
    for (const comparison of report.comparisons) {
      const row = el("tr");
      const name = el("th", "", comparison.option); name.scope = "row";
      const assessment = el("td"); assessment.append(renderClaim(comparison.assessment));
      row.append(name, assessment); body.append(row);
    }
    table.append(head, body); section.append(table); documentNode.append(section);
  }
  const conflicts = reportSection("Conflicting evidence", report.conflicting_evidence);
  if (conflicts) documentNode.append(conflicts);
  documentNode.append(reportSection("Conclusion", report.conclusion));
  if (report.limitations.length) {
    const section = el("section", "report-section limitations-section");
    section.append(el("h2", "", "Gaps & limitations"));
    const list = el("ul");
    list.append(...report.limitations.map((limitation) => el("li", "", limitation)));
    section.append(list); documentNode.append(section);
  }
  const method = el("details", "report-method");
  method.append(el("summary", "", "Behind this research"), el("p", "", report.plan.objective));
  method.append(el("p", "", report.research.follow_up_reasoning));
  const queries = el("ul");
  queries.append(...report.research.queries.map((query) => el("li", "", query)));
  method.append(queries, el("p", "", `${report.research.reviewed_source_count} sources available for synthesis · ${report.research.elapsed_seconds} seconds elapsed. Citation references and supporting excerpts are checked against retrieved content; this is not independent fact verification.`));
  documentNode.append(method);

  const quotes = new Map();
  for (const claim of allClaims(report)) for (const ref of claim.citations) {
    if (!quotes.has(ref.source_id)) quotes.set(ref.source_id, []);
    quotes.get(ref.source_id).push(ref.quote);
  }
  $("#report-source-count").textContent = String(report.sources.length);
  $("#report-sources").replaceChildren(...report.sources.map((s) => sourceCard(s, { final: true, quotes: quotes.get(s.id) || [] })));
  setSourcesOpen(window.matchMedia("(min-width: 1001px)").matches);
  showView("report-view");
  refreshIcons();
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
  title.focus({ preventScroll: true });
}

function reportText(report) {
  const claimText = (claim) => `${claim.kind !== "evidence" ? `${claim.kind.toUpperCase()}: ` : ""}${claim.text}${[...new Set(claim.citations.map((r) => r.source_id))].map((id) => ` [${id}]`).join("")}`;
  const lines = [report.title, "SourcePilot AI — Research smarter. Verify everything.", `Research question: ${report.question}`, `Generated: ${new Date(report.generated_at).toLocaleString()}`, ""];
  const section = (heading, claims, bullets = false) => {
    if (claims.length) lines.push(heading.toUpperCase(), ...claims.map((c) => `${bullets ? "• " : ""}${claimText(c)}`), "");
  };
  section("Executive summary", report.executive_summary);
  section("Key findings", report.key_findings, true);
  lines.push("DETAILED ANALYSIS");
  for (const item of report.detailed_analysis) section(item.heading, item.paragraphs);
  if (report.comparisons.length) lines.push("COMPARISONS", ...report.comparisons.map((c) => `${c.option}: ${claimText(c.assessment)}`), "");
  section("Conflicting evidence", report.conflicting_evidence);
  section("Conclusion", report.conclusion);
  if (report.limitations.length) lines.push("GAPS & LIMITATIONS", ...report.limitations.map((l) => `• ${l}`), "");
  lines.push("SOURCES & REFERENCES", ...report.sources.map((s) => `[${s.id}] ${s.title}\n${s.url}`), "", "AI-assisted synthesis. Check original sources before relying on a finding.");
  return lines.join("\n\n");
}

$("#research-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (question.length < 3 || question.length > 1500) {
    $("#form-error").textContent = "Enter a research question between 3 and 1,500 characters.";
    $("#form-error").hidden = false;
    $("#question").setAttribute("aria-invalid", "true");
    $("#question").focus();
    return;
  }
  $("#question").removeAttribute("aria-invalid");
  startResearch(question);
});

$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault(); $("#research-form").requestSubmit();
  }
});
$("#question").addEventListener("input", () => {
  $("#form-error").hidden = true; $("#question").removeAttribute("aria-invalid");
});
document.querySelectorAll(".example").forEach((button) => button.addEventListener("click", () => {
  $("#question").value = button.dataset.question;
  $("#question").removeAttribute("aria-invalid");
  $("#form-error").hidden = true;
  $("#question").focus();
}));
document.querySelectorAll(".new-research").forEach((button) => button.addEventListener("click", () => resetResearch({ keepQuestion: button.textContent.trim() === "Edit question" })));
$("#cancel-button").addEventListener("click", () => { resetResearch({ keepQuestion: true }); toast("Research cancelled."); });
$("#retry-button").addEventListener("click", () => startResearch(lastQuestion));
$("#research-nav").addEventListener("click", (event) => {
  event.preventDefault();
  showView(currentReport ? "report-view" : activeRequest || lastQuestion ? "workspace" : "landing");
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
});
$("#how-nav").addEventListener("click", () => $("#how-dialog").showModal());
$("#how-close").addEventListener("click", () => $("#how-dialog").close());
$("#how-dialog").addEventListener("click", (event) => {
  const bounds = $("#how-dialog").getBoundingClientRect();
  if (event.target === $("#how-dialog") && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) $("#how-dialog").close();
});
$("#sources-toggle").addEventListener("click", () => setSourcesOpen($("#sources-panel").hidden, { scroll: true }));
$("#sources-close").addEventListener("click", () => {
  setSourcesOpen(false);
  $("#sources-toggle").focus();
});
$("#report-document").addEventListener("click", (event) => {
  const citation = event.target.closest(".citation");
  if (!citation) return;
  setSourcesOpen(true);
  const source = $(citation.getAttribute("href"));
  if (source) requestAnimationFrame(() => source.focus({ preventScroll: true }));
});
$("#copy-button").addEventListener("click", async () => {
  if (!currentReport) return;
  const text = reportText(currentReport);
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = el("textarea", "clipboard-fallback");
      area.value = text; document.body.append(area); area.select();
      const success = document.execCommand("copy"); area.remove();
      if (!success) throw new Error("Copy failed");
    }
    toast("Report copied, including source references.");
  } catch { toast("Copy was blocked by your browser. Select the report text to copy it manually."); }
});
$("#print-button").addEventListener("click", () => { if (currentReport) window.print(); });
refreshIcons();
fetch("/api/health").then((response) => response.json()).then((data) => {
  $("#config-notice").hidden = data.research_available !== false;
}).catch(() => { /* A later research request provides a useful connection error. */ });
