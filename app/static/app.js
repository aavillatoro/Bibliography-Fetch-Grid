const POLL_MS = 600;
const ACTIVE = new Set(["QUEUED", "FETCHING", "PROCESSING"]);
const STATUS_TEXT = {
  QUEUED: "Queued…",
  FETCHING: "Fetching from OpenAlex…",
  PROCESSING: "Processing results…",
};
const MAX_AUTHORS = 5;
const COLUMNS = 7;

const $ = (selector) => document.querySelector(selector);
const form = $("#search");
const statusLine = $("#status");
const results = $("#results");
const rows = $("#rows");
const filter = $("#filter");
const selectAll = $("#select-all");
const target = $("#target");
const addButton = $("#add-selected");
const removeButton = $("#remove-selected");

// what the grid is showing: { kind: "job" | "collection", id }
let view = null;
let collectionName = null;
let papers = [];
let visible = [];
let sortKey = "rank";
let sortDir = 1;
const selected = new Set();

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || response.statusText;
    throw new Error(message);
  }
  return body;
}

function send(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function setStatus(text, isError = false) {
  statusLine.textContent = text;
  statusLine.classList.toggle("error", isError);
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const payload = {
    query: data.get("query"),
    limit: Number(data.get("limit")),
    sort: data.get("sort"),
  };
  for (const key of ["from_year", "to_year"]) {
    if (data.get(key)) payload[key] = Number(data.get(key));
  }

  try {
    const job = await api("/jobs", send("POST", payload));
    // the hashchange handler picks the job up from here
    location.hash = job.id;
  } catch (error) {
    setStatus(error.message, true);
  }
});

function openFromHash() {
  const hash = location.hash.slice(1);
  results.hidden = true;
  setStatus(hash ? "Loading…" : "");
  if (!hash) {
    view = null;
  } else if (hash.startsWith("c/")) {
    view = { kind: "collection", id: hash.slice(2) };
  } else {
    view = { kind: "job", id: hash };
  }
  if (view) load(view);
}

async function load(wanted) {
  let data;
  try {
    data = await api(`/${wanted.kind}s/${wanted.id}`);
  } catch (error) {
    if (wanted === view) setStatus(error.message, true);
    return;
  }
  // the user moved on while this request was in flight
  if (wanted !== view) return;

  if (wanted.kind === "collection") {
    setStatus("");
    showGrid(data.papers, data.name);
    return;
  }

  if (ACTIVE.has(data.status)) {
    setStatus(STATUS_TEXT[data.status]);
    setTimeout(() => load(wanted), POLL_MS);
    return;
  }

  if (data.status === "FAILED") {
    setStatus(`Search failed: ${data.error}`, true);
  } else {
    setStatus("");
    fillForm(data);
    showGrid(data.results, null);
  }
  loadRecent();
}

function fillForm(job) {
  form.query.value = job.query;
  form.from_year.value = job.from_year ?? "";
  form.to_year.value = job.to_year ?? "";
  form.sort.value = job.sort;
  if (![...form.limit.options].some((option) => Number(option.value) === job.limit)) {
    form.limit.add(new Option(job.limit));
  }
  form.limit.value = job.limit;
}

function showGrid(list, name) {
  papers = list.map((paper, index) => ({ ...paper, rank: index + 1 }));
  collectionName = name;
  sortKey = "rank";
  sortDir = 1;
  filter.value = "";
  selected.clear();

  for (const link of document.querySelectorAll(".exports a")) {
    link.href = `/${view.kind}s/${view.id}/export?format=${link.dataset.format}`;
  }
  $("#collection-bar").hidden = name === null;
  $("#collection-name").textContent = name ?? "";
  removeButton.hidden = name === null;
  results.hidden = false;
  render();
}

function compare(a, b) {
  const x = a[sortKey];
  const y = b[sortKey];
  // missing values sort last in both directions
  if (x == null || y == null) return (x == null) - (y == null);
  const order = typeof x === "string" ? x.localeCompare(y) : x - y;
  return order * sortDir;
}

function matches(paper, needle) {
  return [paper.title, paper.venue, ...paper.authors]
    .some((text) => text?.toLowerCase().includes(needle));
}

function render() {
  const needle = filter.value.trim().toLowerCase();
  visible = papers.filter((paper) => !needle || matches(paper, needle)).sort(compare);

  $("#count").textContent =
    visible.length === papers.length
      ? plural(papers.length, "paper")
      : `${visible.length} of ${plural(papers.length, "paper")}`;

  for (const button of document.querySelectorAll("th button")) {
    const active = button.dataset.sort === sortKey;
    button.closest("th").ariaSort = active ? (sortDir === 1 ? "ascending" : "descending") : null;
  }

  rows.replaceChildren(...visible.map(paperRow));
  if (!visible.length) {
    const text = papers.length ? "No papers match." : "No papers here yet.";
    rows.append(el("tr", {}, el("td", { colSpan: COLUMNS, className: "empty" }, text)));
  }
  renderSelection();
}

function renderSelection() {
  $("#selected-count").textContent = `${selected.size} selected`;
  addButton.disabled = removeButton.disabled = !selected.size;
  selectAll.checked =
    visible.length > 0 && visible.every((paper) => selected.has(paper.source_id));
}

// upstream urls are untrusted, so only link http(s)
function safeLink(url, text) {
  if (!/^https?:\/\//i.test(url || "")) return null;
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text);
}

function authorLine(authors) {
  if (authors.length <= MAX_AUTHORS) return authors.join(", ");
  return `${authors.slice(0, MAX_AUTHORS).join(", ")} +${authors.length - MAX_AUTHORS} more`;
}

function paperRow(paper) {
  const title = paper.title || "Untitled";

  const checkbox = el("input", {
    type: "checkbox",
    checked: selected.has(paper.source_id),
    ariaLabel: `Select ${title}`,
  });
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selected.add(paper.source_id);
    else selected.delete(paper.source_id);
    renderSelection();
  });

  const main = el("td", {}, el("div", { className: "title" }, safeLink(paper.source_id, title) || title));
  if (paper.authors.length) {
    main.append(el("div", { className: "meta", title: paper.authors.join(", ") }, authorLine(paper.authors)));
  }
  if (paper.venue) main.append(el("div", { className: "meta venue" }, paper.venue));
  if (paper.abstract) {
    main.append(el("details", {}, el("summary", {}, "Abstract"), el("p", {}, paper.abstract)));
  }

  const links = [safeLink(paper.doi, "DOI"), safeLink(paper.open_access_url, "Open access")];

  return el(
    "tr",
    {},
    el("td", {}, checkbox),
    el("td", { className: "num" }, String(paper.rank)),
    main,
    el("td", { className: "num" }, String(paper.publication_year ?? "")),
    el("td", { className: "num" }, paper.cited_by_count.toLocaleString()),
    el("td", { className: "nowrap" }, paper.work_type ?? ""),
    el("td", { className: "nowrap links" }, ...links.filter(Boolean)),
  );
}

$("thead").addEventListener("click", (event) => {
  const key = event.target.dataset?.sort;
  if (!key) return;
  if (key === sortKey) {
    sortDir = -sortDir;
  } else {
    sortKey = key;
    // numbers are more useful biggest first, except rank
    sortDir = key === "publication_year" || key === "cited_by_count" ? -1 : 1;
  }
  render();
});

filter.addEventListener("input", render);

selectAll.addEventListener("change", () => {
  for (const paper of visible) {
    if (selectAll.checked) selected.add(paper.source_id);
    else selected.delete(paper.source_id);
  }
  render();
});

addButton.addEventListener("click", async () => {
  let id = target.value;
  let name = target.selectedOptions[0].text;
  try {
    if (!id) {
      name = prompt("Name for the new collection")?.trim();
      if (!name) return;
      id = (await api("/collections", send("POST", { name }))).id;
    }
    const count = selected.size;
    await api(`/collections/${id}/papers`, send("POST", { source_ids: [...selected] }));
    selected.clear();
    render();
    setStatus(`Saved ${plural(count, "paper")} to “${name}”.`);
  } catch (error) {
    setStatus(error.message, true);
  }
  await loadCollections();
  if (id) target.value = id;
});

removeButton.addEventListener("click", async () => {
  const query = new URLSearchParams([...selected].map((id) => ["source_id", id]));
  try {
    await api(`/collections/${view.id}/papers?${query}`, { method: "DELETE" });
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  load(view);
  loadCollections();
});

$("#rename-collection").addEventListener("click", async () => {
  const name = prompt("New name", collectionName)?.trim();
  if (!name || name === collectionName) return;
  try {
    await api(`/collections/${view.id}`, send("PATCH", { name }));
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  collectionName = name;
  $("#collection-name").textContent = name;
  loadCollections();
});

$("#delete-collection").addEventListener("click", async () => {
  if (!confirm(`Delete the collection “${collectionName}”? The papers stay in your searches.`)) return;
  try {
    await api(`/collections/${view.id}`, { method: "DELETE" });
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  location.hash = "";
  loadCollections();
});

async function loadCollections() {
  let collections;
  try {
    collections = await api("/collections");
  } catch {
    return;
  }
  const previous = target.value;
  target.replaceChildren(
    ...collections.map((collection) => new Option(collection.name, collection.id)),
    new Option("New collection…", ""),
  );
  if (collections.some((collection) => collection.id === previous)) target.value = previous;

  $("#collections").hidden = !collections.length;
  $("#collection-list").replaceChildren(
    ...collections.map((collection) =>
      el(
        "li",
        {},
        el("a", { href: `#c/${collection.id}` }, collection.name),
        el("span", { className: "meta" }, ` · ${plural(collection.paper_count, "paper")}`),
      ),
    ),
  );
}

function describe(job) {
  const years =
    job.from_year || job.to_year ? ` · ${job.from_year ?? ""}–${job.to_year ?? ""}` : "";
  const outcome =
    job.status === "COMPLETED" ? plural(job.papers_found, "paper") : job.status.toLowerCase();
  return `${years} · ${outcome} · ${new Date(job.created_at).toLocaleString()}`;
}

async function loadRecent() {
  let jobs;
  try {
    jobs = await api("/jobs?limit=10");
  } catch {
    return;
  }
  $("#recent").hidden = !jobs.length;
  $("#recent-list").replaceChildren(
    ...jobs.map((job) =>
      el(
        "li",
        {},
        el("a", { href: `#${job.id}` }, job.query),
        el("span", { className: "meta" }, describe(job)),
      ),
    ),
  );
}

window.addEventListener("hashchange", openFromHash);
openFromHash();
loadCollections();
loadRecent();
