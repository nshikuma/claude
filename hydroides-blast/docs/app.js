/* Hydroides elegans BLAST — static front end.
 *
 * How a search works:
 *   1. The form builds a GitHub issue (title "BLAST: ...", body in the same
 *      "### Heading" layout as the issue form) and opens GitHub's new-issue
 *      page pre-filled with it. The user clicks "Create".
 *   2. The "Run BLAST" workflow runs NCBI BLAST+ and pushes
 *      results/<issue>/result.json to the results branch, then labels the
 *      issue "blast: done" (or "blast: failed").
 *   3. This page polls the issue's labels through the GitHub API and, when
 *      done, reads the result from raw.githubusercontent.com (which does not
 *      count against the API rate limit).
 */
(() => {
  "use strict";

  // ---------------------------------------------------------------- config
  const CFG = Object.assign({ title: "Hydroides elegans BLAST", species: "Hydroides elegans",
    resultsBranch: "blast-results", labName: "", repo: "" }, window.BLAST_CONFIG || {});
  if (!CFG.repo) {
    const host = location.hostname.match(/^([^.]+)\.github\.io$/i);
    const first = location.pathname.split("/").filter(Boolean)[0];
    if (host) CFG.repo = host[1] + "/" + (first || host[0]);
  }
  const API = "https://api.github.com/repos/" + CFG.repo;
  const RAW = "https://raw.githubusercontent.com/" + CFG.repo + "/" + CFG.resultsBranch;
  const SITE = location.origin + location.pathname.replace(/index\.html$/, "");

  const PROGRAMS = {
    blastn: { q: "nucleotide", dbs: ["genome", "transcripts"], name: "blastn",
      desc: "DNA vs DNA. Find where your sequence is in the genome." },
    tblastx: { q: "nucleotide", dbs: ["genome", "transcripts"], name: "tblastx",
      desc: "Translated DNA vs translated DNA. Very distant matches; slow." },
    blastx: { q: "nucleotide", dbs: ["proteins"], name: "blastx",
      desc: "Translated DNA vs proteins. Which protein does my DNA encode?" },
    tblastn: { q: "protein", dbs: ["genome", "transcripts"], name: "tblastn",
      desc: "Protein vs translated DNA. Find genes, including unannotated ones." },
    blastp: { q: "protein", dbs: ["proteins"], name: "blastp",
      desc: "Protein vs proteins. Find homologs of your protein." },
  };
  const DEFAULT_PROGRAM = { "nucleotide:genome": "blastn", "nucleotide:transcripts": "blastn",
    "nucleotide:proteins": "blastx", "protein:genome": "tblastn", "protein:transcripts": "tblastn",
    "protein:proteins": "blastp" };
  const DBS = {
    genome: { label: "Genome assembly", desc: "All scaffolds. Hits show overlapping genes." },
    transcripts: { label: "Transcripts", desc: "Spliced mRNAs from the gene annotation." },
    proteins: { label: "Proteins", desc: "Predicted proteins from the gene annotation." },
  };
  const LIMITS = { blastn: 200000, blastx: 50000, tblastx: 10000, tblastn: 20000, blastp: 50000 };
  const MAX_SEQS = 25;

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n).toLocaleString("en-US");
  const bp = (n) => n >= 1e9 ? (n / 1e9).toFixed(2) + " Gb" : n >= 1e6 ? (n / 1e6).toFixed(1) + " Mb" : n >= 1e3 ? (n / 1e3).toFixed(1) + " kb" : n + " bp";
  const fmtE = (e) => e === 0 ? "0.0" : e < 0.01 ? e.toExponential(1).replace("e", "e") : e.toPrecision(2);

  let INFO = null;          // info.json from the results branch
  const state = { program: null, db: "genome", queryType: null };

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200);
  }
  function store(key, val) {
    try { if (val === undefined) return JSON.parse(localStorage.getItem(key) || "null"); localStorage.setItem(key, JSON.stringify(val)); }
    catch (e) { return null; }
  }
  async function copy(text, what) {
    try { await navigator.clipboard.writeText(text); toast((what || "Text") + " copied"); }
    catch (e) { window.prompt("Copy this:", text); }
  }
  function download(name, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---------------------------------------------------------------- GitHub
  class RateLimited extends Error {}
  async function api(path) {
    const r = await fetch(API + path, { headers: { Accept: "application/vnd.github+json" }, cache: "no-store" });
    if ((r.status === 403 || r.status === 429) && r.headers.get("x-ratelimit-remaining") === "0") {
      const reset = new Date(1000 * Number(r.headers.get("x-ratelimit-reset")));
      throw new RateLimited("GitHub limits how often a browser can check for updates (60 checks an hour). "
        + "Checking will resume at " + reset.toLocaleTimeString() + ". Your search is still running; results will also be posted on the GitHub issue.");
    }
    if (!r.ok) { const e = new Error("GitHub API " + r.status); e.status = r.status; throw e; }
    return r.json();
  }
  async function rawJSON(path, bust) {
    // raw.githubusercontent.com is cached for up to 5 minutes; a query string
    // gets a fresh copy for files that were only just written.
    const r = await fetch(RAW + "/" + path + (bust ? "?t=" + Date.now() : ""), { cache: "no-store" });
    if (!r.ok) { const e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
    return r.json();
  }
  async function rawText(path) {
    const r = await fetch(RAW + "/" + path, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.text();
  }
  const isDemo = (id) => String(id).startsWith("demo-");
  const demoPath = (id) => "demo/" + id.slice(5) + "/";

  // ---------------------------------------------------------------- sequences
  const NUC = /^[ACGTUNRYKMSWBDHV\-]+$/;
  const PROT = /^[ABCDEFGHIKLMNPQRSTUVWXYZ*\-]+$/;
  function parseFasta(text) {
    const recs = []; let cur = null;
    for (let line of text.replace(/\r/g, "").split("\n")) {
      line = line.trim();
      if (!line || line.startsWith(";")) continue;
      if (line.startsWith(">")) { cur = { name: line.slice(1).trim(), seq: "" }; recs.push(cur); }
      else {
        if (!cur) { cur = { name: "", seq: "" }; recs.push(cur); }
        cur.seq += line.replace(/[\s\d]/g, "").toUpperCase();
      }
    }
    recs.forEach((r, i) => { if (!r.name) r.name = "Query_" + (i + 1); });
    return recs;
  }
  function seqType(seq) {
    const s = seq.replace(/-/g, "");
    if (!s) return "empty";
    const nuc = (s.match(/[ACGTUN]/g) || []).length;
    if (nuc / s.length >= 0.9 && NUC.test(s)) return "nucleotide";
    if (PROT.test(s)) return "protein";
    return "invalid";
  }
  function analyseQuery() {
    const recs = parseFasta($("#query").value);
    const out = { recs, type: null, errors: [], total: 0 };
    if (!recs.length) return out;
    const types = new Set();
    for (const r of recs) {
      const t = seqType(r.seq);
      out.total += r.seq.length;
      if (t === "empty") out.errors.push(`“${r.name}” has no sequence.`);
      else if (t === "invalid") {
        const bad = [...new Set(r.seq.replace(/[ABCDEFGHIKLMNPQRSTUVWXYZ*\-]/g, ""))].join(" ");
        out.errors.push(`“${r.name}” contains characters that aren't sequence letters: ${bad}`);
      } else types.add(t);
    }
    if (types.size > 1) out.errors.push("Mix of DNA and protein sequences. Search them separately.");
    if (recs.length > MAX_SEQS) out.errors.push(`Too many sequences (${recs.length}); the limit is ${MAX_SEQS} per search.`);
    if (types.size === 1) out.type = [...types][0];
    return out;
  }

  // ---------------------------------------------------------------- form
  function renderDbCards() {
    const el = $("#db-cards");
    el.innerHTML = Object.entries(DBS).map(([key, d]) => {
      const i = INFO && INFO.databases[key];
      const unavailable = INFO && !i;
      let stats = "";
      if (i && key === "genome") stats = `${fmt(i.sequences)} scaffolds · ${bp(i.total_length)}${i.genes ? " · " + fmt(i.genes) + " genes" : ""}`;
      else if (i) stats = `${fmt(i.sequences)} sequences`;
      else if (unavailable) stats = "Not available (no annotation provided)";
      return `<label class="card${state.db === key ? " selected" : ""}${unavailable ? " disabled" : ""}">
        <input type="radio" name="db" value="${key}" ${state.db === key ? "checked" : ""} ${unavailable ? "disabled" : ""}>
        <div class="card-title">${d.label}</div>
        <div class="card-desc">${d.desc}</div>
        ${stats ? `<div class="card-stats">${stats}</div>` : ""}
      </label>`;
    }).join("");
  }

  function renderProgramCards() {
    const q = state.queryType;
    const valid = Object.entries(PROGRAMS).filter(([, p]) => p.dbs.includes(state.db) && (!q || p.q === q));
    if (!valid.some(([k]) => k === state.program)) {
      state.program = q ? DEFAULT_PROGRAM[q + ":" + state.db] : valid[0][0];
    }
    const rec = q && DEFAULT_PROGRAM[q + ":" + state.db];
    $("#program-cards").innerHTML = valid.map(([key, p]) => `
      <label class="card${state.program === key ? " selected" : ""}">
        <input type="radio" name="program" value="${key}" ${state.program === key ? "checked" : ""}>
        <div class="card-title"><code>${p.name}</code>${key === rec ? '<span class="badge">Recommended</span>' : ""}
          ${!q ? `<span class="badge gray">${p.q} query</span>` : ""}</div>
        <div class="card-desc">${p.desc}</div>
      </label>`).join("");
    $("#task-row").hidden = state.program !== "blastn";
    $("#matrix-label").style.display = state.program === "blastn" ? "none" : "";
    updateSummary();
  }

  function updateQueryStatus() {
    const a = analyseQuery();
    const el = $("#query-status");
    if (!a.recs.length) { el.innerHTML = ""; state.queryType = null; }
    else {
      const parts = [];
      if (a.type) {
        parts.push(`<span class="pill">✓ ${a.recs.length} ${a.type === "nucleotide" ? "DNA" : "protein"} sequence${a.recs.length > 1 ? "s" : ""} · ${fmt(a.total)} ${a.type === "nucleotide" ? "bp" : "aa"}</span>`);
        if (state.program && PROGRAMS[state.program].q === a.type && a.total > LIMITS[state.program])
          parts.push(`<span class="pill warn">Over the ${fmt(LIMITS[state.program])} limit for ${state.program}</span>`);
        if (a.type === "nucleotide" && a.recs.some((r) => r.seq.length < 30) && state.program === "blastn")
          parts.push(`<span class="pill warn">Short sequence: optimised for primers/short reads automatically</span>`);
      }
      a.errors.forEach((e) => parts.push(`<span class="pill err">${esc(e)}</span>`));
      el.innerHTML = parts.join("");
      state.queryType = a.errors.length ? (a.type || null) : a.type;
      if (a.type) {
        // Nudge the database choice to something that works with this query type.
        const ok = Object.values(PROGRAMS).some((p) => p.q === a.type && p.dbs.includes(state.db));
        if (!ok) state.db = "genome";
      }
    }
    renderDbCards();
    renderProgramCards();
    return a;
  }

  function updateSummary() {
    const a = analyseQuery();
    const btn = $("#submit-btn");
    let problem = "";
    if (!a.recs.length) problem = "Enter a query sequence to start.";
    else if (a.errors.length) problem = "Fix the query first.";
    else if (a.type !== PROGRAMS[state.program].q) problem = `${state.program} needs a ${PROGRAMS[state.program].q} query.`;
    else if (a.total > LIMITS[state.program]) problem = `Query too long for ${state.program} (limit ${fmt(LIMITS[state.program])}).`;
    btn.disabled = !!problem;
    $("#submit-summary").innerHTML = problem ? esc(problem)
      : `<b>${state.program}</b>${state.program === "blastn" ? " (" + $("input[name=task]:checked").value + ")" : ""} · ${a.recs.length} quer${a.recs.length > 1 ? "ies" : "y"} vs <b>${DBS[state.db].label.toLowerCase()}</b>`;
  }

  function buildIssue() {
    const a = analyseQuery();
    const token = Math.random().toString(36).slice(2, 8);
    const prog = state.program;
    const clean = (s) => s.replace(/[\r\n#`<>]/g, " ").trim();
    const title = clean($("#jobtitle").value);
    const first = clean(a.recs[0].name).split(/\s+/)[0].slice(0, 40);
    const issueTitle = `BLAST: ${prog} vs ${state.db} — ${title || first + (a.recs.length > 1 ? " +" + (a.recs.length - 1) : "")} [${token}]`;
    const fasta = a.recs.map((r) => ">" + clean(r.name) + "\n" + r.seq.replace(/(.{80})/g, "$1\n").trim()).join("\n");
    const sec = (h, v) => `### ${h}\n\n${v}\n`;
    let body = "<!-- Submitted from the BLAST website. Click Create below to run the search; please don't edit the text. -->\n\n";
    body += sec("Program", prog) + "\n" + sec("Database", state.db) + "\n";
    if (prog === "blastn") body += sec("blastn task", $("input[name=task]:checked").value) + "\n";
    else body += sec("Matrix", $("#matrix").value) + "\n";
    body += sec("E-value", $("#evalue").value) + "\n" + sec("Max hits", $("#maxhits").value) + "\n";
    body += sec("Low-complexity filter", $("#filter").checked ? "yes" : "no") + "\n";
    if (title) body += sec("Job title", title) + "\n";
    body += sec("Query sequences", "```fasta\n" + fasta + "\n```");
    return { token, title: issueTitle, body };
  }

  function submit(ev) {
    ev.preventDefault();
    updateSummary();
    if ($("#submit-btn").disabled) return;
    if (!CFG.repo) { alert("This page isn't connected to a GitHub repository yet. Set `repo` in config.js."); return; }
    const issue = buildIssue();
    let url = `https://github.com/${CFG.repo}/issues/new?title=${encodeURIComponent(issue.title)}&body=${encodeURIComponent(issue.body)}`;
    const note = $("#paste-note");
    note.hidden = true;
    if (url.length > 7500) {
      // Too long for a URL: put the request on the clipboard and ask for a paste.
      copy(issue.body, "Search request");
      url = `https://github.com/${CFG.repo}/issues/new?title=${encodeURIComponent(issue.title)}&body=${encodeURIComponent("PASTE HERE: select this text and press Ctrl+V / ⌘V")}`;
      note.innerHTML = "<b>Your sequences are long, so they were copied to your clipboard.</b> On GitHub, replace the text in the description box by pasting (Ctrl+V / ⌘V), then click Create.";
      note.hidden = false;
    }
    const pending = { token: issue.token, title: issue.title, created: Date.now() };
    store("blast.pending", pending);
    store("blast.draft", null);
    window.open(url, "_blank", "noopener");
    $("#reopen-link").href = url;
    location.hash = "#/submitted";
  }

  function fillForm(req, fasta) {
    $("#query").value = fasta || "";
    if (req) {
      state.db = req.database; state.program = req.program;
      if (req.task) { const r = $(`input[name=task][value="${req.task}"]`); if (r) r.checked = true; }
      if (req.matrix) $("#matrix").value = req.matrix;
      const setSel = (sel, v) => { const o = [...$(sel).options].find((o) => Number(o.value) === Number(v)); if (o) $(sel).value = o.value; else { $(sel).add(new Option(v, v, true, true)); } };
      if (req.evalue != null) setSel("#evalue", req.evalue);
      if (req.max_hits) setSel("#maxhits", req.max_hits);
      $("#filter").checked = req.filter !== false;
      $("#jobtitle").value = req.title || "";
    }
    updateQueryStatus();
  }

  function initForm() {
    const q = $("#query");
    const draft = store("blast.draft");
    if (draft && draft.query) q.value = draft.query;
    q.addEventListener("input", () => { updateQueryStatus(); store("blast.draft", { query: q.value }); });
    $("#db-cards").addEventListener("change", (e) => { state.db = e.target.value; renderDbCards(); renderProgramCards(); updateQueryStatus(); });
    $("#program-cards").addEventListener("change", (e) => { state.program = e.target.value; renderProgramCards(); updateQueryStatus(); });
    $("#task-seg").addEventListener("change", updateSummary);
    $("#clear-btn").addEventListener("click", () => { q.value = ""; store("blast.draft", null); updateQueryStatus(); q.focus(); });
    $("#file-input").addEventListener("change", async (e) => {
      const f = e.target.files[0]; if (!f) return;
      if (f.size > 2e6) { alert("That file is larger than 2 MB. BLAST searches here are limited to 25 sequences."); return; }
      q.value = await f.text(); e.target.value = ""; updateQueryStatus();
    });
    $$("[data-example]").forEach((b) => b.addEventListener("click", () => {
      const kind = b.dataset.example;
      const ex = INFO && INFO.examples && INFO.examples[kind];
      if (!ex && !FALLBACK_EXAMPLES[kind]) { toast("A DNA example appears once the genome databases are built"); return; }
      q.value = ex ? `>${ex.id} (example from this genome)\n${ex.seq.replace(/(.{70})/g, "$1\n").trim()}` : FALLBACK_EXAMPLES[kind];
      state.db = kind === "protein" ? (INFO && !INFO.databases.proteins ? "genome" : "proteins") : "genome";
      state.program = null;
      updateQueryStatus();
    }));
    $("#search-form").addEventListener("submit", submit);
    $("#manual-job").addEventListener("submit", (e) => {
      e.preventDefault(); const n = $("#manual-job-num").value; if (n) location.hash = "#/job/" + n;
    });
    updateQueryStatus();
  }

  // Histone H3 is nearly identical in all animals, so it makes a safe example
  // before the genome-specific examples are available.
  const FALLBACK_EXAMPLES = {
    protein: ">histone_H3 (example; highly conserved in all animals)\nMARTKQTARKSTGGKAPRKQLATKAARKSAPATGGVKKPHRYRPGTVALREIRRYQKSTELLIRKLPFQRLVREIAQDF\nKTDLRFQSSAVMALQEASEAYLVGLFEDTNLCAIHAKRVTIMPKDIQLARRIRGERA",
  };

  // ---------------------------------------------------------------- waiting for issue creation
  let pollTimer = null;
  function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

  function showSubmitted() {
    const p = store("blast.pending");
    if (!p) { location.hash = "#/"; return; }
    if (!$("#reopen-link").getAttribute("href")) $("#reopen-link").hidden = true;
    let tries = 0;
    const since = new Date(p.created - 60000).toISOString();
    const tick = async () => {
      tries++;
      try {
        const issues = await api(`/issues?state=all&sort=created&direction=desc&per_page=30&since=${since}`);
        const hit = issues.find((i) => i.title.includes("[" + p.token + "]"));
        if (hit) {
          store("blast.pending", null);
          rememberJob(hit.number, p.title);
          location.hash = "#/job/" + hit.number;
          return;
        }
        $("#waiting-text").textContent = "Waiting for the search to be created on GitHub…";
      } catch (e) {
        $("#waiting-text").textContent = e instanceof RateLimited ? e.message : "Couldn't reach GitHub; retrying…";
      }
      if (Date.now() - p.created > 30 * 60000) { $("#waiting-text").textContent = "Stopped waiting. If you created the search, open it from the Searches page."; return; }
      pollTimer = setTimeout(tick, tries < 12 ? 8000 : 20000);
    };
    pollTimer = setTimeout(tick, 4000);
  }

  function rememberJob(n, title) {
    const jobs = store("blast.jobs") || [];
    if (!jobs.some((j) => j.n === n)) jobs.unshift({ n, title: title.replace(/^BLAST:\s*/, "").replace(/\s*\[\w+\]$/, ""), at: Date.now() });
    store("blast.jobs", jobs.slice(0, 50));
  }

  // ---------------------------------------------------------------- job page
  function issueStatus(issue) {
    const labels = issue.labels.map((l) => (typeof l === "string" ? l : l.name));
    if (labels.includes("blast: done")) return "done";
    if (labels.includes("blast: failed")) return "failed";
    if (labels.includes("blast: running")) return "running";
    if (issue.state === "closed") return "closed";
    return "queued";
  }

  async function showJob(id) {
    const view = $("#view-job");
    view.innerHTML = `<div class="panel center"><span class="spinner"></span> Loading search ${esc(id)}…</div>`;
    if (isDemo(id)) {
      try {
        const res = await (await fetch(demoPath(id) + "result.json")).json();
        return renderResult(res, id);
      } catch (e) { view.innerHTML = `<div class="notice error">Demo not found.</div>`; return; }
    }
    let started = Date.now(), tries = 0;
    const tick = async () => {
      if (!location.hash.startsWith("#/job/" + id)) return;
      tries++;
      let issue;
      try { issue = await api("/issues/" + id); }
      catch (e) {
        if (e.status === 404) { view.innerHTML = `<div class="notice error">There is no search #${esc(id)}.</div>`; return; }
        renderWaiting(view, id, null, e instanceof RateLimited ? e.message : "Couldn't reach GitHub; retrying…");
        pollTimer = setTimeout(tick, e instanceof RateLimited ? 60000 : 15000); return;
      }
      if (issue.pull_request || !/^BLAST/.test(issue.title)) { view.innerHTML = `<div class="notice error">#${esc(id)} is not a BLAST search.</div>`; return; }
      rememberJob(issue.number, issue.title);
      const st = issueStatus(issue);
      if (st === "done" || st === "failed") {
        try { return renderResult(await rawJSON(`results/${id}/result.json`, true), id, issue); }
        catch (e) {
          if (st === "failed") return renderFailure(view, id, issue, null);
          // Just finished: the raw file can lag the label by a few seconds.
          if (tries < 40) { renderWaiting(view, id, issue, "Finishing up…"); pollTimer = setTimeout(tick, 5000); return; }
          view.innerHTML = `<div class="notice error">The search finished but its results couldn't be loaded. <a href="${esc(issue.html_url)}">See the GitHub issue</a>.</div>`;
          return;
        }
      }
      if (st === "closed") return renderFailure(view, id, issue, "This search was closed without results.");
      renderWaiting(view, id, issue);
      const age = Date.now() - started;
      pollTimer = setTimeout(tick, age < 5 * 60000 ? 12000 : 30000);
    };
    tick();
  }

  function renderWaiting(view, id, issue, msg) {
    const st = issue ? issueStatus(issue) : "queued";
    const since = issue ? Math.round((Date.now() - new Date(issue.created_at)) / 1000) : 0;
    const el = since > 3600 ? Math.round(since / 3600) + " h" : since > 60 ? Math.floor(since / 60) + " min " + (since % 60) + " s" : since + " s";
    view.innerHTML = `<div class="panel center">
      <h2>Search #${esc(id)} is ${st === "running" ? "running" : "queued"}</h2>
      ${issue ? `<p class="muted">${esc(issue.title.replace(/^BLAST:\s*/, "").replace(/\s*\[\w+\]$/, ""))}</p>` : ""}
      <ol class="progress-steps">
        <li class="done">Submitted</li>
        <li class="${st === "running" ? "done" : "now"}">Waiting for a GitHub server</li>
        <li class="${st === "running" ? "now" : ""}">Running BLAST</li>
        <li>Results</li>
      </ol>
      <p class="muted small">${msg ? esc(msg) + "<br>" : ""}Started ${el} ago. Most searches take 1–3 minutes; the first search after a genome update takes longer while databases are built.<br>
      This page updates by itself. You can also close it: results will be posted on the <a href="${issue ? esc(issue.html_url) : "#"}" target="_blank" rel="noopener">GitHub issue</a>.</p>
    </div>`;
  }

  function renderFailure(view, id, issue, msg) {
    view.innerHTML = `<div class="panel center"><h2>Search #${esc(id)} didn't run</h2>
      <div class="notice error" style="text-align:left">${msg ? esc(msg) : "Something went wrong on the server."}</div>
      <p><a class="btn" href="${esc(issue.html_url)}" target="_blank" rel="noopener">See details on GitHub</a> <a class="btn btn-primary" href="#/">New search</a></p></div>`;
  }

  // ---------------------------------------------------------------- results
  const scoreClass = (s) => s >= 200 ? 5 : s >= 80 ? 4 : s >= 50 ? 3 : s >= 40 ? 2 : 1;
  const geneName = (g) => g.name || g.id;
  function hitGeneText(res, hit, hsp) {
    if (hit.gene) return geneName(hit.gene) + (hit.gene.desc ? " — " + hit.gene.desc : "");
    if (!hsp) {
      // Whole hit: every gene any alignment touches; else the best one's nearest gene.
      const seen = new Map();
      hit.hsps.forEach((p) => p.genes && p.genes.overlap.forEach((x) => seen.set(x.id, x)));
      if (seen.size) return [...seen.values()].map((x) => geneName(x) + (x.desc ? " (" + x.desc + ")" : "")).join("; ")
        + (hit.hsps.some((p) => p.genes && !p.genes.overlap.length) ? " + intergenic alignments" : "");
    }
    const g = (hsp || hit.hsps[0]).genes;
    if (!g) return "";
    if (g.overlap.length) return g.overlap.map((x) => geneName(x) + (x.desc ? " (" + x.desc + ")" : "")).join("; ");
    if (g.nearest) return `intergenic · nearest: ${geneName(g.nearest)}${g.nearest.desc ? " (" + g.nearest.desc + ")" : ""}, ${bp(g.nearest.distance)} away`;
    return "";
  }
  function hitLocation(hit) {
    // Location of the best alignment; other alignments may be far away on a long scaffold.
    const p = hit.hsps[0], lo = Math.min(p.hit_from, p.hit_to), hi = Math.max(p.hit_from, p.hit_to);
    const more = hit.hsp_count > 1 ? ` (+${hit.hsp_count - 1} more)` : "";
    return { lo, hi, text: `${hit.id}:${fmt(lo)}-${fmt(hi)}${more}`, plain: `${hit.id}:${lo}-${hi}` };
  }
  function strandText(program, p) {
    const f = (x) => (x > 0 ? "+" : "") + x;
    if (program === "blastn") return "Strand: Plus/" + (p.hit_to < p.hit_from ? "Minus" : "Plus");
    if (program === "blastx") return "Frame: " + f(p.query_frame);
    if (program === "tblastn") return "Frame: " + f(p.hit_frame);
    if (program === "tblastx") return "Frame: " + f(p.query_frame) + "/" + f(p.hit_frame);
    return "";
  }

  function alignmentText(program, p) {
    const qStep = program === "blastx" || program === "tblastx" ? 3 : 1;
    const sStep = program === "tblastn" || program === "tblastx" ? 3 : 1;
    let qDir = 1, qPos = Math.min(p.query_from, p.query_to);
    if (qStep === 3 && p.query_frame < 0) { qDir = -1; qPos = Math.max(p.query_from, p.query_to); }
    let sDir = 1, sPos = p.hit_from;
    if (sStep === 1 && p.hit_to < p.hit_from) sDir = -1;
    if (sStep === 3) { if (p.hit_frame < 0) { sDir = -1; sPos = Math.max(p.hit_from, p.hit_to); } else sPos = Math.min(p.hit_from, p.hit_to); }
    const W = 60, lines = [];
    const width = String(Math.max(p.query_from, p.query_to, p.hit_from, p.hit_to)).length;
    const pad = (n) => String(n).padEnd(width);
    for (let i = 0; i < p.qseq.length; i += W) {
      const q = p.qseq.slice(i, i + W), m = (p.midline || "").slice(i, i + W), s = p.hseq.slice(i, i + W);
      const qn = q.replace(/-/g, "").length, sn = s.replace(/-/g, "").length;
      const qEnd = qn ? qPos + qDir * (qn * qStep - 1) : qPos;
      const sEnd = sn ? sPos + sDir * (sn * sStep - 1) : sPos;
      lines.push(`Query  ${pad(qPos)}  ${q}  ${qEnd}`);
      lines.push(`       ${" ".repeat(width)}  ${m}`);
      lines.push(`Sbjct  ${pad(sPos)}  ${s}  ${sEnd}`);
      lines.push("");
      if (qn) qPos = qEnd + qDir;
      if (sn) sPos = sEnd + sDir;
    }
    return lines.join("\n").trimEnd();
  }

  function renderGraphic(q, qi) {
    const hits = q.hits.slice(0, 100);
    const W = 1000, L = 20, R = 20, top = 34, rowH = 9, gap = 3;
    const H = top + hits.length * (rowH + gap) + 8;
    const x = (pos) => L + (W - L - R) * (pos - 1) / Math.max(q.len - 1, 1);
    const ticks = [];
    const step = niceStep(q.len);
    for (let t = step; t < q.len; t += step) ticks.push(t);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Where hits align on the query">
      <rect x="${L}" y="12" width="${W - L - R}" height="8" rx="2" fill="var(--accent)"/>
      <text x="${L}" y="30">1</text><text x="${W - R}" y="30" text-anchor="end">${fmt(q.len)}</text>
      ${ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="20" y2="24" stroke="var(--muted)"/><text x="${x(t)}" y="32" text-anchor="middle">${fmt(t)}</text>`).join("")}`;
    hits.forEach((h, i) => {
      const y = top + i * (rowH + gap) + 4;
      const c = `var(--s${scoreClass(h.max_score)})`;
      const segs = h.hsps.map((p) => { const a = Math.min(p.query_from, p.query_to), b = Math.max(p.query_from, p.query_to);
        return `<rect x="${x(a)}" y="${y}" width="${Math.max(x(b) - x(a), 2)}" height="${rowH}" rx="1.5" fill="${c}"/>`; });
      svg += `<g class="hitbar" data-hit="${i}"><title>${esc(h.id)} ${esc(hitGeneText(null, h))} · E=${fmtE(h.evalue)} · ${h.max_score} bits</title>
        <rect x="${L}" y="${y - 1}" width="${W - L - R}" height="${rowH + 2}" fill="transparent"/>${segs.join("")}</g>`;
    });
    svg += "</svg>";
    return svg;
  }
  function niceStep(len) {
    const raw = len / 8, mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
    return [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  }

  const SORTS = {
    evalue: (a, b) => a.evalue - b.evalue || b.max_score - a.max_score,
    max: (a, b) => b.max_score - a.max_score, total: (a, b) => b.total_score - a.total_score,
    cover: (a, b) => b.query_cover - a.query_cover, ident: (a, b) => b.identity - a.identity,
    len: (a, b) => b.len - a.len, name: (a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }),
  };

  function renderResult(res, id, issue) {
    stopPolling();
    const view = $("#view-job");
    if (res.status === "failed") {
      return renderFailure(view, id, issue || { html_url: `https://github.com/${CFG.repo}/issues/${id}` }, res.error);
    }
    const req = res.request, prog = req.program;
    const nHits = res.queries.reduce((s, q) => s + q.hits.length, 0);
    const demo = isDemo(id);
    const base = demo ? demoPath(id) : `${RAW}/results/${id}/`;
    const issueUrl = issue ? issue.html_url : `https://github.com/${CFG.repo}/issues/${id}`;
    const fileDesc = { "alignments.txt": ["Alignments (text)", "Classic BLAST pairwise output"], "hits.tsv": ["Hit table (TSV)", "Opens in Excel; includes gene names"],
      "hits.fasta": ["Hit sequences (FASTA)", "Aligned regions of each hit"], "query.fa": ["Query (FASTA)", "What was searched"] };
    view.innerHTML = `
      ${demo ? '<div class="notice info" style="margin-bottom:14px"><b>Demo:</b> these results come from a small synthetic test genome, to show what results look like.</div>' : ""}
      <div class="res-head">
        <div>
          <h1>${esc(req.title || (prog + " vs " + DBS[req.database].label.toLowerCase()))}</h1>
          <div class="res-meta">
            <span><b>${prog}</b>${req.task ? " · " + req.task : ""}${req.matrix ? " · " + req.matrix : ""}</span>
            <span>${DBS[req.database].label}</span>
            <span>${res.queries.length} quer${res.queries.length > 1 ? "ies" : "y"} · ${fmt(nHits)} hits</span>
            <span>E ≤ ${req.evalue}</span>
            ${res.submitted_by ? `<span>by ${esc(res.submitted_by)}</span>` : ""}
            ${res.finished ? `<span>${new Date(res.finished).toLocaleString()}</span>` : ""}
            ${demo ? "" : `<a href="${esc(issueUrl)}" target="_blank" rel="noopener">#${esc(id)} on GitHub</a>`}
          </div>
        </div>
        <div class="res-actions">
          <div class="dropdown"><button class="btn" id="dl-btn" aria-haspopup="true">⬇ Download</button>
            <div class="dropdown-menu" hidden>${(res.files || []).map((f) => `<a href="${esc(base + f)}" data-file="${esc(f)}">${fileDesc[f] ? fileDesc[f][0] + "<small>" + fileDesc[f][1] + "</small>" : esc(f)}</a>`).join("")}</div></div>
          <button class="btn" id="edit-btn">✎ Edit &amp; resubmit</button>
          <button class="btn" id="share-btn">🔗 Copy link</button>
        </div>
      </div>
      ${res.queries.length > 1 ? `<div class="query-tabs" role="tablist">${res.queries.map((q, i) => `<button role="tab" data-q="${i}">${esc(q.id.split(" ")[0])}<span class="count">${q.hits.length}</span></button>`).join("")}</div>` : ""}
      <div id="query-result"></div>`;

    // downloads: fetch + save so the file gets a sensible name
    $("#dl-btn").addEventListener("click", (e) => { e.stopPropagation(); const m = $(".dropdown-menu", view); m.hidden = !m.hidden; });
    $$(".dropdown-menu a", view).forEach((a) => a.addEventListener("click", async (e) => {
      e.preventDefault();
      try { const r = await fetch(a.href); download(`blast-${id}-${a.dataset.file}`, await r.text()); }
      catch (err) { window.open(a.href, "_blank"); }
    }));
    $("#share-btn").addEventListener("click", () => copy(SITE + "#/job/" + id, "Link"));
    $("#edit-btn").addEventListener("click", async () => {
      let fasta = "";
      try { fasta = demo ? await (await fetch(base + "query.fa")).text() : await rawText(`results/${id}/query.fa`); } catch (e) { /* leave empty */ }
      location.hash = "#/";
      fillForm(req, fasta.trim());
      toast("Search loaded into the form");
    });

    const showQuery = (qi) => {
      $$(".query-tabs button", view).forEach((b) => b.classList.toggle("active", Number(b.dataset.q) === qi));
      renderQuery(res, qi, $("#query-result"));
    };
    $$(".query-tabs button", view).forEach((b) => b.addEventListener("click", () => showQuery(Number(b.dataset.q))));
    showQuery(0);
  }

  function renderQuery(res, qi, el) {
    const q = res.queries[qi], prog = res.request.program, isGenome = res.request.database === "genome";
    if (!q.hits.length) {
      el.innerHTML = `<div class="box"><h2>${esc(q.id)}</h2><p class="muted">Length ${fmt(q.len)}</p>
        <div class="notice">No hits with E-value ≤ ${res.request.evalue}. ${noHitsAdvice(res.request)}</div></div>`;
      return;
    }
    let sortKey = "evalue", asc = true, filter = "";
    el.innerHTML = `
      <div class="box">
        <div class="box-title"><h2>${esc(q.id)} <span class="muted small">· ${fmt(q.len)} ${PROGRAMS[prog].q === "protein" ? "aa" : "bp"}</span></h2>
          <div class="legend">Alignment score (bits): <span><i style="background:var(--s1)"></i>&lt;40</span><span><i style="background:var(--s2)"></i>40–50</span><span><i style="background:var(--s3)"></i>50–80</span><span><i style="background:var(--s4)"></i>80–200</span><span><i style="background:var(--s5)"></i>≥200</span></div></div>
        <div class="graphic">${renderGraphic(q, qi)}</div>
        ${q.hits.length > 100 ? '<p class="muted small">Graphic shows the top 100 hits.</p>' : ""}
      </div>
      <div class="box">
        <div class="box-title"><h2>Sequences producing significant alignments</h2>
          <div class="filter-row"><input type="search" id="hit-filter" placeholder="Filter by name or gene…" aria-label="Filter hits">
          <button class="btn btn-sm" id="copy-table">Copy table</button></div></div>
        <div class="table-wrap"><table class="hits"><thead><tr>
          <th data-sort="name">${isGenome ? "Scaffold / gene" : "Sequence / gene"}</th>
          <th data-sort="max" class="num">Max score</th><th data-sort="total" class="num hide-sm">Total score</th>
          <th data-sort="cover" class="num">Query cover</th><th data-sort="evalue" class="num">E-value</th>
          <th data-sort="ident" class="num">Identity</th><th data-sort="len" class="num hide-sm">Length</th>
          ${isGenome ? '<th class="hide-sm">Location</th>' : ""}
        </tr></thead><tbody></tbody></table></div>
      </div>
      <h2 class="section-title">Alignments</h2>
      <div id="alignments"></div>`;

    const tbody = $("tbody", el);
    const order = () => {
      const f = filter.toLowerCase();
      const rows = q.hits.map((h, i) => ({ h, i })).filter(({ h }) => !f || (h.id + " " + h.title + " " + hitGeneText(res, h)).toLowerCase().includes(f));
      rows.sort((a, b) => SORTS[sortKey](a.h, b.h) * (asc ? 1 : -1));
      return rows;
    };
    const draw = () => {
      tbody.innerHTML = order().map(({ h, i }) => {
        const loc = isGenome ? hitLocation(h) : null;
        return `<tr>
          <td><div class="subj"><span class="score-dot" style="background:var(--s${scoreClass(h.max_score)})"></span><a href="#" data-goto="${i}">${esc(h.id)}</a>${h.title ? ` <span class="muted">${esc(h.title)}</span>` : ""}</div>
            ${hitGeneText(res, h) ? `<div class="gene">${esc(hitGeneText(res, h))}</div>` : ""}</td>
          <td class="num">${h.max_score}</td><td class="num hide-sm">${h.total_score}</td>
          <td class="num"><span class="cover"><b style="width:${Math.min(h.query_cover, 100)}%"></b></span>${Math.round(h.query_cover)}%</td>
          <td class="num">${fmtE(h.evalue)}</td><td class="num">${h.identity.toFixed(1)}%</td><td class="num hide-sm">${fmt(h.len)}</td>
          ${isGenome ? `<td class="hide-sm mono small">${esc(loc.text)}</td>` : ""}
        </tr>`;
      }).join("") || `<tr><td colspan="8" class="muted">No hits match “${esc(filter)}”.</td></tr>`;
      $$("th[data-sort]", el).forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === sortKey); th.classList.toggle("asc", th.dataset.sort === sortKey && !asc); });
    };
    $$("th[data-sort]", el).forEach((th) => th.addEventListener("click", () => {
      if (sortKey === th.dataset.sort) asc = !asc; else { sortKey = th.dataset.sort; asc = true; }
      draw();
    }));
    $("#hit-filter", el).addEventListener("input", (e) => { filter = e.target.value; draw(); });
    $("#copy-table", el).addEventListener("click", () => {
      const rows = [["subject", "gene", "max_score", "total_score", "query_cover", "evalue", "identity", "length"].concat(isGenome ? ["location"] : [])];
      order().forEach(({ h }) => rows.push([h.id, hitGeneText(res, h), h.max_score, h.total_score, h.query_cover, fmtE(h.evalue), h.identity, h.len].concat(isGenome ? [hitLocation(h).plain] : [])));
      copy(rows.map((r) => r.join("\t")).join("\n"), "Table");
    });
    draw();

    // alignments: render lazily in batches so a 500-hit result stays responsive
    const alnEl = $("#alignments", el);
    const renderHit = (h, i) => {
      const loc = hitLocation(h);
      const card = document.createElement("details");
      card.className = "hit-card"; card.id = `q${qi}-hit${i}`;
      if (i < 3) card.open = true;
      card.innerHTML = `<summary><span class="score-dot" style="background:var(--s${scoreClass(h.max_score)})"></span>
          <span class="hit-name">${esc(h.id)}</span>
          <span class="hit-sub">${esc(hitGeneText(res, h) || h.title)} · length ${fmt(h.len)} · ${h.hsp_count} alignment${h.hsp_count > 1 ? "s" : ""}${h.hsp_count > h.hsps.length ? " (top " + h.hsps.length + " shown)" : ""}</span></summary>`;
      const fill = () => {
        if (card.dataset.filled) return; card.dataset.filled = 1;
        h.hsps.forEach((p, k) => {
          const d = document.createElement("div");
          d.className = "hsp";
          const pct = (n) => Math.round(100 * n / p.align_len);
          const region = `${h.id}:${Math.min(p.hit_from, p.hit_to)}-${Math.max(p.hit_from, p.hit_to)}`;
          const g = p.genes;
          let geneLine = "";
          if (g && g.overlap.length) geneLine = "Overlaps " + g.overlap.map((x) => `<b>${esc(geneName(x))}</b>${x.desc ? " (" + esc(x.desc) + ")" : ""} <span class="mono">${fmt(x.loc[0])}-${fmt(x.loc[1])} ${x.loc[2]}</span>`).join("; ");
          else if (g && g.nearest) geneLine = `Intergenic. Nearest gene <b>${esc(geneName(g.nearest))}</b>${g.nearest.desc ? " (" + esc(g.nearest.desc) + ")" : ""}, ${bp(g.nearest.distance)} away`;
          else if (h.gene && h.gene.loc) geneLine = `Gene <b>${esc(geneName(h.gene))}</b> at <span class="mono">${esc(h.gene.loc[0])}:${fmt(h.gene.loc[1])}-${fmt(h.gene.loc[2])} (${h.gene.loc[3]})</span>`;
          d.innerHTML = `<div class="hsp-stats">
              <span><b>Alignment ${k + 1}</b></span>
              <span>Score <b>${p.bit_score}</b> bits (${p.score})</span><span>Expect <b>${fmtE(p.evalue)}</b></span>
              <span>Identities <b>${p.identity}/${p.align_len}</b> (${pct(p.identity)}%)</span>
              ${p.positive != null ? `<span>Positives ${p.positive}/${p.align_len} (${pct(p.positive)}%)</span>` : ""}
              <span>Gaps ${p.gaps || 0}/${p.align_len} (${pct(p.gaps || 0)}%)</span>
              ${strandText(prog, p) ? `<span>${strandText(prog, p)}</span>` : ""}
            </div>
            ${geneLine ? `<div class="hsp-gene">${geneLine}</div>` : ""}
            <pre class="aln">${esc(alignmentText(prog, p))}</pre>
            <div class="hsp-tools">
              <button class="btn btn-sm" data-copy="region">Copy coordinates</button>
              <button class="btn btn-sm" data-copy="seq">Copy hit sequence</button>
              <button class="btn btn-sm" data-copy="fasta">Copy as FASTA</button>
            </div>`;
          const seq = p.hseq.replace(/-/g, "");
          d.querySelector('[data-copy="region"]').onclick = () => copy(region, "Coordinates");
          d.querySelector('[data-copy="seq"]').onclick = () => copy(seq, "Sequence");
          d.querySelector('[data-copy="fasta"]').onclick = () => copy(`>${region} hit to ${q.id.split(" ")[0]}\n${seq.replace(/(.{70})/g, "$1\n").trim()}`, "FASTA");
          card.appendChild(d);
        });
      };
      if (card.open) fill();
      card.addEventListener("toggle", () => card.open && fill());
      return card;
    };
    let next = 0;
    const batch = () => {
      const frag = document.createDocumentFragment();
      for (const end = Math.min(next + 25, q.hits.length); next < end; next++) frag.appendChild(renderHit(q.hits[next], next));
      alnEl.appendChild(frag);
      if (next < q.hits.length) requestIdleCallbackSafe(batch);
    };
    batch();

    const goto = (i) => {
      while (next <= i) batch();
      const card = $(`#q${qi}-hit${i}`); card.open = true;
      card.scrollIntoView({ behavior: "smooth", block: "start" });
      card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
    };
    el.addEventListener("click", (e) => {
      const a = e.target.closest("[data-goto]");
      if (a) { e.preventDefault(); goto(Number(a.dataset.goto)); }
      const g = e.target.closest(".hitbar");
      if (g) goto(Number(g.dataset.hit));
    });
  }
  const requestIdleCallbackSafe = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 300 }) : setTimeout(fn, 30));

  function noHitsAdvice(req) {
    const tips = [];
    if (req.evalue < 1) tips.push("raise the E-value threshold");
    if (req.program === "blastn" && req.task === "megablast") tips.push("use “More dissimilar” (dc-megablast) or “Somewhat similar” (blastn)");
    if (req.program === "blastn") tips.push("try tblastx, or blastx against proteins, for distant homologs");
    if (req.program === "blastp") tips.push("try tblastn against the genome, which also finds unannotated genes");
    if (req.filter) tips.push("turn off the low-complexity filter");
    return tips.length ? "You could " + tips.join(", or ") + "." : "";
  }

  // ---------------------------------------------------------------- job list
  async function showJobs() {
    const mine = store("blast.jobs") || [];
    const pending = store("blast.pending");
    $("#jobs-mine").innerHTML = (pending ? `<div class="notice info" style="margin-bottom:12px">You have a search waiting to be created on GitHub. <a href="#/submitted">Check on it</a>.</div>` : "")
      + (mine.length ? `<h2 class="section-title" style="margin-top:0">Your searches on this computer</h2><ul class="joblist">${mine.map((j) =>
        `<li><a href="#/job/${j.n}"><span class="jt">${esc(j.title)}</span><span class="jm">#${j.n} · ${new Date(j.at).toLocaleString()}</span></a></li>`).join("")}</ul>` : "");
    const all = $("#jobs-all");
    const demos = `<p class="muted small">Want to see what results look like? Demo results on a synthetic genome: <a href="#/job/demo-tblastn">tblastn</a> · <a href="#/job/demo-blastn">blastn</a> · <a href="#/job/demo-blastp">blastp</a></p>`;
    if (!CFG.repo) { all.innerHTML = `<p class="muted">Not connected to a repository.</p>` + demos; return; }
    try {
      const issues = (await api("/issues?state=all&sort=created&direction=desc&per_page=50")).filter((i) => !i.pull_request && /^BLAST/.test(i.title));
      all.innerHTML = (issues.length ? `<ul class="joblist">${issues.map((i) => {
        const st = issueStatus(i);
        return `<li><a href="#/job/${i.number}"><span class="status ${st}">${st}</span><span class="jt">${esc(i.title.replace(/^BLAST:\s*/, "").replace(/\s*\[\w+\]$/, ""))}</span>
          <span class="jm">#${i.number} · ${esc(i.user.login)} · ${new Date(i.created_at).toLocaleDateString()}</span></a></li>`;
      }).join("")}</ul>` : `<p class="muted">No searches yet.</p>`) + demos;
    } catch (e) {
      all.innerHTML = `<p class="muted">${esc(e instanceof RateLimited ? e.message : "Couldn't load the list from GitHub.")} <a href="https://github.com/${esc(CFG.repo)}/issues?q=is%3Aissue+BLAST+in%3Atitle">See searches on GitHub</a>.</p>` + demos;
    }
  }

  // ---------------------------------------------------------------- info, banner, footer
  async function loadInfo() {
    try { if (!CFG.repo) throw new Error("no repo"); INFO = await rawJSON("info.json"); }
    catch (e) {
      if (!CFG.repo) {
        try { INFO = await (await fetch("demo/info.json")).json(); INFO.demo = true; } catch (_) { INFO = null; }
      } else INFO = null;
    }
    const b = $("#banner");
    if (INFO && INFO.demo) {
      b.innerHTML = `<div class="notice info"><b>Preview mode.</b> This copy of the site isn't connected to a GitHub repository, so it shows a synthetic test genome. <a href="#/job/demo-tblastn">See demo results</a>.</div>`; b.hidden = false;
    } else if (!INFO) {
      b.innerHTML = `<div class="notice"><b>The genome databases haven't been built yet.</b> Searches will wait until they are (see the README's setup steps). Meanwhile, <a href="#/job/demo-tblastn">see demo results</a>.</div>`; b.hidden = false;
    }
    const d = INFO && INFO.databases.genome;
    $("#footer").innerHTML = [
      INFO ? `<i>${esc(INFO.species)}</i>${INFO.assembly ? " assembly " + esc(INFO.assembly) : ""} · ${d ? bp(d.total_length) + " in " + fmt(d.sequences) + " scaffolds" : ""} · databases built ${esc(INFO.built)}` : "",
      `Searches run with NCBI BLAST+${INFO ? " " + esc(INFO.blast_version) : ""} on GitHub Actions`,
      CFG.labName ? esc(CFG.labName) : "",
      CFG.repo ? `<a href="https://github.com/${esc(CFG.repo)}">Source &amp; searches on GitHub</a>` : "",
    ].filter(Boolean).join(" · ");
    if (INFO) {
      const rows = [["Species", `<i>${esc(INFO.species)}</i>`], ["Assembly", esc(INFO.assembly || "—")]];
      if (d) rows.push(["Scaffolds", fmt(d.sequences)], ["Total length", bp(d.total_length) + ` (${fmt(d.total_length)} bp)`], ["Scaffold N50", bp(d.n50)], ["GC content", d.gc_percent + "%"]);
      if (d && d.genes) rows.push(["Genes", fmt(d.genes)]);
      if (INFO.databases.transcripts) rows.push(["Transcripts", fmt(INFO.databases.transcripts.sequences)]);
      if (INFO.databases.proteins) rows.push(["Proteins", fmt(INFO.databases.proteins.sequences)]);
      rows.push(["Databases built", esc(INFO.built)], ["BLAST+ version", esc(INFO.blast_version)]);
      $("#db-info-help").innerHTML = `<h2>About the databases</h2><dl class="stats">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
    }
  }

  // ---------------------------------------------------------------- router
  function route() {
    stopPolling();
    const h = location.hash || "#/";
    const [, page, arg] = h.match(/^#\/?([^/]*)\/?(.*)$/) || [];
    $$(".view").forEach((v) => (v.hidden = true));
    $$("nav a").forEach((a) => a.classList.remove("active"));
    const nav = (n) => { const a = $(`nav a[data-nav="${n}"]`); if (a) a.classList.add("active"); };
    if (page === "job" && arg) { $("#view-job").hidden = false; nav("jobs"); showJob(decodeURIComponent(arg)); }
    else if (page === "jobs") { $("#view-jobs").hidden = false; nav("jobs"); showJobs(); }
    else if (page === "help") { $("#view-help").hidden = false; nav("help"); }
    else if (page === "submitted") { $("#view-submitted").hidden = false; nav("search"); showSubmitted(); }
    else { $("#view-search").hidden = false; nav("search"); }
    window.scrollTo(0, 0);
  }

  document.addEventListener("click", () => $$(".dropdown-menu").forEach((m) => (m.hidden = true)));
  document.title = CFG.title;
  $("#brand-species").textContent = CFG.species;
  initForm();
  window.addEventListener("hashchange", route);
  route();
  loadInfo().then(() => updateQueryStatus());
})();
