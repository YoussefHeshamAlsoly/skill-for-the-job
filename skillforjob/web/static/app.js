/* ---------------------------------------------------------------------- */
/* skill-for-the-job - web interface                                      */
/*                                                                         */
/* The page talks to the small JSON API of the Flask application:         */
/*   GET  /api/meta    what the dataset holds                              */
/*   GET  /api/titles  title suggestions for the search box                 */
/*   POST /api/skills  the analysis of one job title                       */
/*   POST /api/plot    the same analysis as a PNG                           */
/* ---------------------------------------------------------------------- */

(() => {
  "use strict";

  const API = {
    meta: "/api/meta",
    titles: "/api/titles",
    skills: "/api/skills",
    plot: "/api/plot",
  };

  const numberFormat = new Intl.NumberFormat("en-US");

  const el = {
    datasetChip: document.getElementById("dataset-chip"),
    datasetLabel: document.getElementById("dataset-label"),
    themeToggle: document.getElementById("theme-toggle"),
    reset: document.getElementById("reset"),
    target: document.getElementById("target"),
    titleList: document.getElementById("title-suggestions"),
    suggestions: document.getElementById("suggestions"),
    level: document.getElementById("level"),
    range: document.getElementById("range"),
    dateCustom: document.getElementById("date-custom"),
    dateFrom: document.getElementById("date-from"),
    dateTo: document.getElementById("date-to"),
    rangeHint: document.getElementById("range-hint"),
    looseInputs: Array.from(document.querySelectorAll('input[name="loose"]')),
    coverage: document.getElementById("coverage"),
    coverageField: document.getElementById("coverage-field"),
    coverageValue: document.getElementById("coverage-value"),
    top: document.getElementById("top"),
    topValue: document.getElementById("top-value"),
    showTitles: document.getElementById("show-titles"),
    titlesLimit: document.getElementById("titles-limit"),
    search: document.getElementById("search"),
    exportJson: document.getElementById("export-json"),
    downloadPng: document.getElementById("download-png"),
    summary: document.getElementById("summary"),
    summaryTitle: document.getElementById("summary-title"),
    summarySub: document.getElementById("summary-sub"),
    summaryStats: document.getElementById("summary-stats"),
    chart: document.getElementById("chart"),
    chartTitle: document.getElementById("chart-title"),
    chartNote: document.getElementById("chart-note"),
    chartEmpty: document.getElementById("chart-empty"),
    titlesPanel: document.getElementById("titles-panel"),
    titlesNote: document.getElementById("titles-note"),
    titlesBody: document.getElementById("titles-body"),
    suggestionsPanel: document.getElementById("suggestions-panel"),
    suggestionList: document.getElementById("suggestion-list"),
    tooltip: document.getElementById("tooltip"),
    toast: document.getElementById("toast"),
  };

  const state = {
    meta: null,
    result: null,
    suggestionItems: [],
    toastTimer: null,
  };

  /* ------------------------------------------------------------------ */
  /* Small helpers                                                      */
  /* ------------------------------------------------------------------ */

  const isLoose = () =>
    Boolean(document.querySelector('input[name="loose"]:checked')?.value === "1");

  const format = (value) => numberFormat.format(value);

  const slug = (text) =>
    String(text || "skills")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "skills";

  function isoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function parseDate(text) {
    if (!text) return null;
    const parsed = new Date(`${text}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) {
      const message = (data && data.error) || `the server answered ${response.status}`;
      throw new Error(message);
    }
    return data;
  }
  function showToast(message, isError) {
    el.toast.textContent = message;
    el.toast.classList.toggle("is-error", Boolean(isError));
    el.toast.hidden = false;
    requestAnimationFrame(() => el.toast.classList.add("is-visible"));
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      el.toast.classList.remove("is-visible");
      window.setTimeout(() => {
        el.toast.hidden = true;
      }, 200);
    }, 3600);
  }

  function setBusy(busy) {
    el.search.disabled = busy;
    el.search.innerHTML = busy ? '<span class="spinner"></span>Searching' : "Search";
    document.body.classList.toggle("is-busy", busy);
  }

  /* ------------------------------------------------------------------ */
  /* Tooltips                                                            */
  /* ------------------------------------------------------------------ */

  let tipTarget = null;

  function showTip(target) {
    const text = target.dataset.tip;
    if (!text) return;
    tipTarget = target;
    el.tooltip.textContent = text;
    el.tooltip.dataset.placement = "top";
    el.tooltip.classList.add("is-visible");
    el.tooltip.setAttribute("aria-hidden", "false");
    placeTip(target);
  }

  function placeTip(target) {
    const rect = target.getBoundingClientRect();
    const box = el.tooltip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - box.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - box.width - 8));
    let top = rect.top - box.height - 10;
    let placement = "top";
    if (top < 8) {
      top = rect.bottom + 10;
      placement = "bottom";
    }
    el.tooltip.dataset.placement = placement;
    el.tooltip.style.left = `${Math.round(left)}px`;
    el.tooltip.style.top = `${Math.round(top)}px`;
    el.tooltip.style.setProperty(
      "--arrow",
      `${Math.round(Math.max(10, Math.min(rect.left + rect.width / 2 - left, box.width - 10)))}px`,
    );
  }

  function hideTip() {
    tipTarget = null;
    el.tooltip.classList.remove("is-visible");
    el.tooltip.setAttribute("aria-hidden", "true");
  }

  function bindTooltips() {
    const find = (node) => node.closest("[data-tip]");

    document.addEventListener("mouseover", (event) => {
      const target = find(event.target);
      if (target && target !== tipTarget) showTip(target);
      else if (target && tipTarget) placeTip(target);
    });

    document.addEventListener("mouseout", (event) => {
      const target = find(event.target);
      if (target && !target.contains(event.relatedTarget)) hideTip();
    });

    document.addEventListener("focusin", (event) => {
      const target = find(event.target);
      if (target) showTip(target);
    });

    document.addEventListener("focusout", () => hideTip());

    window.addEventListener("scroll", () => {
      if (tipTarget) placeTip(tipTarget);
    }, true);

    window.addEventListener("resize", hideTip);
  }

  /* ------------------------------------------------------------------ */
  /* Theme                                                               */
  /* ------------------------------------------------------------------ */

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem("sfj-theme", theme);
    } catch (error) {
      /* the browser may forbid the storage, that is fine */
    }
  }

  function bindTheme() {
    let theme = "dark";
    try {
      const stored = window.localStorage.getItem("sfj-theme");
      if (stored === "light" || stored === "dark") theme = stored;
    } catch (error) {
      /* ignore */
    }
    applyTheme(theme);

    el.themeToggle.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  }

  /* ------------------------------------------------------------------ */
  /* The dataset meta data                                               */
  /* ------------------------------------------------------------------ */

  function renderMeta(meta) {
    if (meta.available) {
      el.datasetLabel.textContent = `${format(meta.rows)} jobs · updated ${meta.updated}`;
      el.datasetChip.classList.remove("is-missing");
    } else {
      el.datasetLabel.textContent = "no dataset - run 'python main.py update'";
      el.datasetChip.classList.add("is-missing");
    }

    el.level.innerHTML = "";
    el.level.append(new Option("Any", ""));
    (meta.levels || []).forEach((level) => {
      el.level.append(new Option(level, level));
    });

    el.titleList.innerHTML = "";
    (meta.popular_titles || []).forEach((title) => {
      const option = document.createElement("option");
      option.value = title;
      el.titleList.append(option);
    });

    renderRangePresets(meta);
  }

  function renderRangePresets(meta) {
    const previous = el.range.value;
    el.range.innerHTML = "";

    const presets = [{ value: "", label: "All dates" }];
    const newest = parseDate(meta.date_max);
    const oldest = parseDate(meta.date_min);

    if (newest) {
      const lastDays = (days) => {
        const from = new Date(newest);
        from.setDate(from.getDate() - (days - 1));
        return `${isoDate(from)} ${isoDate(newest)}`;
      };
      presets.push({ value: lastDays(30), label: "Last 30 days" });
      presets.push({ value: lastDays(90), label: "Last 90 days" });

      const year = newest.getFullYear();
      const fromYear = new Date(newest);
      fromYear.setFullYear(fromYear.getFullYear() - 1);
      presets.push({
        value: `${isoDate(fromYear)} ${isoDate(newest)}`,
        label: "Last 12 months",
      });
      presets.push({
        value: `${year}-01-01 ${isoDate(newest)}`,
        label: "This year",
      });
      presets.push({
        value: `${year - 1}-01-01 ${year - 1}-12-31`,
        label: "Last year",
      });

      if (oldest) {
        const firstYear = oldest.getFullYear();
        for (let y = year; y >= firstYear && y > year - 6; y -= 1) {
          presets.push({ value: `${y}-01-01 ${y}-12-31`, label: String(y) });
        }
      }
    }

    presets.push({ value: "custom", label: "Custom range…" });

    presets.forEach((preset) => {
      el.range.append(new Option(preset.label, preset.value));
    });

    const known = presets.some((preset) => preset.value === previous);
    el.range.value = known ? previous : "";
    updateRangeState();
  }

  function updateRangeState() {
    const custom = el.range.value === "custom";
    el.dateCustom.hidden = !custom;
    if (custom) {
      const meta = state.meta || {};
      if (!el.dateFrom.value && meta.date_min) el.dateFrom.value = meta.date_min;
      if (!el.dateTo.value && meta.date_max) el.dateTo.value = meta.date_max;
      el.rangeHint.textContent = "Only the listings posted inside this period are used.";
    } else {
      const option = el.range.options[el.range.selectedIndex];
      el.rangeHint.textContent = option
        ? option.textContent
        : "Every posting date in the dataset.";
    }
  }

  /* ------------------------------------------------------------------ */
  /* The title suggestions                                               */
  /* ------------------------------------------------------------------ */

  let suggestTimer = null;

  function scheduleSuggestions() {
    window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(loadSuggestions, 160);
  }

  async function loadSuggestions() {
    const query = el.target.value.trim();
    if (query.length < 2) {
      hideSuggestions();
      return;
    }
    try {
      const data = await fetchJSON(`${API.titles}?q=${encodeURIComponent(query)}&limit=12`);
      state.suggestionItems = data.titles || [];
      renderSuggestions();
    } catch (error) {
      hideSuggestions();
    }
  }

  function renderSuggestions() {
    el.suggestions.innerHTML = "";
    if (!state.suggestionItems.length) {
      hideSuggestions();
      return;
    }
    state.suggestionItems.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.innerHTML = `<span></span><span class="count">${format(item.jobs)}</span>`;
      button.firstChild.textContent = item.title;
      button.addEventListener("click", () => {
        el.target.value = item.title;
        hideSuggestions();
        runSearch();
      });
      el.suggestions.append(button);
    });
    el.suggestions.hidden = false;
  }

  function hideSuggestions() {
    el.suggestions.hidden = true;
    el.suggestions.innerHTML = "";
    state.suggestionItems = [];
  }

  /* ------------------------------------------------------------------ */
  /* Reading the options                                                 */
  /* ------------------------------------------------------------------ */

  function currentRange() {
    const value = el.range.value;
    if (!value || value === "custom") {
      if (value === "custom") {
        const from = el.dateFrom.value;
        const to = el.dateTo.value;
        if (from && to) return `${from} ${to}`;
        if (from) return `${from} new`;
        if (to) return `old ${to}`;
        return "";
      }
      return "";
    }
    return value;
  }

  function collect() {
    return {
      target: el.target.value.trim(),
      level: el.level.value || null,
      coverage: Number(el.coverage.value) / 100,
      loose: isLoose(),
      date_range: currentRange() || null,
      top: Number(el.top.value),
      titles_limit: el.showTitles.checked ? Number(el.titlesLimit.value) : 0,
    };
  }

  /* ------------------------------------------------------------------ */
  /* The search                                                          */
  /* ------------------------------------------------------------------ */

  let activeRequest = null;

  async function runSearch() {
    const body = collect();
    if (!body.target) {
      showToast("Please enter a job title.", true);
      el.target.focus();
      return;
    }

    // A new search cancels the one that is still running.
    if (activeRequest) activeRequest.abort();
    const controller = new AbortController();
    activeRequest = controller;

    setBusy(true);
    try {
      const data = await fetchJSON(API.skills, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      state.result = data;
      renderResult(data);
      writeUrl(body);
    } catch (error) {
      if (error.name === "AbortError") return;
      showToast(error.message, true);
    } finally {
      if (activeRequest === controller) setBusy(false);
    }
  }

  function renderResult(data) {
    el.summary.hidden = false;
    el.summaryTitle.textContent = data.target;
    el.summarySub.textContent = [
      data.match,
      data.level === "any" ? "every level" : data.level,
      data.date_range,
    ]
      .filter(Boolean)
      .join(" · ");

    const share = data.jobs_in_scope ? data.matched_jobs / data.jobs_in_scope : 0;
    el.summaryStats.innerHTML = "";
    addStat(format(data.matched_jobs), "matched jobs");
    addStat(format(data.jobs_in_scope), "jobs in scope");
    addStat(format(data.skills_found), "different skills");
    addStat(`${(share * 100).toFixed(1)}%`, "of the scope");

    // --- the histogram ------------------------------------------------ //
    const skills = data.skills || [];
    el.chart.innerHTML = "";
    el.chartEmpty.hidden = skills.length > 0;
    el.chartTitle.textContent = `Top ${skills.length} skills`;
    el.chartNote.textContent = skills.length
      ? `share of the ${format(data.matched_jobs)} matched jobs`
      : "";

    const biggest = skills.reduce((max, item) => Math.max(max, item.jobs), 0);
    skills.forEach((skill) => {
      const row = document.createElement("div");
      row.className = "bar-row";

      const name = document.createElement("span");
      name.className = "bar-skill";
      name.textContent = skill.skill;
      name.title = skill.skill;

      const track = document.createElement("span");
      track.className = "bar-track";
      const fill = document.createElement("span");
      fill.className = "bar-fill";
      fill.style.width = biggest ? `${Math.max(1, (skill.jobs / biggest) * 100)}%` : "0%";
      track.append(fill);

      const count = document.createElement("span");
      count.className = "bar-count";
      count.textContent = format(skill.jobs);

      const percent = document.createElement("span");
      percent.className = "bar-share";
      percent.textContent = `${skill.share}%`;

      row.append(name, track, count, percent);
      el.chart.append(row);
    });

    // --- the job titles ----------------------------------------------- //
    const titles = data.titles || [];
    el.titlesPanel.hidden = titles.length === 0;
    el.titlesBody.innerHTML = "";
    if (titles.length) {
      el.titlesNote.textContent = `${format(titles.length)} titles shown`;
      titles.forEach((entry) => {
        const row = document.createElement("tr");
        const name = document.createElement("td");
        name.textContent = entry.title;
        const jobs = document.createElement("td");
        jobs.className = "num";
        jobs.textContent = format(entry.jobs);
        const percent = document.createElement("td");
        percent.className = "num";
        percent.textContent = `${entry.share}%`;
        row.append(name, jobs, percent);
        el.titlesBody.append(row);
      });
    }

    // --- the suggestions ---------------------------------------------- //
    const suggestions = data.suggestions || [];
    el.suggestionsPanel.hidden = suggestions.length === 0;
    el.suggestionList.innerHTML = "";
    suggestions.forEach((title) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = title;
      button.addEventListener("click", () => {
        el.target.value = title;
        runSearch();
      });
      item.append(button);
      el.suggestionList.append(item);
    });

    el.exportJson.disabled = false;
    el.downloadPng.disabled = data.matched_jobs === 0;
  }

  function addStat(value, name) {
    const box = document.createElement("div");
    box.className = "stat";
    const valueBox = document.createElement("span");
    valueBox.className = "value";
    valueBox.textContent = value;
    const nameBox = document.createElement("span");
    nameBox.className = "name";
    nameBox.textContent = name;
    box.append(valueBox, nameBox);
    el.summaryStats.append(box);
  }

  /* ------------------------------------------------------------------ */
  /* Exporting                                                           */
  /* ------------------------------------------------------------------ */

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportJson() {
    if (!state.result) return;
    const text = JSON.stringify(state.result, null, 2);
    download(
      new Blob([text], { type: "application/json" }),
      `skills-${slug(state.result.target)}.json`,
    );
  }

  async function downloadPng() {
    if (!state.result) return;
    const body = collect();
    setBusy(true);
    try {
      const response = await fetch(API.plot, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error((data && data.error) || "the plot could not be built");
      }
      const blob = await response.blob();
      download(blob, `skills-${slug(body.target)}.png`);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------------------------------------------ */
  /* The url keeps the state                                             */
  /* ------------------------------------------------------------------ */

  function writeUrl(body) {
    const params = new URLSearchParams();
    params.set("q", body.target);
    if (body.level) params.set("level", body.level);
    if (body.loose) params.set("loose", "1");
    if (body.coverage !== 0.6) params.set("coverage", String(body.coverage));
    if (body.date_range) params.set("range", body.date_range);
    if (body.top !== 20) params.set("top", String(body.top));
    if (body.titles_limit) params.set("titles", String(body.titles_limit));
    window.history.replaceState(null, "", `?${params.toString()}`);
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    const target = params.get("q");
    if (!target) return false;

    el.target.value = target;
    const level = params.get("level");
    if (level) el.level.value = level;
    const loose = params.get("loose") === "1";
    el.looseInputs.forEach((input) => {
      input.checked = loose ? input.value === "1" : input.value === "0";
    });
    const coverage = Number(params.get("coverage"));
    if (coverage) el.coverage.value = String(Math.round(coverage * 100));
    const top = Number(params.get("top"));
    if (top) el.top.value = String(top);
    const titles = Number(params.get("titles"));
    if (titles) {
      el.showTitles.checked = true;
      el.titlesLimit.value = String(titles);
    }
    const range = params.get("range");
    if (range) {
      const known = Array.from(el.range.options).some((option) => option.value === range);
      if (known) {
        el.range.value = range;
      } else {
        el.range.value = "custom";
        const parts = range.split(" ");
        if (parts.length === 2 && /^\d{4}-\d{2}-\d{2}$/.test(parts[0])) {
          el.dateFrom.value = parts[0];
          el.dateTo.value = parts[1];
        }
      }
    }
    syncControls();
    return true;
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */

  function syncControls() {
    const loose = isLoose();
    el.coverage.disabled = !loose;
    el.coverageField.dataset.disabled = String(!loose);
    el.coverageValue.textContent = `${el.coverage.value}%`;
    el.topValue.textContent = el.top.value;
    el.titlesLimit.disabled = !el.showTitles.checked;
  }

  /** Re-run the search, but only when there is something to search for. */
  function refresh() {
    if (!el.target.value.trim()) return;
    runSearch();
  }

  function resetAll() {
    el.target.value = "";
    el.level.value = "";
    el.range.value = "";
    el.dateFrom.value = "";
    el.dateTo.value = "";
    el.looseInputs.forEach((input) => {
      input.checked = input.value === "0";
    });
    el.coverage.value = "60";
    el.top.value = "20";
    el.showTitles.checked = false;
    el.titlesLimit.value = "25";
    el.summary.hidden = true;
    el.chart.innerHTML = "";
    el.chartEmpty.hidden = false;
    el.titlesPanel.hidden = true;
    el.suggestionsPanel.hidden = true;
    el.exportJson.disabled = true;
    el.downloadPng.disabled = true;
    state.result = null;
    hideSuggestions();
    updateRangeState();
    syncControls();
    window.history.replaceState(null, "", window.location.pathname);
  }

  function bindControls() {
    el.target.addEventListener("input", scheduleSuggestions);
    el.target.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        hideSuggestions();
        runSearch();
      }
    });
    el.target.addEventListener("blur", () => {
      window.setTimeout(hideSuggestions, 120);
    });

    el.range.addEventListener("change", () => {
      updateRangeState();
      refresh();
    });
    el.looseInputs.forEach((input) =>
      input.addEventListener("change", () => {
        syncControls();
        refresh();
      }),
    );
    el.coverage.addEventListener("input", syncControls);
    el.coverage.addEventListener("change", refresh);
    el.top.addEventListener("input", syncControls);
    el.top.addEventListener("change", refresh);
    el.showTitles.addEventListener("change", () => {
      syncControls();
      refresh();
    });
    el.titlesLimit.addEventListener("change", refresh);
    el.level.addEventListener("change", refresh);
    el.dateFrom.addEventListener("change", refresh);
    el.dateTo.addEventListener("change", refresh);

    el.search.addEventListener("click", runSearch);
    el.reset.addEventListener("click", resetAll);
    el.exportJson.addEventListener("click", exportJson);
    el.downloadPng.addEventListener("click", downloadPng);

    document.addEventListener("keydown", (event) => {
      if (event.key === "/" && document.activeElement !== el.target) {
        event.preventDefault();
        el.target.focus();
      }
    });

    syncControls();
  }

  /* ------------------------------------------------------------------ */
  /* Start                                                               */
  /* ------------------------------------------------------------------ */

  async function boot() {
    bindTooltips();
    bindTheme();
    bindControls();

    // The tips of a <label> belong to the control it points at.
    document.querySelectorAll("label[data-tip][for]").forEach((label) => {
      const control = document.getElementById(label.getAttribute("for"));
      if (control && !control.dataset.tip) control.dataset.tip = label.dataset.tip;
    });

    try {
      const meta = await fetchJSON(API.meta);
      state.meta = meta;
      renderMeta(meta);
    } catch (error) {
      showToast(error.message, true);
    }

    if (readUrl()) runSearch();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
