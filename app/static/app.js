const POLL_MS = 600;
const ACTIVE = new Set(["QUEUED", "FETCHING", "PROCESSING"]);
const STATUS_TEXT = {
  QUEUED: "Queued…",
  FETCHING: "Fetching from OpenAlex…",
  PROCESSING: "Processing results…",
};
const MAX_AUTHORS = 5;

const form = document.querySelector("#search");
const statusLine = document.querySelector("#status");
const results = document.querySelector("#results");
const rows = document.querySelector("#rows");
const count = document.querySelector("#count");
const filter = document.querySelector("#filter");
const recent = document.querySelector("#recent");
const recentList = document.querySelector("#recent-list");

let currentJobId = null;
let papers = [];
let sortKey = "rank";
let sortDir = 1;

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

function setStatus(text, isError = false) {
  statusLine.textContent = text;
  statusLine.classList.toggle("error", isError);
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
    const job = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // the hashchange handler picks the job up from here
    location.hash = job.id;
  } catch (error) {
    setStatus(error.message, true);
  }
});

async function watch(jobId) {
  let job;
  try {
    job = await api(`/jobs/${jobId}`);
  } catch (error) {
    if (jobId === currentJobId) setStatus(error.message, true);
    return;
  }
  // the user moved on to another job while this request was in flight
  if (jobId !== currentJobId) return;

  if (ACTIVE.has(job.status)) {
    setStatus(STATUS_TEXT[job.status]);
    setTimeout(() => watch(jobId), POLL_MS);
    return;
  }

  if (job.status === "FAILED") {
    setStatus(`Search failed: ${job.error}`, true);
  } else {
    setStatus("");
    showResults(job);
  }
  loadRecent();
}

function openJob(jobId) {
  currentJobId = jobId;
  results.hidden = true;
  setStatus("Loading…");
  watch(jobId);
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

function showResults(job) {
  fillForm(job);
  papers = job.results.map((paper, index) => ({ ...paper, rank: index + 1 }));
  sortKey = "rank";
  sortDir = 1;
  filter.value = "";
  for (const link of document.querySelectorAll(".exports a")) {
    link.href = `/jobs/${job.id}/export?format=${link.dataset.format}`;
  }
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
  const visible = papers.filter((paper) => !needle || matches(paper, needle)).sort(compare);

  count.textContent =
    visible.length === papers.length
      ? `${papers.length} papers`
      : `${visible.length} of ${papers.length} papers`;

  for (const button of document.querySelectorAll("th button")) {
    const active = button.dataset.sort === sortKey;
    button.closest("th").ariaSort = active ? (sortDir === 1 ? "ascending" : "descending") : null;
  }

  rows.replaceChildren(...visible.map(paperRow));
  if (!visible.length) {
    rows.append(el("tr", {}, el("td", { colSpan: 6, className: "empty" }, "No papers match.")));
  }
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
    el("td", { className: "num" }, String(paper.rank)),
    main,
    el("td", { className: "num" }, String(paper.publication_year ?? "")),
    el("td", { className: "num" }, paper.cited_by_count.toLocaleString()),
    el("td", {}, paper.work_type ?? ""),
    el("td", { className: "links" }, ...links.filter(Boolean)),
  );
}

document.querySelector("thead").addEventListener("click", (event) => {
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

function describe(job) {
  const years =
    job.from_year || job.to_year ? ` · ${job.from_year ?? ""}–${job.to_year ?? ""}` : "";
  const outcome =
    job.status === "COMPLETED" ? `${job.papers_found} papers` : job.status.toLowerCase();
  return `${years} · ${outcome} · ${new Date(job.created_at).toLocaleString()}`;
}

async function loadRecent() {
  let jobs;
  try {
    jobs = await api("/jobs?limit=10");
  } catch {
    return;
  }
  recent.hidden = !jobs.length;
  recentList.replaceChildren(
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

function openFromHash() {
  const jobId = location.hash.slice(1);
  if (jobId) openJob(jobId);
}

window.addEventListener("hashchange", openFromHash);
openFromHash();
loadRecent();
