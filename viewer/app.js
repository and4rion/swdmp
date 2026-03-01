(function () {
  "use strict";

  var PAGE_SIZE = 100;
  var STORAGE_KEY = "sharewood-archive-viewer-cache-v1";
  var DEBUG = new URLSearchParams(window.location.search).get("debug") === "1";
  var jsZipLoadPromise = null;
  var state = {
    rows: [],
    filtered: [],
    columns: [],
    page: 1,
  };

  var preferredColumns = [
    "torrent_id",
    "title",
    "details_url",
    "category",
    "subcategory",
    "size",
    "seeders",
    "leechers",
    "uploaded",
    "uploaded_relative",
    "page",
    "fetched_at",
  ];

  var ui = {
    fileInput: document.getElementById("fileInput"),
    searchInput: document.getElementById("searchInput"),
    categoryFilter: document.getElementById("categoryFilter"),
    subcategoryFilter: document.getElementById("subcategoryFilter"),
    sortField: document.getElementById("sortField"),
    sortDirection: document.getElementById("sortDirection"),
    hideNoSeed: document.getElementById("hideNoSeed"),
    tableHead: document.querySelector("#resultTable thead"),
    tableBody: document.querySelector("#resultTable tbody"),
    status: document.getElementById("status"),
    stats: document.getElementById("stats"),
    paginationTop: document.getElementById("paginationTop"),
    paginationBottom: document.getElementById("paginationBottom"),
  };

  var urlPrefs = parseUrlState();

  function setStatus(message) {
    ui.status.textContent = message;
    debugLog("status:", message);
  }

  function debugLog() {
    if (!DEBUG) return;
    var args = Array.prototype.slice.call(arguments);
    args.unshift("[viewer]");
    console.log.apply(console, args);
  }

  function parsePositiveInt(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return null;
    if (n < 1) return null;
    return Math.floor(n);
  }

  function parseUrlState() {
    var params = new URLSearchParams(window.location.search);
    return {
      q: params.get("q") || "",
      category: params.get("cat") || "",
      subcategory: params.get("sub") || "",
      sortField: params.get("sort") || "",
      sortDirection: params.get("dir") === "asc" ? "asc" : "desc",
      hideNoSeed: params.get("noseed") === "1",
      page: parsePositiveInt(params.get("p")) || 1,
    };
  }

  function applyUrlPrefsToControls() {
    ui.searchInput.value = urlPrefs.q;
    ui.categoryFilter.value = urlPrefs.category;
    ui.subcategoryFilter.value = urlPrefs.subcategory;
    ui.sortDirection.value = urlPrefs.sortDirection;
    ui.hideNoSeed.checked = urlPrefs.hideNoSeed;
  }

  function syncUrlState() {
    var params = new URLSearchParams();
    var q = ui.searchInput.value.trim();
    if (q) params.set("q", q);
    if (ui.categoryFilter.value) params.set("cat", ui.categoryFilter.value);
    if (ui.subcategoryFilter.value) params.set("sub", ui.subcategoryFilter.value);
    if (ui.sortField.value) params.set("sort", ui.sortField.value);
    if (ui.sortDirection.value && ui.sortDirection.value !== "desc") {
      params.set("dir", ui.sortDirection.value);
    }
    if (ui.hideNoSeed.checked) params.set("noseed", "1");
    if (state.page > 1) params.set("p", String(state.page));

    var query = params.toString();
    var nextUrl = window.location.pathname + (query ? "?" + query : "") + window.location.hash;
    window.history.replaceState(null, "", nextUrl);
  }

  function saveDataToLocalStorage(text, sourceName) {
    if (typeof text !== "string" || text.length > 4500000) {
      debugLog("cache skip: input too large", {
        sourceName: sourceName,
        length: typeof text === "string" ? text.length : null,
      });
      return false;
    }
    try {
      var payload = {
        sourceName: sourceName || "local-file",
        text: text,
        savedAt: new Date().toISOString(),
      };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
      debugLog("cache write ok", {
        sourceName: payload.sourceName,
        textLength: payload.text.length,
        savedAt: payload.savedAt,
      });
      return true;
    } catch (err) {
      debugLog("cache write failed", {
        name: err && err.name,
        message: err && err.message,
      });
      return false;
    }
  }

  function loadDataFromLocalStorage() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        debugLog("cache read: empty");
        return null;
      }
      var payload = JSON.parse(raw);
      if (!payload || typeof payload.text !== "string") {
        debugLog("cache read: invalid payload");
        return null;
      }
      debugLog("cache read ok", {
        sourceName: payload.sourceName,
        textLength: payload.text.length,
        savedAt: payload.savedAt,
      });
      return payload;
    } catch (err) {
      debugLog("cache read failed", {
        name: err && err.name,
        message: err && err.message,
      });
      return null;
    }
  }

  function detectFormat(text, sourceName) {
    var name = (sourceName || "").toLowerCase();
    var trimmed = text.trimStart();
    if (name.endsWith(".csv")) return "csv";
    if (name.endsWith(".jsonl") || name.endsWith(".ndjson")) return "jsonl";
    if (trimmed.startsWith("{")) return "jsonl";
    if (trimmed.startsWith("[")) return "json-array";
    return "csv";
  }

  function stripGzSuffix(name) {
    var lowered = (name || "").toLowerCase();
    if (!lowered.endsWith(".gz")) return name;
    return name.slice(0, -3);
  }

  function preferredArchiveEntryName(names) {
    if (!names.length) return null;
    var preferredOrder = [".jsonl", ".csv", ".json", ".txt"];
    for (var i = 0; i < preferredOrder.length; i += 1) {
      var ext = preferredOrder[i];
      for (var j = 0; j < names.length; j += 1) {
        if (names[j].toLowerCase().endsWith(ext)) return names[j];
      }
    }
    return names[0];
  }

  function ensureJsZip() {
    if (window.JSZip) return Promise.resolve(window.JSZip);
    if (jsZipLoadPromise) return jsZipLoadPromise;

    jsZipLoadPromise = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js";
      script.async = true;
      script.onload = function () {
        if (window.JSZip) {
          resolve(window.JSZip);
        } else {
          reject(new Error("JSZip loaded but unavailable in window."));
        }
      };
      script.onerror = function () {
        reject(new Error("Failed to load JSZip library from CDN."));
      };
      document.head.appendChild(script);
    });

    return jsZipLoadPromise;
  }

  function streamToText(stream) {
    return new Response(stream).text();
  }

  async function extractTextFromZip(file) {
    debugLog("archive: loading jszip");
    var JSZip = await ensureJsZip();
    var archive = await JSZip.loadAsync(await file.arrayBuffer());
    var entryNames = Object.keys(archive.files).filter(function (name) {
      return !archive.files[name].dir;
    });
    if (!entryNames.length) {
      throw new Error("ZIP archive is empty.");
    }

    var entryName = preferredArchiveEntryName(entryNames);
    var entry = archive.files[entryName];
    var text = await entry.async("string");
    debugLog("archive: selected entry", { entryName: entryName, textLength: text.length });
    return {
      text: text,
      sourceName: entryName,
    };
  }

  async function extractTextFromGzip(file) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("This browser does not support .gz decompression (DecompressionStream missing).");
    }
    var ds = new DecompressionStream("gzip");
    var decompressed = file.stream().pipeThrough(ds);
    var text = await streamToText(decompressed);
    var sourceName = stripGzSuffix(file.name || "archive");
    debugLog("archive: gzip decompressed", { sourceName: sourceName, textLength: text.length });
    return {
      text: text,
      sourceName: sourceName,
    };
  }

  async function readUploadFile(file) {
    var name = (file.name || "").toLowerCase();
    if (name.endsWith(".zip")) {
      return extractTextFromZip(file);
    }
    if (name.endsWith(".gz")) {
      return extractTextFromGzip(file);
    }
    return {
      text: await file.text(),
      sourceName: file.name || "uploaded-file",
    };
  }

  function parseJsonLines(text) {
    var out = [];
    var lines = text.split(/\r?\n/);
    for (var i = 0; i < lines.length; i += 1) {
      var line = lines[i].trim();
      if (!line) continue;
      out.push(JSON.parse(line));
    }
    return out;
  }

  function parseJsonArray(text) {
    var value = JSON.parse(text);
    if (!Array.isArray(value)) {
      throw new Error("JSON root is not an array.");
    }
    return value;
  }

  function parseCsv(text) {
    var rows = [];
    var row = [];
    var cell = "";
    var inQuotes = false;

    for (var i = 0; i < text.length; i += 1) {
      var ch = text[i];
      var next = text[i + 1];

      if (inQuotes) {
        if (ch === '"' && next === '"') {
          cell += '"';
          i += 1;
        } else if (ch === '"') {
          inQuotes = false;
        } else {
          cell += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        row.push(cell);
        cell = "";
      } else if (ch === "\n") {
        row.push(cell.replace(/\r$/, ""));
        rows.push(row);
        row = [];
        cell = "";
      } else {
        cell += ch;
      }
    }

    if (cell.length > 0 || row.length > 0) {
      row.push(cell.replace(/\r$/, ""));
      rows.push(row);
    }

    if (rows.length === 0) return [];

    var headers = rows[0].map(function (h) {
      return String(h || "").trim();
    });

    var out = [];
    for (var r = 1; r < rows.length; r += 1) {
      if (rows[r].length === 1 && rows[r][0] === "") continue;
      var obj = {};
      for (var c = 0; c < headers.length; c += 1) {
        obj[headers[c]] = rows[r][c] != null ? rows[r][c] : "";
      }
      out.push(obj);
    }
    return out;
  }

  function normalizeRows(rows) {
    return rows.map(function (row) {
      var out = {};
      Object.keys(row).forEach(function (key) {
        out[String(key).trim()] = row[key];
      });
      return out;
    });
  }

  function uniqueValues(rows, field) {
    var set = new Set();
    rows.forEach(function (row) {
      var value = row[field];
      if (value == null || value === "") return;
      set.add(String(value));
    });
    return Array.from(set).sort(function (a, b) {
      return a.localeCompare(b);
    });
  }

  function buildColumns(rows) {
    var found = new Set();
    rows.forEach(function (row) {
      Object.keys(row).forEach(function (k) {
        found.add(k);
      });
    });
    var cols = preferredColumns.filter(function (k) {
      return found.has(k);
    });
    Array.from(found)
      .filter(function (k) {
        return cols.indexOf(k) === -1;
      })
      .sort()
      .forEach(function (k) {
        cols.push(k);
      });
    return cols;
  }

  function numericMaybe(value) {
    if (value == null) return null;
    var raw = String(value).trim();
    if (!raw) return null;
    var normalized = raw.replace(/\s+/g, "").replace(/,/g, ".");
    var n = Number(normalized);
    return Number.isFinite(n) ? n : null;
  }

  function sizeBytesMaybe(value) {
    if (value == null) return null;
    var raw = String(value).trim();
    if (!raw) return null;

    var match = raw.match(/^(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)$/i);
    if (!match) return null;

    var amount = Number(match[1].replace(",", "."));
    if (!Number.isFinite(amount)) return null;

    var unit = match[2].toUpperCase();
    var factors = {
      B: 1,
      KB: 1000,
      MB: 1000 * 1000,
      GB: 1000 * 1000 * 1000,
      TB: 1000 * 1000 * 1000 * 1000,
      KIB: 1024,
      MIB: 1024 * 1024,
      GIB: 1024 * 1024 * 1024,
      TIB: 1024 * 1024 * 1024 * 1024,
    };

    var factor = factors[unit];
    if (!factor) return null;
    return amount * factor;
  }

  function dateMaybe(value) {
    if (!value) return null;
    var s = String(value).trim();
    if (!/^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(s)) return null;
    var t = Date.parse(s);
    return Number.isFinite(t) ? t : null;
  }

  function compareValues(a, b, field, direction) {
    var av = a[field];
    var bv = b[field];

    var asz = sizeBytesMaybe(av);
    var bsz = sizeBytesMaybe(bv);
    if (asz != null && bsz != null) {
      return direction === "asc" ? asz - bsz : bsz - asz;
    }

    var ad = dateMaybe(av);
    var bd = dateMaybe(bv);
    if (ad != null && bd != null) {
      return direction === "asc" ? ad - bd : bd - ad;
    }

    var an = numericMaybe(av);
    var bn = numericMaybe(bv);
    if (an != null && bn != null) {
      return direction === "asc" ? an - bn : bn - an;
    }

    var as = String(av == null ? "" : av).toLowerCase();
    var bs = String(bv == null ? "" : bv).toLowerCase();
    var cmp = as.localeCompare(bs);
    return direction === "asc" ? cmp : -cmp;
  }

  function applyFilters() {
    var q = ui.searchInput.value.trim().toLowerCase();
    var category = ui.categoryFilter.value;
    var subcategory = ui.subcategoryFilter.value;
    var sortField = ui.sortField.value;
    var sortDirection = ui.sortDirection.value;
    var hideNoSeed = ui.hideNoSeed.checked;

    var rows = state.rows.filter(function (row) {
      if (category && String(row.category || "") !== category) return false;
      if (subcategory && String(row.subcategory || "") !== subcategory) return false;
      if (hideNoSeed) {
        var seeds = numericMaybe(row.seeders);
        if (seeds == null || seeds < 1) return false;
      }
      if (!q) return true;
      var haystack = state.columns
        .map(function (k) {
          return String(row[k] == null ? "" : row[k]).toLowerCase();
        })
        .join(" ");
      return haystack.indexOf(q) !== -1;
    });

    if (sortField) {
      rows.sort(function (a, b) {
        return compareValues(a, b, sortField, sortDirection);
      });
    }

    state.filtered = rows;
    if (state.page < 1) state.page = 1;
    var maxPage = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (state.page > maxPage) state.page = maxPage;
    render();
  }

  function setSelectOptions(select, values, withAll) {
    var current = select.value;
    select.innerHTML = "";
    if (withAll) {
      var allOption = document.createElement("option");
      allOption.value = "";
      allOption.textContent = "All";
      select.appendChild(allOption);
    }
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    if (Array.from(select.options).some(function (o) { return o.value === current; })) {
      select.value = current;
    }
  }

  function renderStats() {
    var rows = state.filtered;
    var minUploaded = null;
    var maxUploaded = null;

    rows.forEach(function (row) {
      var t = dateMaybe(row.uploaded);
      if (t == null) return;
      if (minUploaded == null || t < minUploaded) minUploaded = t;
      if (maxUploaded == null || t > maxUploaded) maxUploaded = t;
    });

    var items = [
      ["Rows", String(rows.length)],
      ["Categories", String(uniqueValues(rows, "category").length)],
      ["Subcategories", String(uniqueValues(rows, "subcategory").length)],
      ["Date Range", minUploaded == null ? "n/a" : new Date(minUploaded).toISOString().slice(0, 10) + " to " + new Date(maxUploaded).toISOString().slice(0, 10)],
    ];

    ui.stats.innerHTML = "";
    items.forEach(function (pair) {
      var box = document.createElement("div");
      box.className = "stat";
      box.innerHTML = '<span class="k"></span><span class="v"></span>';
      box.querySelector(".k").textContent = pair[0];
      box.querySelector(".v").textContent = pair[1];
      ui.stats.appendChild(box);
    });
  }

  function renderTable() {
    var cols = state.columns;
    var start = (state.page - 1) * PAGE_SIZE;
    var end = start + PAGE_SIZE;
    var rows = state.filtered.slice(start, end);
    var activeSortField = ui.sortField.value;
    var activeSortDirection = ui.sortDirection.value;

    ui.tableHead.innerHTML = "";
    ui.tableBody.innerHTML = "";

    var trh = document.createElement("tr");
    cols.forEach(function (col) {
      var th = document.createElement("th");
      th.style.cursor = "pointer";
      var indicator = "";
      if (col === activeSortField) {
        indicator = activeSortDirection === "asc" ? " ▲" : " ▼";
      }
      th.textContent = col + indicator;
      th.title = "Click to sort";
      th.addEventListener("click", function () {
        if (ui.sortField.value === col) {
          ui.sortDirection.value = ui.sortDirection.value === "asc" ? "desc" : "asc";
        } else {
          ui.sortField.value = col;
          ui.sortDirection.value = "desc";
        }
        state.page = 1;
        applyFilters();
      });
      trh.appendChild(th);
    });
    ui.tableHead.appendChild(trh);

    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      cols.forEach(function (col) {
        var td = document.createElement("td");
        var value = row[col];
        if (col === "details_url" && value) {
          var a = document.createElement("a");
          a.href = String(value);
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = "open";
          td.appendChild(a);
        } else {
          td.textContent = value == null ? "" : String(value);
        }
        tr.appendChild(td);
      });
      ui.tableBody.appendChild(tr);
    });
  }

  function renderPagination() {
    var total = state.filtered.length;
    var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

    function mount(target) {
      target.innerHTML = "";
      var prev = document.createElement("button");
      prev.type = "button";
      prev.className = "ghost";
      prev.textContent = "Prev";
      prev.disabled = state.page <= 1;
      prev.addEventListener("click", function () {
        state.page -= 1;
        render();
      });
      target.appendChild(prev);

      var info = document.createElement("span");
      info.textContent = "Page " + state.page + " / " + totalPages;
      target.appendChild(info);

      var next = document.createElement("button");
      next.type = "button";
      next.className = "ghost";
      next.textContent = "Next";
      next.disabled = state.page >= totalPages;
      next.addEventListener("click", function () {
        state.page += 1;
        render();
      });
      target.appendChild(next);
    }

    mount(ui.paginationTop);
    mount(ui.paginationBottom);
  }

  function render() {
    renderStats();
    renderTable();
    renderPagination();
    syncUrlState();
    setStatus("Showing " + state.filtered.length + " rows.");
  }

  function setupControls() {
    [ui.searchInput, ui.categoryFilter, ui.subcategoryFilter, ui.sortField, ui.sortDirection, ui.hideNoSeed].forEach(function (el) {
      el.addEventListener("input", function () {
        state.page = 1;
        applyFilters();
      });
      el.addEventListener("change", function () {
        state.page = 1;
        applyFilters();
      });
    });

    ui.fileInput.addEventListener("change", function () {
      var file = ui.fileInput.files && ui.fileInput.files[0];
      if (!file) return;
      setStatus("Loading file...");
      readUploadFile(file)
        .then(function (payload) {
          loadFromText(payload.text, payload.sourceName || file.name);
        })
        .catch(function (err) {
          setStatus("Failed to parse file: " + err.message);
          debugLog("file load failed", {
            fileName: file && file.name,
            name: err && err.name,
            message: err && err.message,
          });
        });
    });
  }

  function loadFromText(text, sourceName, options) {
    options = options || {};
    debugLog("loadFromText start", {
      sourceName: sourceName,
      textLength: text.length,
      skipCacheWrite: !!options.skipCacheWrite,
    });
    var format = detectFormat(text, sourceName);
    debugLog("detected format", format);
    var rows;
    if (format === "jsonl") rows = parseJsonLines(text);
    else if (format === "json-array") rows = parseJsonArray(text);
    else rows = parseCsv(text);

    rows = normalizeRows(rows);
    if (!rows.length) {
      debugLog("loadFromText: no rows");
      throw new Error("No rows found in file.");
    }
    debugLog("rows parsed", rows.length);

    state.rows = rows;
    state.columns = buildColumns(rows);
    state.page = urlPrefs.page;

    setSelectOptions(ui.categoryFilter, uniqueValues(rows, "category"), true);
    setSelectOptions(ui.subcategoryFilter, uniqueValues(rows, "subcategory"), true);
    setSelectOptions(ui.sortField, state.columns, false);
    if (urlPrefs.sortField && state.columns.indexOf(urlPrefs.sortField) !== -1) {
      ui.sortField.value = urlPrefs.sortField;
    } else if (state.columns.indexOf("uploaded") !== -1) {
      ui.sortField.value = "uploaded";
    }
    ui.sortDirection.value = urlPrefs.sortDirection;

    applyFilters();

    if (!options.skipCacheWrite) {
      var persisted = saveDataToLocalStorage(text, sourceName);
      if (!persisted) {
        setStatus("Loaded " + rows.length + " rows from " + sourceName + ". Could not persist to localStorage (quota limit). ");
        return;
      }
    }

    setStatus("Loaded " + rows.length + " rows from " + sourceName + ".");
  }

  applyUrlPrefsToControls();
  setupControls();

  var cached = loadDataFromLocalStorage();
  if (cached) {
    try {
      loadFromText(cached.text, cached.sourceName || "cached", { skipCacheWrite: true });
      setStatus("Loaded " + state.rows.length + " rows from cached data.");
    } catch (err) {
      debugLog("cache restore failed", {
        name: err && err.name,
        message: err && err.message,
      });
      window.localStorage.removeItem(STORAGE_KEY);
      debugLog("cache cleared after restore failure");
    }
  }
})();
