const $ = (id) => document.getElementById(id);

let selectedGuild = "";
let selectedCategory = "";
let selectedChannel = "";
let pollTimer = null;
/** @type {Record<string, string>} date -> output folder name */
let pdfByDate = {};
let currentPdfDate = "";
let cachedResults = null;
let lightboxImages = [];
let lightboxIndex = 0;
let savedChannelLabel = "";

function selectedChannelName() {
  if ($("all-in-category").checked) {
    const cat = $("category-select").selectedOptions[0];
    return cat && cat.value ? `${cat.textContent}-all` : "all-channels";
  }
  const ch = $("channel-select").selectedOptions[0];
  return ch && ch.value ? ch.textContent : "screenshots";
}

function selectionQueryParams() {
  const params = new URLSearchParams();
  if ($("all-in-category").checked) {
    if (!$("category-select").value) return null;
    params.set("all_in_category", "true");
    params.set("channel_name", selectedChannelName());
    return params;
  }
  if (!$("channel-select").value) return null;
  params.set("channel_id", $("channel-select").value);
  params.set("channel_name", selectedChannelName());
  return params;
}

function hasChannelSelection() {
  if ($("all-in-category").checked) {
    return !!$("category-select").value;
  }
  return !!$("channel-select").value;
}

function toast(msg, type = "ok") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${type}`;
  setTimeout(() => el.classList.add("hidden"), 4000);
}

function showConfirm({ title, message, confirmLabel = "Confirm" }) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = title;
    $("confirm-message").textContent = message;
    $("confirm-ok").textContent = confirmLabel;

    const dialog = $("confirm-dialog");
    dialog.classList.remove("hidden");

    const cleanup = (result) => {
      dialog.classList.add("hidden");
      $("confirm-ok").removeEventListener("click", onOk);
      $("confirm-cancel").removeEventListener("click", onCancel);
      dialog.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKeydown);
      resolve(result);
    };

    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onBackdrop = (e) => {
      if (e.target === dialog) cleanup(false);
    };
    const onKeydown = (e) => {
      if (e.key === "Escape") cleanup(false);
    };

    $("confirm-ok").addEventListener("click", onOk);
    $("confirm-cancel").addEventListener("click", onCancel);
    dialog.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKeydown);
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => d.msg || String(d)).join(", ")
      : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function showConnected(username, tokenSaved = false, retrievalChannel = "") {
  let status = `Connected as ${username}`;
  if (retrievalChannel) {
    status += ` — last retrieved from: ${retrievalChannel}`;
  }
  $("connect-status").textContent = status;
  $("connect-status").className = "status-text ok";
  $("select-section").classList.remove("hidden");
  $("retrieve-btn").disabled = false;
  $("full-rescan-btn").disabled = false;
  if (tokenSaved) {
    $("saved-token-row").classList.remove("hidden");
    $("token-row").classList.add("hidden");
  }
}

function setJobControls({ running, paused }) {
  $("pause-btn").classList.toggle("hidden", !running || paused);
  $("continue-btn").classList.toggle("hidden", !paused);
  $("stop-btn").classList.toggle("hidden", !running && !paused);
}

function setRetrievalButtonsEnabled(enabled) {
  $("retrieve-btn").disabled = !enabled;
  $("full-rescan-btn").disabled = !enabled;
  $("reset-downloads-btn").disabled = !enabled;
}

function showTokenInput() {
  $("saved-token-row").classList.add("hidden");
  $("token-row").classList.remove("hidden");
  $("bot-token").value = "";
  $("bot-token").focus();
}

async function persistSelection() {
  if (!selectedGuild || !selectedCategory) return;
  const allInCategory = $("all-in-category").checked;
  if (!allInCategory && !selectedChannel) return;
  const channelName = selectedChannelName();
  try {
    await api("/api/save-selection", {
      method: "POST",
      body: JSON.stringify({
        guild_id: selectedGuild,
        category_id: selectedCategory,
        channel_id: selectedChannel || "",
        channel_name: channelName,
        all_in_category: allInCategory,
      }),
    });
    savedChannelLabel = channelName;
  } catch {
    /* selection save is best-effort */
  }
}

async function loadCategoriesForGuild(guildId) {
  const categories = await api(`/api/guilds/${guildId}/categories`);
  $("category-select").innerHTML = '<option value="">Select Category</option>';
  categories.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    $("category-select").appendChild(opt);
  });
}

async function loadChannelsForCategory(guildId, categoryId) {
  const channels = await api(
    `/api/guilds/${guildId}/channels?category_id=${encodeURIComponent(categoryId)}`
  );
  $("channel-select").innerHTML = '<option value="">Select Channel</option>';
  channels.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    $("channel-select").appendChild(opt);
  });
}

async function restoreSavedSelection(saved) {
  if (!saved?.guild_id) return;
  $("guild-select").value = saved.guild_id;
  selectedGuild = saved.guild_id;
  $("category-select").disabled = false;
  await loadCategoriesForGuild(saved.guild_id);
  if (saved.category_id) {
    $("category-select").value = saved.category_id;
    selectedCategory = saved.category_id;
    $("channel-select").disabled = false;
    await loadChannelsForCategory(saved.guild_id, saved.category_id);
  }
  $("all-in-category").checked = !!saved.all_in_category;
  if (saved.all_in_category) {
    selectedChannel = "";
  } else if (saved.channel_id) {
    const first = saved.channel_id.split(",")[0];
    $("channel-select").value = first;
    selectedChannel = first;
  }
  if (saved.channel_name) {
    savedChannelLabel = saved.channel_name;
  }
  await loadRetrieveDateOptions();
}

async function checkStatus() {
  try {
    const data = await api("/api/status");
    if (data.connected) {
      savedChannelLabel = data.retrieval_channel_name || "";
      showConnected(data.username, data.token_saved, data.retrieval_channel_name);
      await loadGuilds();
      await restoreSavedSelection(data.saved_selection);
      await loadResults();
      await resumeJobPollingIfNeeded();
      return;
    }
    if (data.token_saved) {
      $("connect-status").textContent =
        "Saved token is invalid or expired. Enter a new token below.";
      $("connect-status").className = "status-text err";
    }
  } catch {
    /* not connected yet */
  }
}

$("change-token-btn").addEventListener("click", showTokenInput);

$("connect-btn").addEventListener("click", async () => {
  const token = $("bot-token").value.trim();
  if (!token) {
    toast("Enter a bot token.", "err");
    return;
  }
  $("connect-btn").disabled = true;
  try {
    const data = await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    savedChannelLabel = data.retrieval_channel_name || "";
    showConnected(data.username, true, data.retrieval_channel_name);
    await loadGuilds();
    await restoreSavedSelection(data.saved_selection);
    toast("Connected. Token saved on this PC.");
  } catch (err) {
    $("connect-status").textContent = err.message;
    $("connect-status").className = "status-text err";
    toast(err.message, "err");
  } finally {
    $("connect-btn").disabled = false;
  }
});

async function loadGuilds() {
  const guilds = await api("/api/guilds");
  const sel = $("guild-select");
  sel.innerHTML = '<option value="">Select Server</option>';
  guilds.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g.id;
    opt.textContent = g.name;
    sel.appendChild(opt);
  });
}

$("guild-select").addEventListener("change", async (e) => {
  selectedGuild = e.target.value;
  selectedCategory = "";
  selectedChannel = "";
  $("category-select").disabled = !selectedGuild;
  $("channel-select").disabled = true;
  $("category-select").innerHTML = '<option value="">Select Category</option>';
  $("channel-select").innerHTML = '<option value="">Select Channel</option>';
  if (!selectedGuild) return;
  await loadCategoriesForGuild(selectedGuild);
});

$("category-select").addEventListener("change", async (e) => {
  selectedCategory = e.target.value;
  selectedChannel = "";
  $("channel-select").disabled = !selectedCategory;
  $("channel-select").innerHTML = '<option value="">Select Channel</option>';
  $("results-section").classList.add("hidden");
  if (!selectedCategory) return;
  await loadChannelsForCategory(selectedGuild, selectedCategory);
  if ($("all-in-category").checked) {
    await persistSelection();
    await loadRetrieveDateOptions();
    await loadResults();
  }
});

$("channel-select").addEventListener("change", async (e) => {
  selectedChannel = e.target.value;
  await persistSelection();
  await loadRetrieveDateOptions();
  if (selectedChannel) {
    await loadResults();
  } else {
    $("results-section").classList.add("hidden");
  }
});

$("all-in-category").addEventListener("change", async () => {
  await persistSelection();
  await loadRetrieveDateOptions();
  if (hasChannelSelection()) {
    await loadResults();
  } else {
    $("results-section").classList.add("hidden");
  }
});

async function loadRetrieveDateOptions() {
  const sel = $("retrieve-date-filter");
  const current = sel.value;
  sel.innerHTML = '<option value="all">All dates</option>';
  const params = selectionQueryParams();
  try {
    const q = params ? `?${params}` : "";
    const data = await api(`/api/dates${q}`);
    data.dates.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      sel.appendChild(opt);
    });
  } catch {
    /* no dates yet */
  }
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "Custom date...";
  sel.appendChild(custom);
  if ([...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  } else {
    sel.value = "all";
  }
  $("retrieve-custom-date-row").classList.toggle(
    "hidden",
    sel.value !== "custom"
  );
}

$("retrieve-date-filter").addEventListener("change", () => {
  $("retrieve-custom-date-row").classList.toggle(
    "hidden",
    $("retrieve-date-filter").value !== "custom"
  );
});

function selectedRetrieveDate() {
  const value = $("retrieve-date-filter").value;
  if (value === "all") return "all";
  if (value === "custom") {
    return $("retrieve-custom-date").value || "all";
  }
  return value;
}

async function startRetrieval(fullRescan) {
  selectedGuild = $("guild-select").value;
  selectedCategory = $("category-select").value;
  selectedChannel = $("channel-select").value;
  const allInCategory = $("all-in-category").checked;
  if (!selectedGuild || !selectedCategory) {
    toast("Select a server and category.", "err");
    return;
  }
  if (!allInCategory && !selectedChannel) {
    toast("Select a channel or enable all channels in category.", "err");
    return;
  }
  if ($("retrieve-date-filter").value === "custom" && !$("retrieve-custom-date").value) {
    toast("Pick a custom date or choose All dates.", "err");
    return;
  }

  $("progress-section").classList.remove("hidden");
  $("results-section").classList.add("hidden");
  setRetrievalButtonsEnabled(false);
  setJobControls({ running: true, paused: false });

  try {
    await api("/api/retrieve", {
      method: "POST",
      body: JSON.stringify({
        guild_id: selectedGuild,
        category_id: selectedCategory,
        channel_id: selectedChannel || null,
        channel_name: selectedChannelName(),
        all_in_category: allInCategory,
        full_rescan: fullRescan,
        retrieve_date: selectedRetrieveDate(),
      }),
    });
    savedChannelLabel = selectedChannelName();
    pollTimer = setInterval(pollJob, 800);
  } catch (err) {
    setRetrievalButtonsEnabled(true);
    setJobControls({ running: false, paused: false });
    toast(err.message, "err");
  }
}

$("retrieve-btn").addEventListener("click", () => startRetrieval(false));
$("full-rescan-btn").addEventListener("click", () => startRetrieval(true));

$("pause-btn").addEventListener("click", async () => {
  try {
    await api("/api/job/pause", { method: "POST" });
    setJobControls({ running: true, paused: true });
  } catch (err) {
    toast(err.message, "err");
  }
});

$("continue-btn").addEventListener("click", async () => {
  try {
    await api("/api/job/resume", { method: "POST" });
    setJobControls({ running: true, paused: false });
  } catch (err) {
    toast(err.message, "err");
  }
});

$("stop-btn").addEventListener("click", async () => {
  try {
    await api("/api/job/stop", { method: "POST" });
    toast("Stopping retrieval...");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("reset-downloads-btn").addEventListener("click", resetDownloads);

function selectedResultsDateFilter() {
  const value = $("date-filter").value;
  return value && value !== "all" ? value : null;
}

function resetConfirmMessage(label, captureDate) {
  if (captureDate) {
    return (
      `Delete downloaded screenshots for "${label}" on ${captureDate}?\n\n` +
      "Only images for this date will be removed. Other dates and channels are not affected.\n" +
      "PDFs in output/ are not affected.\n\n" +
      "This cannot be undone."
    );
  }
  const scope = $("all-in-category").checked
    ? "all channels in this category"
    : "the selected channel";
  return (
    `Delete all downloaded screenshots for "${label}"?\n\n` +
    `Everything under ${scope} will be removed. Other channels in downloads/ are not affected.\n` +
    "PDFs in output/ are not affected.\n\n" +
    "This cannot be undone."
  );
}

async function resetDownloads() {
  if (!hasChannelSelection()) {
    toast("Select a channel first.", "err");
    return;
  }
  const label = selectedChannelName();
  const captureDate = selectedResultsDateFilter();
  const ok = await showConfirm({
    title: captureDate ? `Reset ${captureDate}?` : "Reset all downloads?",
    message: resetConfirmMessage(label, captureDate),
    confirmLabel: captureDate ? "Reset date" : "Reset all",
  });
  if (!ok) return;

  const params = selectionQueryParams();
  const body = {
    channel_id: params?.get("channel_id") || null,
    channel_name: params?.get("channel_name") || label,
    all_in_category: params?.get("all_in_category") === "true",
    capture_date: captureDate,
  };

  try {
    const result = await api("/api/reset-downloads", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (captureDate) {
      delete pdfByDate[captureDate];
    } else {
      pdfByDate = {};
    }
    await loadRetrieveDateOptions();
    await loadResults();
    toast(`Reset complete. ${result.deleted} image(s) removed.`);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function resumeJobPollingIfNeeded() {
  try {
    const job = await api("/api/job");
    if (job.status !== "running" && job.status !== "paused") return;
    $("progress-section").classList.remove("hidden");
    setRetrievalButtonsEnabled(false);
    setJobControls({ running: true, paused: job.status === "paused" });
    pollTimer = setInterval(pollJob, 800);
    await pollJob();
  } catch {
    /* no active job */
  }
}

async function finishJob(job, message, messageType = "ok") {
  clearInterval(pollTimer);
  setRetrievalButtonsEnabled(true);
  setJobControls({ running: false, paused: false });
  await loadResults();
  await loadRetrieveDateOptions();
  if (message) toast(message, messageType);
}

async function pollJob() {
  const job = await api("/api/job");
  $("progress-phase").textContent = job.phase || "Retrieving Discord messages...";
  $("stat-messages").textContent = job.messages_checked.toLocaleString();
  $("stat-found").textContent = job.images_found.toLocaleString();
  $("stat-downloaded").textContent = job.images_downloaded.toLocaleString();
  $("progress-fill").style.width = `${Math.round(job.progress * 100)}%`;

  if (job.status === "paused") {
    setJobControls({ running: true, paused: true });
    return;
  }

  if (job.status === "error") {
    await finishJob(job, job.error || "Retrieval failed.", "err");
    return;
  }

  if (job.status === "stopped") {
    await finishJob(job, "Retrieval stopped. Downloaded images were kept.");
    return;
  }

  if (job.status === "complete") {
    if (job.messages_checked === 0 && job.images_found === 0) {
      await finishJob(
        job,
        "No new images found. Use Full Rescan to reload entire channel history.",
        "err"
      );
    } else {
      await finishJob(job, "Retrieval complete.");
    }
  }
}

function populateDateFilter(dates) {
  const sel = $("date-filter");
  const current = sel.value || "all";
  sel.innerHTML = '<option value="all">All dates</option>';
  dates.forEach((group) => {
    const opt = document.createElement("option");
    opt.value = group.date;
    opt.textContent = `${group.date} (${group.count} images)`;
    sel.appendChild(opt);
  });
  if ([...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  } else {
    sel.value = "all";
  }
}

function filteredDateGroups() {
  if (!cachedResults) return [];
  const filter = $("date-filter").value;
  if (filter === "all") return cachedResults.dates;
  return cachedResults.dates.filter((g) => g.date === filter);
}

function flatImagesFromGroups(groups) {
  return groups.flatMap((g) => g.images);
}

function openLightbox(images, index) {
  if (!images.length) return;
  lightboxImages = images;
  lightboxIndex = index;
  $("lightbox").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  showLightboxImage();
}

function closeLightbox() {
  $("lightbox").classList.add("hidden");
  $("lightbox-img").src = "";
  document.body.style.overflow = "";
}

function showLightboxImage() {
  const img = lightboxImages[lightboxIndex];
  if (!img) return;
  $("lightbox-img").src = `/api/image/${img.attachment_id}`;
  $("lightbox-img").alt = img.filename || "Screenshot";
  const time = new Date(img.capture_datetime).toLocaleString();
  $("lightbox-meta").innerHTML = `
    <strong>${img.filename || "Screenshot"}</strong><br>
    ${img.width} &times; ${img.height} &middot; ${img.orientation}<br>
    ${time} &middot; ${img.capture_date}
    ${img.channel_name ? `<br>Channel: ${img.channel_name}` : ""}
    <br><span>${lightboxIndex + 1} of ${lightboxImages.length}</span>
  `;
  $("lightbox-prev").classList.toggle("hidden", lightboxIndex === 0);
  $("lightbox-next").classList.toggle(
    "hidden",
    lightboxIndex === lightboxImages.length - 1
  );
}

function pdfViewUrl(folder, date) {
  return `/api/view-pdf/${encodeURIComponent(folder)}/${encodeURIComponent(date)}`;
}

function registerGeneratedPdf(date, outputFolder) {
  const folder = outputFolder.split(/[/\\]/).pop() || "";
  if (!folder) return;
  pdfByDate[date] = folder;
}

function registerGeneratedPdfsFromFiles(files) {
  pdfByDate = {};
  files.forEach((file) => {
    const parts = file.split(/[/\\]/);
    const filename = parts[parts.length - 1] || "";
    const match = filename.match(/^screenshots_(.+)\.pdf$/);
    if (!match) return;
    const folder = parts[parts.length - 2] || "";
    if (folder) pdfByDate[match[1]] = folder;
  });
}

function updatePdfActionButtons(date) {
  const hasPdf = Boolean(pdfByDate[date]);
  const escaped = CSS.escape(date);
  document
    .querySelectorAll(`.view-date[data-date="${escaped}"], .explorer-date[data-date="${escaped}"]`)
    .forEach((btn) => btn.classList.toggle("hidden", !hasPdf));
}

async function openPdfInExplorer(date) {
  const folder = pdfByDate[date];
  if (!folder) return;
  await api(
    `/api/open-pdf/${encodeURIComponent(folder)}/${encodeURIComponent(date)}`,
    { method: "POST" }
  );
}

function openPdfViewer(date) {
  const folder = pdfByDate[date];
  if (!folder) return;
  currentPdfDate = date;
  $("pdf-viewer-title").textContent = `screenshots_${date}.pdf`;
  $("pdf-viewer").src = pdfViewUrl(folder, date);
  $("pdf-viewer-section").classList.remove("hidden");
  $("pdf-viewer-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closePdfViewer() {
  $("pdf-viewer-section").classList.add("hidden");
  $("pdf-viewer").src = "";
  currentPdfDate = "";
}

function renderDateGroups() {
  const groups = filteredDateGroups();
  const container = $("date-groups");
  container.innerHTML = "";
  const allFlat = flatImagesFromGroups(groups);

  groups.forEach((group) => {
    const hasPdf = Boolean(pdfByDate[group.date]);
    const section = document.createElement("div");
    section.className = "date-group";
    section.innerHTML = `
      <div class="date-header">
        <h3>${group.date} &mdash; ${group.count} images</h3>
        <div class="button-row">
          <button class="btn primary gen-date" data-date="${group.date}">Generate PDF</button>
          <button type="button" class="btn secondary view-date${hasPdf ? "" : " hidden"}" data-date="${group.date}">View PDF</button>
          <button type="button" class="btn secondary explorer-date${hasPdf ? "" : " hidden"}" data-date="${group.date}">Open in Explorer</button>
        </div>
      </div>
      <div class="thumb-grid"></div>
    `;
    const grid = section.querySelector(".thumb-grid");
    group.images.forEach((img) => {
      const globalIndex = allFlat.findIndex(
        (i) => i.attachment_id === img.attachment_id
      );
      const card = document.createElement("div");
      card.className = "thumb-card";
      card.title = "Click to view full image";
      const time = new Date(img.capture_datetime).toLocaleString();
      card.innerHTML = `
        <img src="${img.thumbnail_url || ""}" alt="${img.filename}" loading="lazy">
        <div class="thumb-meta">
          ${img.width} &times; ${img.height}<br>
          ${img.orientation}<br>
          ${time}
        </div>
      `;
      card.addEventListener("click", () => openLightbox(allFlat, globalIndex));
      grid.appendChild(card);
    });
    container.appendChild(section);
  });

  container.querySelectorAll(".gen-date").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const date = btn.dataset.date;
      try {
        const result = await api("/api/generate-pdf", {
          method: "POST",
          body: JSON.stringify({
            date,
            channel_name: savedChannelLabel || selectedChannelName(),
          }),
        });
        registerGeneratedPdf(date, result.output_folder || "");
        updatePdfActionButtons(date);
        openPdfViewer(date);
        toast(`PDF ready for ${date}`);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });

  container.querySelectorAll(".view-date").forEach((btn) => {
    btn.addEventListener("click", () => openPdfViewer(btn.dataset.date));
  });

  container.querySelectorAll(".explorer-date").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await openPdfInExplorer(btn.dataset.date);
      } catch (err) {
        toast(err.message, "err");
      }
    });
  });
}

async function loadResults() {
  const params = selectionQueryParams();
  const path = params ? `/api/results?${params}` : "/api/results";
  const data = await api(path);
  cachedResults = data;
  if (data.dates_found > 0) {
    $("results-section").classList.remove("hidden");
    $("reset-downloads-btn").disabled = false;
  } else {
    $("results-section").classList.add("hidden");
    return;
  }
  const msgLine =
    data.messages_checked > 0
      ? `Messages checked: ${data.messages_checked.toLocaleString()}<br>`
      : "";
  $("results-summary").innerHTML = `
    <strong>Screenshots ready</strong> (${selectedChannelName()})<br>
    ${msgLine}
    Images: ${data.images_found.toLocaleString()}<br>
    Dates: ${data.dates_found}
  `;
  populateDateFilter(data.dates);
  renderDateGroups();
}

$("date-filter").addEventListener("change", renderDateGroups);

$("generate-all-btn").addEventListener("click", async () => {
  try {
    const result = await api("/api/generate-pdf", {
      method: "POST",
      body: JSON.stringify({
        date: null,
        channel_name: savedChannelLabel || selectedChannelName(),
      }),
    });
    registerGeneratedPdfsFromFiles(result.files || []);
    toast(`Generated ${result.generated} PDF(s) in ${result.output_folder}.`);
    await loadResults();
    const dates = Object.keys(pdfByDate).sort().reverse();
    if (dates.length) openPdfViewer(dates[0]);
  } catch (err) {
    toast(err.message, "err");
  }
});

$("pdf-viewer-close").addEventListener("click", closePdfViewer);
$("pdf-explorer-btn").addEventListener("click", async () => {
  if (!currentPdfDate) return;
  try {
    await openPdfInExplorer(currentPdfDate);
  } catch (err) {
    toast(err.message, "err");
  }
});

$("lightbox-close").addEventListener("click", closeLightbox);
$("lightbox").addEventListener("click", (e) => {
  if (e.target === $("lightbox")) closeLightbox();
});
$("lightbox-prev").addEventListener("click", () => {
  if (lightboxIndex > 0) {
    lightboxIndex -= 1;
    showLightboxImage();
  }
});
$("lightbox-next").addEventListener("click", () => {
  if (lightboxIndex < lightboxImages.length - 1) {
    lightboxIndex += 1;
    showLightboxImage();
  }
});
document.addEventListener("keydown", (e) => {
  if ($("lightbox").classList.contains("hidden")) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft" && lightboxIndex > 0) {
    lightboxIndex -= 1;
    showLightboxImage();
  }
  if (e.key === "ArrowRight" && lightboxIndex < lightboxImages.length - 1) {
    lightboxIndex += 1;
    showLightboxImage();
  }
});

checkStatus();
