/* All research text is rendered with textContent. No provider-generated HTML. */
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
  const numbered = final && source.id != null;
  if (numbered) {
    card.id = `source-${source.id}`;
    card.tabIndex = -1;
  }
  const domain = el("div", "source-domain");
  domain.append(el("span", "source-number", numbered ? String(source.id) : ""));
  if (!numbered) domain.firstChild.append(icon("globe-2"));
  domain.append(el("span", "", source.domain));
  const url = externalUrl(source.url);
  const title = el(url ? "a" : "span", "source-title", source.title);
  if (url) {
    title.href = url;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    title.append(icon("arrow-up-right"));
  }
  card.append(domain, title);
  if (source.summary) card.append(el("p", "", source.summary));
  if (final && url) card.append(el("span", "print-url", url));
  if (final && quotes.length) {
    const details = el("details", "source-excerpts");
    details.append(el("summary", "", "Where it’s cited in the report"));
    for (const quote of [...new Set(quotes)].slice(0, 6)) details.append(el("blockquote", "", quote));
    card.append(details);
  }
  return card;
}

function handleEvent(event) {
  if (event.type === "progress") {
    setStage(event.stage, event.message);
    if (event.query) logActivity(event.query);
    for (const query of event.queries || []) logActivity(query);
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
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 330_000);
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

function plainText(segments) {
  return (segments || []).map((s) => s.t === "strong" || s.t === "em" ? plainText(s.c) : s.t === "cite" ? "" : s.v || "").join("").replace(/\s+/g, " ").replace(/\s+([.,;:!?)])/g, "$1").trim();
}

function renderInline(segments, parent, excerpt = null) {
  for (const segment of segments || []) {
    if (segment.t === "text") parent.append(document.createTextNode(segment.v));
    else if (segment.t === "strong" || segment.t === "em") parent.append(renderInline(segment.c, el(segment.t), excerpt));
    else if (segment.t === "code") parent.append(el("code", "", segment.v));
    else if (segment.t === "link") {
      const url = externalUrl(segment.url);
      if (!url) { parent.append(document.createTextNode(segment.v)); continue; }
      const link = el("a", "report-link", segment.v);
      link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
      parent.append(link);
    } else if (segment.t === "cite") {
      for (const id of segment.ids) {
        const source = currentReport.sources.find((s) => s.id === id);
        if (!source) { parent.append(document.createTextNode(` [${id}]`)); continue; }
        const link = el("a", "citation", String(id));
        link.href = `#source-${id}`;
        link.title = source.title;
        link.setAttribute("aria-label", `Source ${id}: ${source.title}`);
        parent.append(link);
        if (excerpt) excerpt(id);
      }
    }
  }
  return parent;
}

function renderBlock(block, excerpts) {
  const recorder = (text) => (id) => {
    if (!excerpts.has(id)) excerpts.set(id, []);
    excerpts.get(id).push(text.length > 320 ? `${text.slice(0, 317)}…` : text);
  };
  if (block.type === "heading") return renderInline(block.content, el("h3"));
  if (block.type === "paragraph") return renderInline(block.content, el("p"), recorder(plainText(block.content)));
  if (block.type === "quote") return renderInline(block.content, el("blockquote", "report-quote"), recorder(plainText(block.content)));
  if (block.type === "code") return el("pre", "report-code", block.text);
  if (block.type === "list") {
    const list = el(block.ordered ? "ol" : "ul");
    for (const item of block.items) {
      list.append(renderInline(item.content, el("li", item.level ? `nested-${item.level}` : ""), recorder(plainText(item.content))));
    }
    return list;
  }
  if (block.type === "table") {
    const wrapper = el("div", "table-scroll");
    const table = el("table", "comparison-table");
    const head = el("thead");
    const headRow = el("tr");
    for (const cell of block.header) {
      const th = renderInline(cell, el("th"));
      th.scope = "col";
      headRow.append(th);
    }
    head.append(headRow);
    const body = el("tbody");
    for (const cells of block.rows) {
      const row = el("tr");
      const record = recorder(cells.map(plainText).join(" · "));
      cells.forEach((cell, i) => {
        const node = renderInline(cell, el(i === 0 ? "th" : "td"), record);
        if (i === 0) node.scope = "row";
        row.append(node);
      });
      body.append(row);
    }
    table.append(head, body);
    wrapper.append(table);
    return wrapper;
  }
  return null;
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
  const research = report.research;
  const cited = report.sources.filter((s) => s.id != null).length;
  const meta = el("div", "report-meta");
  for (const [name, label] of [["calendar-days", date],
    ["files", cited ? `${cited} cited sources` : `${report.sources.length} sources`],
    ["search", research.search_count ? `${research.search_count} web searches` : `${research.consulted_source_count} sources consulted`]]) {
    const item = el("span");
    item.append(icon(name), document.createTextNode(label));
    meta.append(item);
  }
  documentNode.append(kicker, title, meta, el("p", "report-question", `Your question: ${report.question}`));

  const excerpts = new Map();
  let section = null;
  for (const block of report.blocks) {
    if (block.type === "heading" && block.level === 2) {
      section = el("section", "report-section");
      section.append(renderInline(block.content, el("h2")));
      documentNode.append(section);
      continue;
    }
    if (!section) {
      section = el("section", "report-section summary-section");
      const heading = el("h2");
      heading.append(icon("sparkles"), document.createTextNode("Overview"));
      section.append(heading);
      documentNode.append(section);
    }
    const node = renderBlock(block, excerpts);
    if (node) section.append(node);
  }
  if (report.limitations.length) {
    const notes = el("section", "report-section limitations-section");
    notes.append(el("h2", "", "Research notes"));
    const list = el("ul");
    list.append(...report.limitations.map((limitation) => el("li", "", limitation)));
    notes.append(list);
    documentNode.append(notes);
  }
  const method = el("details", "report-method");
  method.append(el("summary", "", "Behind this research"));
  method.append(el("p", "", `Planned, searched, analyzed, and written by the Tavily Research API (${research.model} model, ${research.mode === "poll" ? "background task" : "live stream"}).`));
  if (research.queries.length) {
    const queries = el("ul");
    queries.append(...research.queries.map((query) => el("li", "", query)));
    method.append(queries);
  }
  method.append(el("p", "", `${research.consulted_source_count} sources consulted · ${research.elapsed_seconds} seconds elapsed${research.credits ? ` · ${research.credits} Tavily credits` : ""}. Citation numbers link to the sources Tavily cited; open them to verify each finding.`));
  documentNode.append(method);

  $("#report-source-count").textContent = String(report.sources.length);
  $("#report-sources").replaceChildren(...report.sources.map((s) => sourceCard(s, { final: true, quotes: excerpts.get(s.id) || [] })));
  setSourcesOpen(window.matchMedia("(min-width: 1001px)").matches);
  showView("report-view");
  refreshIcons();
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
  title.focus({ preventScroll: true });
}

function reportText(report) {
  const lines = [report.title, "SourcePilot AI — Research smarter. Verify everything.", `Research question: ${report.question}`,
    `Generated: ${new Date(report.generated_at).toLocaleString()}`, "", report.markdown, ""];
  if (report.limitations.length) lines.push("RESEARCH NOTES", ...report.limitations.map((l) => `• ${l}`), "");
  lines.push("SOURCES & REFERENCES", ...report.sources.map((s) => `${s.id != null ? `[${s.id}]` : "•"} ${s.title}\n${s.url}`), "",
    "Researched with the Tavily Research API. Check original sources before relying on a finding.");
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
