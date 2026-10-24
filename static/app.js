"use strict";

(() => {
  const TOKEN =
    document.querySelector('meta[name="app-token"]')?.getAttribute("content") || "";

  const DEFAULT_FORMATS = { mp3: [320, 192, 128], m4a: [256, 128], original: [] };
  const TERMINAL = new Set(["completed", "completed_with_errors", "cancelled", "error"]);
  const POLL_ACTIVE = 1600;
  const POLL_IDLE = 6000;
  const DETAIL_REFRESH = 2200;

  const FORMAT_LABELS = { mp3: "MP3", m4a: "M4A", original: "Оригинал" };
  const PLATFORM_LABELS = {
    yandex: "Яндекс Музыка",
    soundcloud: "SoundCloud",
    spotify: "Spotify",
    vk: "VK Музыка",
    deezer: "Deezer",
    youtube: "YouTube",
    bandcamp: "Bandcamp",
    apple: "Apple Music",
    generic: "Другой сайт",
  };
  const KIND_LABELS = {
    track: "Трек",
    playlist: "Плейлист",
    album: "Альбом",
    artist: "Исполнитель",
    chart: "Чарт",
    set: "Сет",
  };
  const JOB_STATUS_LABELS = {
    queued: "В очереди",
    running: "Скачиваю",
    cancelling: "Останавливаю…",
    completed: "Готово",
    completed_with_errors: "Готово с ошибками",
    cancelled: "Отменено",
    error: "Ошибка",
  };
  const TRACK_STATUS_LABELS = {
    pending: "В очереди",
    preparing: "Подготовка",
    searching: "Поиск аудио",
    downloading: "Скачивание",
    converting: "Конвертация",
    tagging: "Теги",
    done: "Готово",
    skipped: "Пропущено",
    error: "Ошибка",
    cancelled: "Отменено",
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    ffmpegChip: $("ffmpeg-chip"),
    ffmpegText: $("ffmpeg-chip-text"),
    settingsBtn: $("settings-btn"),
    resolveForm: $("resolve-form"),
    urlInput: $("url-input"),
    pasteBtn: $("paste-btn"),
    resolveBtn: $("resolve-btn"),
    errorBox: $("error-box"),
    errorMessage: $("error-message"),
    errorHint: $("error-hint"),
    errorClose: $("error-close"),
    resolveLoading: $("resolve-loading"),
    preview: $("preview"),
    previewCover: $("preview-cover"),
    previewPlatform: $("preview-platform"),
    previewKind: $("preview-kind"),
    previewMode: $("preview-mode"),
    previewTitle: $("preview-title"),
    previewAuthor: $("preview-author"),
    previewMeta: $("preview-meta"),
    previewWarnings: $("preview-warnings"),
    previewCount: $("preview-count"),
    previewCountLabel: $("preview-count-label"),
    openOutputBtn: $("open-output-btn"),
    previewQuality: $("preview-quality"),
    previewFolder: $("preview-folder"),
    previewIndex: $("preview-index"),
    previewFolderPath: $("preview-folder-path"),
    selectAll: $("select-all"),
    selectionCount: $("selection-count"),
    trackFilter: $("track-filter"),
    collapseTracks: $("collapse-tracks"),
    trackList: $("track-list"),
    previewFooterNote: $("preview-footer-note"),
    downloadSelected: $("download-selected"),
    downloadAll: $("download-all"),
    jobsSection: $("jobs-section"),
    jobsList: $("jobs-list"),
    clearFinished: $("clear-finished"),
    jobTemplate: $("job-template"),
    jobTrackTemplate: $("job-track-template"),
    settingsModal: $("settings-modal"),
    settingsForm: $("settings-form"),
    settingsError: $("settings-error"),
    settingsErrorMessage: $("settings-error-message"),
    settingsErrorHint: $("settings-error-hint"),
    settingsSave: $("settings-save"),
    setOutput: $("set-output"),
    setOutputPick: $("set-output-pick"),
    setOutputOpen: $("set-output-open"),
    setQuality: $("set-quality"),
    setConcurrency: $("set-concurrency"),
    setFolder: $("set-folder"),
    setIndex: $("set-index"),
    setMaxTracks: $("set-max-tracks"),
    setMatchSource: $("set-match-source"),
    setCookies: $("set-cookies"),
    setProxy: $("set-proxy"),
    setSpotifyId: $("set-spotify-id"),
    setSpotifyMarket: $("set-spotify-market"),
    setSpotifySecret: $("set-spotify-secret"),
    setVkToken: $("set-vk-token"),
    setFfmpeg: $("set-ffmpeg"),
    settingsFfmpegStatus: $("settings-ffmpeg-status"),
    toasts: $("toasts"),
  };

  const secretControls = [
    {
      key: "spotify_client_secret",
      input: el.setSpotifySecret,
      button: document.querySelector('[data-secret-clear="spotify_client_secret"]'),
      hint: document.querySelector('[data-secret-hint="spotify_client_secret"]'),
    },
    {
      key: "vk_token",
      input: el.setVkToken,
      button: document.querySelector('[data-secret-clear="vk_token"]'),
      hint: document.querySelector('[data-secret-hint="vk_token"]'),
    },
  ];

  const state = {
    status: null,
    settings: null,
    playlist: null,
    selected: new Set(),
    resolving: false,
    resolveAbort: null,
    downloading: false,
    previewQualityTouched: false,
    jobs: new Map(),
    jobCards: new Map(),
    details: new Map(),
    detailFetchedAt: new Map(),
    detailLoading: new Set(),
    detailsOpen: new Set(),
    pollTimer: null,
    pollBusy: false,
    clearFlags: new Set(),
    settingsReturnFocus: null,
    started: false,
  };

  class ApiError extends Error {
    constructor(message, { hint = "", code = "", status = 0 } = {}) {
      super(message);
      this.name = "ApiError";
      this.hint = hint;
      this.code = code;
      this.status = status;
    }
  }

  const numberFormat = new Intl.NumberFormat("ru-RU");

  function nf(value) {
    return numberFormat.format(Number(value) || 0);
  }

  function plural(count, one, few, many) {
    const abs = Math.abs(Number(count) || 0);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
  }

  function formatSpeed(value) {
    let speed = Number(value);
    if (!Number.isFinite(speed) || speed <= 0) return "";
    const units = ["Б/с", "КБ/с", "МБ/с", "ГБ/с"];
    let unit = units[0];
    for (const candidate of units) {
      unit = candidate;
      if (speed < 1024 || candidate === units[units.length - 1]) break;
      speed /= 1024;
    }
    return `${speed.toLocaleString("ru-RU", { maximumFractionDigits: speed < 10 ? 1 : 0 })} ${unit}`;
  }

  function formatSeconds(value) {
    const total = Math.max(0, Math.round(Number(value) || 0));
    if (total < 60) return `${total} с`;
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    if (minutes < 60) return `${minutes} мин${seconds ? ` ${seconds} с` : ""}`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return `${hours} ч${rest ? ` ${rest} мин` : ""}`;
  }

  function formatClock(timestamp) {
    if (!timestamp) return "";
    try {
      return new Date(timestamp * 1000).toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }

  function searchKey(value) {
    return String(value || "")
      .toLocaleLowerCase("ru")
      .normalize("NFKD")
      .replace(/\p{M}/gu, "")
      .trim();
  }

  function sanitizeFilename(value, maxLength = 90, fallback = "Плейлист") {
    let name = String(value || "")
      .normalize("NFC")
      .replace(/[\x00-\x1f\x7f<>:"/\\|?*]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^[ .]+|[ .]+$/g, "");
    if (!name) name = fallback;
    if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$/i.test(name)) name = `_${name}`;
    if (name.length > maxLength) name = name.slice(0, maxLength).replace(/[ .]+$/g, "");
    return name || fallback;
  }

  function joinPath(base, name) {
    const separator = String(base).includes("\\") ? "\\" : "/";
    const trimmed = String(base).replace(/[\\/]+$/, "") || String(base);
    return `${trimmed}${separator}${name}`;
  }

  function formatLabel(format, quality) {
    if (!format) return "Формат не задан";
    const label = FORMAT_LABELS[format] || String(format).toUpperCase();
    if (format === "original") return "Оригинал";
    return quality ? `${label} ${quality} кбит/с` : label;
  }

  function platformLabel(platform) {
    return PLATFORM_LABELS[platform] || platform || "Сайт";
  }

  function shortTitle(value, limit = 70) {
    const text = String(value || "Без названия");
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  function parseQuality(value) {
    const [format, rawQuality] = String(value || "").split(":");
    let quality = Number.parseInt(rawQuality, 10);
    if (!Number.isFinite(quality) || quality < 0) quality = 0;
    return { format: format || "mp3", quality };
  }

  async function api(path, { method = "GET", body, signal } = {}) {
    const headers = { Accept: "application/json" };
    let payload;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    if (method !== "GET" && method !== "HEAD") headers["X-App-Token"] = TOKEN;

    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body: payload,
        credentials: "same-origin",
        cache: "no-store",
        signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      throw new ApiError("Не удалось связаться с приложением.", {
        hint: "Проверьте, что окно PLDL запущено, и обновите страницу.",
      });
    }

    let data = null;
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.includes("application/json")) {
      try {
        data = await response.json();
      } catch {
        data = null;
      }
    } else {
      try {
        const text = await response.text();
        if (text) {
          data = { ok: false, error: { message: text.slice(0, 400) } };
        }
      } catch {
        data = null;
      }
    }

    if (!response.ok || !data || data.ok === false) {
      const info = data?.error || {};
      throw new ApiError(info.message || `Сервер ответил ошибкой ${response.status}.`, {
        hint: info.hint || "",
        code: info.code || "",
        status: response.status,
      });
    }
    return data;
  }

  function dismissToast(node) {
    if (!node || node.classList.contains("leaving")) return;
    clearTimeout(node._timer);
    node.classList.add("leaving");
    window.setTimeout(() => node.remove(), 240);
  }

  function toast(message, type = "info", title = "", timeout = 5200) {
    if (!el.toasts) return;
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    const strong = document.createElement("strong");
    strong.textContent = title || message;
    node.append(strong);
    if (title) {
      const detail = document.createElement("p");
      detail.textContent = message;
      node.append(detail);
    }
    node.addEventListener("click", () => dismissToast(node));
    el.toasts.append(node);
    while (el.toasts.children.length > 5) dismissToast(el.toasts.firstElementChild);
    node._timer = window.setTimeout(() => dismissToast(node), timeout);
  }

  function showError(error) {
    const message =
      typeof error === "string" ? error : error?.message || "Неизвестная ошибка.";
    const hint = typeof error === "string" ? "" : error?.hint || "";
    el.errorMessage.textContent = message;
    el.errorHint.textContent = hint;
    el.errorHint.classList.toggle("hidden", !hint);
    el.errorBox.classList.remove("hidden");
  }

  function hideError() {
    el.errorBox.classList.add("hidden");
  }

  function updateFfmpeg(ffmpeg) {
    const found = Boolean(ffmpeg?.found);
    const known = ffmpeg !== null && ffmpeg !== undefined;
    el.ffmpegChip.classList.toggle("ok", found);
    el.ffmpegChip.classList.toggle("warn", known && !found);
    el.ffmpegText.textContent = !known
      ? "FFmpeg: неизвестно"
      : found
        ? "FFmpeg: готов"
        : "FFmpeg: не найден";
    el.ffmpegChip.title = found
      ? `FFmpeg найден: ${ffmpeg.path || ""}`
      : "FFmpeg не найден. Нажмите, чтобы указать путь в настройках.";
  }

  function getFormats() {
    return state.settings?.formats || DEFAULT_FORMATS;
  }

  function fillQualitySelect(select, format, quality) {
    if (!select) return;
    const formats = getFormats();
    const wanted = `${format}:${quality ?? ""}`;
    const fragment = document.createDocumentFragment();

    for (const [fmt, qualities] of Object.entries(formats)) {
      const label = FORMAT_LABELS[fmt] || String(fmt).toUpperCase();
      if (!Array.isArray(qualities) || qualities.length === 0) {
        const option = document.createElement("option");
        option.value = `${fmt}:0`;
        option.textContent = `${label} · без перекодирования`;
        fragment.append(option);
        continue;
      }
      for (const bitrate of qualities) {
        const option = document.createElement("option");
        option.value = `${fmt}:${bitrate}`;
        option.textContent = `${label} · ${bitrate} кбит/с`;
        fragment.append(option);
      }
    }

    select.replaceChildren(fragment);
    if ([...select.options].some((option) => option.value === wanted)) {
      select.value = wanted;
    } else if (state.settings) {
      const fallback = `${state.settings.format}:${state.settings.quality}`;
      if ([...select.options].some((option) => option.value === fallback)) {
        select.value = fallback;
      }
    }
    if (!select.value && select.options.length) select.selectedIndex = 0;
  }

  function applyStatus(data) {
    state.status = data;
    state.settings = data.settings || state.settings;
    updateFfmpeg(data.ffmpeg);
    if (state.playlist && !state.previewQualityTouched) {
      fillQualitySelect(
        el.previewQuality,
        state.settings?.format || "mp3",
        state.settings?.quality ?? 320,
      );
    }
    updateFolderNote();
    updateFooterNote();
  }

  async function loadStatus({ quiet = false } = {}) {
    try {
      const data = await api("/api/status");
      applyStatus(data);
      return data;
    } catch (error) {
      updateFfmpeg(null);
      if (!quiet) showError(error);
      return null;
    }
  }

  function setResolveLoading(loading) {
    el.resolveBtn.disabled = loading;
    const label = el.resolveBtn.querySelector(".btn-label");
    const spinner = el.resolveBtn.querySelector(".spinner");
    if (label) label.textContent = loading ? "Разбираю…" : "Разобрать";
    if (spinner) spinner.classList.toggle("hidden", !loading);
    el.resolveLoading.classList.toggle("hidden", !loading);
    el.resolveForm.setAttribute("aria-busy", loading ? "true" : "false");
  }

  function clearPreview() {
    state.playlist = null;
    state.selected.clear();
    state.previewQualityTouched = false;
    el.preview.classList.add("hidden");
    el.trackList.replaceChildren();
    el.previewFolderPath.textContent = "";
    el.previewFooterNote.textContent = "";
    el.selectionCount.textContent = "Выбрано: 0";
    el.selectAll.checked = false;
    el.selectAll.indeterminate = false;
    el.downloadSelected.disabled = true;
    el.downloadAll.disabled = true;
    el.previewCover.removeAttribute("src");
    el.previewCover.hidden = true;
    el.previewWarnings.replaceChildren();
  }

  function updateFolderNote() {
    if (!state.playlist) {
      el.previewFolderPath.textContent = "";
      return;
    }
    const destination = previewDestination();
    el.previewFolderPath.textContent = destination
      ? `Куда: ${destination}`
      : "Папка не задана — откройте настройки";
    el.previewFolderPath.title = destination || "";
  }

  function previewDestination() {
    if (!state.playlist) return "";
    const base = state.settings?.output_dir || "";
    if (!base) return "";
    const separate =
      el.previewFolder.checked &&
      (state.playlist.track_count > 1 || state.playlist.kind !== "track");
    if (!separate) return base;
    return joinPath(base, sanitizeFilename(state.playlist.title, 90, "Плейлист"));
  }

  function updateFooterNote() {
    if (!state.playlist) {
      el.previewFooterNote.textContent = "";
      return;
    }
    const { format, quality } = parseQuality(el.previewQuality.value);
    const selected = state.selected.size;
    const total = state.playlist.track_count || state.playlist.tracks.length;
    const parts = [
      selected
        ? `Выбрано ${nf(selected)} из ${nf(total)}`
        : "Ничего не выбрано",
      formatLabel(format, quality),
    ];
    el.previewFooterNote.textContent = parts.join(" · ");
  }

  function renderPreview(playlist) {
    state.playlist = playlist;
    state.selected = new Set((playlist.tracks || []).map((track) => track.index));
    state.previewQualityTouched = false;

    const cover = playlist.cover;
    el.previewCover.onerror = () => {
      el.previewCover.hidden = true;
    };
    if (cover) {
      el.previewCover.hidden = false;
      if (el.previewCover.src !== cover) el.previewCover.src = cover;
    } else {
      el.previewCover.removeAttribute("src");
      el.previewCover.hidden = true;
    }

    const platform = playlist.platform || "generic";
    el.previewPlatform.className = `badge platform-${platform}`;
    el.previewPlatform.textContent =
      playlist.platform_label || platformLabel(platform);
    el.previewKind.textContent = KIND_LABELS[playlist.kind] || playlist.kind || "Ссылка";
    el.previewMode.textContent =
      playlist.mode_label ||
      (playlist.download_mode === "match" ? "Поиск аудио" : "Прямая загрузка");
    el.previewTitle.textContent = playlist.title || "Без названия";
    el.previewAuthor.textContent = playlist.author || "Автор не указан";

    const count = playlist.track_count || playlist.tracks.length;
    el.previewCount.textContent = nf(count);
    el.previewCountLabel.textContent = plural(count, "трек", "трека", "треков");
    const meta = [`${nf(count)} ${plural(count, "трек", "трека", "треков")}`];
    if (playlist.total_duration > 0) meta.push(playlist.total_duration_label);
    if (playlist.resolved_in != null) {
      meta.push(`разобрано за ${formatSeconds(playlist.resolved_in)}`);
    }
    el.previewMeta.textContent = meta.join(" · ");

    el.previewWarnings.replaceChildren(
      ...(playlist.warnings || []).map((warning) => {
        const node = document.createElement("div");
        node.className = "warning";
        node.textContent = warning;
        return node;
      }),
    );

    fillQualitySelect(
      el.previewQuality,
      state.settings?.format || "mp3",
      state.settings?.quality ?? 320,
    );
    el.previewFolder.checked = state.settings
      ? Boolean(state.settings.playlist_folder)
      : true;
    el.previewIndex.checked = state.settings
      ? Boolean(state.settings.index_prefix)
      : true;

    el.trackFilter.value = "";
    setTracksCollapsed(false);
    renderTracks();
    updateFolderNote();
    updateFooterNote();
    el.preview.classList.remove("hidden");
    window.requestAnimationFrame(() => {
      el.preview.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function handleResolve(event) {
    event?.preventDefault();
    if (state.resolving) return;
    hideError();
    const url = el.urlInput.value.trim();
    if (!url) {
      showError(new ApiError("Вставьте ссылку на плейлист, альбом, трек или профиль."));
      el.urlInput.focus();
      return;
    }

    state.resolving = true;
    state.resolveAbort?.abort();
    const controller = new AbortController();
    state.resolveAbort = controller;
    clearPreview();
    setResolveLoading(true);

    try {
      const data = await api("/api/resolve", {
        method: "POST",
        body: { url },
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      renderPreview(data.playlist);
      el.urlInput.blur();
    } catch (error) {
      if (error?.name === "AbortError") return;
      showError(error);
      el.errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } finally {
      if (state.resolveAbort === controller) {
        state.resolveAbort = null;
        state.resolving = false;
        setResolveLoading(false);
      }
    }
  }

  function setTracksCollapsed(collapsed) {
    el.trackList.classList.toggle("collapsed", collapsed);
    el.collapseTracks.classList.toggle("collapsed", collapsed);
    const label = el.collapseTracks.querySelector("span");
    if (label) label.textContent = collapsed ? "Развернуть список" : "Свернуть список";
  }

  function buildTrackRow(track) {
    const row = document.createElement("label");
    row.className = "track-row";
    row.dataset.index = String(track.index);
    const checked = state.selected.has(track.index);
    row.classList.toggle("checked", checked);
    row.classList.toggle("unavailable", !track.available);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = checked;
    checkbox.dataset.trackIndex = String(track.index);
    const trackName = `${track.artist ? `${track.artist} — ` : ""}${track.title}`;
    checkbox.setAttribute("aria-label", `Скачать ${trackName}`);

    const number = document.createElement("span");
    number.className = "track-num";
    number.textContent = String(track.index + 1).padStart(2, "0");

    const main = document.createElement("div");
    main.className = "track-main";
    const title = document.createElement("span");
    title.className = "track-title";
    title.textContent = track.title || "Без названия";
    title.title = track.album ? `${track.title} · ${track.album}` : track.title || "";
    const artist = document.createElement("span");
    artist.className = "track-artist";
    artist.textContent = track.artist || "Неизвестный исполнитель";
    main.append(title, artist);

    const duration = document.createElement("span");
    duration.className = "track-duration";
    duration.textContent = track.duration_label || "--:--";

    row.append(checkbox, number, main, duration);
    return row;
  }

  function renderTracks() {
    if (!state.playlist) {
      el.trackList.replaceChildren();
      syncSelectionUi();
      return;
    }
    const query = searchKey(el.trackFilter.value);
    const tracks = state.playlist.tracks.filter((track) => {
      if (!query) return true;
      return searchKey(`${track.artist} ${track.title} ${track.album}`).includes(query);
    });

    const fragment = document.createDocumentFragment();
    if (!tracks.length) {
      const empty = document.createElement("div");
      empty.className = "track-empty";
      empty.textContent = query
        ? "Ничего не найдено. Измените фильтр."
        : "В этом списке нет треков.";
      fragment.append(empty);
    } else {
      for (const track of tracks) fragment.append(buildTrackRow(track));
    }
    el.trackList.replaceChildren(fragment);
    syncSelectionUi();
  }

  function syncVisibleRows() {
    for (const row of el.trackList.querySelectorAll(".track-row")) {
      const index = Number(row.dataset.index);
      const checked = state.selected.has(index);
      row.classList.toggle("checked", checked);
      const checkbox = row.querySelector('input[type="checkbox"]');
      if (checkbox) checkbox.checked = checked;
    }
  }

  function syncSelectionUi() {
    const total = state.playlist
      ? state.playlist.track_count || state.playlist.tracks.length
      : 0;
    const selected = state.selected.size;
    el.selectionCount.textContent = `Выбрано: ${nf(selected)} из ${nf(total)}`;
    el.selectAll.checked = total > 0 && selected === total;
    el.selectAll.indeterminate = selected > 0 && selected < total;
    el.downloadSelected.disabled = state.downloading || selected === 0;
    el.downloadAll.disabled = state.downloading || total === 0;
    updateFooterNote();
  }

  function handleTrackChange(event) {
    const checkbox = event.target.closest('input[type="checkbox"]');
    if (!checkbox || !el.trackList.contains(checkbox)) return;
    const index = Number(checkbox.dataset.trackIndex);
    if (!Number.isInteger(index)) return;
    if (checkbox.checked) state.selected.add(index);
    else state.selected.delete(index);
    const row = checkbox.closest(".track-row");
    if (row) row.classList.toggle("checked", checkbox.checked);
    syncSelectionUi();
  }

  function handleSelectAll() {
    if (!state.playlist) return;
    if (el.selectAll.checked) {
      for (const track of state.playlist.tracks) state.selected.add(track.index);
    } else {
      state.selected.clear();
    }
    syncVisibleRows();
    syncSelectionUi();
  }

  function setButtonBusy(button, busy, label) {
    if (!button) return;
    const span = button.querySelector(".btn-label") || button.querySelector("span");
    if (busy) {
      if (span && !button.dataset.originalLabel) {
        button.dataset.originalLabel = span.textContent;
      }
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      if (span && label) span.textContent = label;
    } else {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (span && button.dataset.originalLabel) {
        span.textContent = button.dataset.originalLabel;
        delete button.dataset.originalLabel;
      }
    }
  }

  async function startDownload(mode) {
    if (state.downloading || !state.playlist) return;
    const indices =
      mode === "selected"
        ? [...state.selected].sort((left, right) => left - right)
        : state.playlist.tracks.map((track) => track.index);
    if (!indices.length) {
      toast("Отметьте хотя бы один трек.", "warning", "Ничего не выбрано");
      return;
    }

    const { format, quality } = parseQuality(el.previewQuality.value);
    const body = {
      playlist_id: state.playlist.id,
      indices,
      format,
      quality,
      playlist_folder: el.previewFolder.checked,
      index_prefix: el.previewIndex.checked,
    };
    if (state.settings?.concurrency) body.concurrency = state.settings.concurrency;

    const button = mode === "selected" ? el.downloadSelected : el.downloadAll;
    state.downloading = true;
    syncSelectionUi();
    setButtonBusy(button, true, "Запускаю…");
    try {
      const data = await api("/api/download", { method: "POST", body });
      hideError();
      addJob(data.job);
      const countLabel = plural(indices.length, "трек", "трека", "треков");
      toast(
        `${nf(indices.length)} ${countLabel} · ${formatLabel(format, quality)}`,
        "success",
        "Загрузка началась",
      );
      el.jobsSection.classList.remove("hidden");
      window.setTimeout(() => {
        el.jobsSection.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 60);
      schedulePoll(300);
    } catch (error) {
      showError(error);
      el.errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } finally {
      state.downloading = false;
      setButtonBusy(button, false);
      syncSelectionUi();
    }
  }

  function addJob(job) {
    if (!job?.id) return;
    state.jobs.set(job.id, job);
    const card = ensureJobCard(job);
    updateJobCard(job);
    reorderJobCards();
    el.jobsSection.classList.remove("hidden");
  }

  function setOptionalText(node, text) {
    if (!node) return;
    node.textContent = text || "";
    node.hidden = !text;
  }

  function ensureJobCard(job) {
    let card = state.jobCards.get(job.id);
    if (card) return card;
    card = el.jobTemplate.content.firstElementChild.cloneNode(true);
    card.dataset.jobId = job.id;
    el.jobsList.append(card);
    state.jobCards.set(job.id, card);
    return card;
  }

  function jobSub(job) {
    const total = job.total ?? job.playlist_total ?? 0;
    const parts = [
      formatLabel(job.format, job.quality),
      `${nf(total)} ${plural(total, "трек", "трека", "треков")}`,
    ];
    parts.push(job.mode === "match" ? "поиск аудио" : "прямая загрузка");
    if (job.created_at) parts.push(`в ${formatClock(job.created_at)}`);
    return parts.join(" · ");
  }

  function jobElapsed(job) {
    if (!job.elapsed) return "";
    if (!job.started_at && !TERMINAL.has(job.status)) return "в очереди";
    return TERMINAL.has(job.status)
      ? `за ${formatSeconds(job.elapsed)}`
      : `прошло ${formatSeconds(job.elapsed)}`;
  }

  function updateJobCard(job) {
    const card = state.jobCards.get(job.id);
    if (!card) return;
    const terminal = TERMINAL.has(job.status);
    card.dataset.status = job.status || "";

    const platform = job.platform || "generic";
    const badge = card.querySelector(".job-platform");
    if (badge) {
      badge.className = `badge job-platform platform-${platform}`;
      badge.textContent = platformLabel(platform);
    }
    const title = card.querySelector(".job-title");
    if (title) title.textContent = job.title || "Без названия";
    const sub = card.querySelector(".job-sub");
    if (sub) sub.textContent = jobSub(job);

    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const percent = card.querySelector(".job-percent");
    if (percent) percent.textContent = `${Math.round(progress)}%`;
    const bar = card.querySelector(".progress-bar");
    if (bar) bar.style.width = `${progress}%`;

    const status = card.querySelector(".job-status");
    if (status) {
      status.dataset.status = job.status || "";
      status.textContent = JOB_STATUS_LABELS[job.status] || job.status || "";
    }

    const done = (Number(job.done) || 0) + (Number(job.skipped) || 0);
    let counts = `Готово ${nf(done)} из ${nf(job.total)}`;
    if (job.failed) counts += ` · ошибок ${nf(job.failed)}`;
    if (job.cancelled) counts += ` · отменено ${nf(job.cancelled)}`;
    setOptionalText(card.querySelector(".job-counts"), counts);
    setOptionalText(
      card.querySelector(".job-speed"),
      !terminal && job.speed ? formatSpeed(job.speed) : "",
    );
    setOptionalText(
      card.querySelector(".job-eta"),
      !terminal && job.eta != null ? `осталось ${formatSeconds(job.eta)}` : "",
    );
    setOptionalText(card.querySelector(".job-elapsed"), jobElapsed(job));

    const current = card.querySelector(".job-current");
    if (current) {
      current.textContent = job.current ? `Сейчас: ${job.current}` : "";
      current.hidden = !job.current;
    }

    const cancel = card.querySelector('[data-job-action="cancel"]');
    if (cancel) {
      cancel.hidden = terminal;
      cancel.disabled = job.status === "cancelling";
    }
    const retry = card.querySelector('[data-job-action="retry"]');
    if (retry) {
      retry.hidden =
        !terminal || (Number(job.failed) || 0) + (Number(job.cancelled) || 0) === 0;
    }

    const detailsOpen = state.detailsOpen.has(job.id);
    card.classList.toggle("details-open", detailsOpen);
    const detailsBox = card.querySelector(".job-details");
    if (detailsBox) detailsBox.classList.toggle("hidden", !detailsOpen);
    const detailsLabel = card.querySelector('[data-job-action="details"] span');
    if (detailsLabel) detailsLabel.textContent = detailsOpen ? "Свернуть" : "Подробнее";
  }

  function reorderJobCards() {
    const jobs = [...state.jobs.values()].sort(
      (left, right) => (Number(right.created_at) || 0) - (Number(left.created_at) || 0),
    );
    const cards = jobs.map((job) => state.jobCards.get(job.id)).filter(Boolean);
    cards.forEach((card, index) => {
      if (el.jobsList.children[index] !== card) {
        el.jobsList.insertBefore(card, el.jobsList.children[index] || null);
      }
    });
  }

  function trackDetail(track) {
    switch (track.status) {
      case "done":
        return track.filename || "готово";
      case "skipped":
        return track.filename ? `уже был скачан · ${track.filename}` : "уже был скачан";
      case "error":
        return [track.error, track.hint].filter(Boolean).join(" — ") || "ошибка";
      case "cancelled":
        return "отменено";
      case "downloading": {
        const bits = [];
        if (track.progress) bits.push(`${Math.round(track.progress)}%`);
        if (track.speed) bits.push(formatSpeed(track.speed));
        if (track.eta != null) bits.push(`осталось ${formatSeconds(track.eta)}`);
        return bits.join(" · ") || "скачивание…";
      }
      case "searching":
        return "поиск аудио…";
      case "preparing":
        return "подготовка…";
      case "converting":
        return "конвертация…";
      case "tagging":
        return "теги и обложка…";
      case "pending":
      case "queued":
        return track.duration_label ? `в очереди · ${track.duration_label}` : "в очереди";
      default:
        return TRACK_STATUS_LABELS[track.status] || track.status || "";
    }
  }

  function setDetailsPlaceholder(card, text) {
    const list = card.querySelector(".job-track-list");
    if (!list) return;
    const empty = document.createElement("div");
    empty.className = "track-empty";
    empty.textContent = text;
    list.replaceChildren(empty);
  }

  function renderJobDetails(card, full) {
    const list = card.querySelector(".job-track-list");
    if (!list) return;
    const tracks = Array.isArray(full.tracks) ? full.tracks : [];
    const scrollTop = list.scrollTop;
    const fragment = document.createDocumentFragment();

    if (!tracks.length) {
      const empty = document.createElement("div");
      empty.className = "track-empty";
      empty.textContent = "Список треков пока недоступен.";
      fragment.append(empty);
    } else {
      for (const track of tracks) {
        const row = el.jobTrackTemplate.content.firstElementChild.cloneNode(true);
        row.classList.toggle("error", track.status === "error");
        const dot = row.querySelector(".job-track-status");
        if (dot) {
          dot.dataset.status = track.status || "pending";
          dot.title = TRACK_STATUS_LABELS[track.status] || track.status || "";
        }
        const name = row.querySelector(".job-track-name");
        if (name) {
          name.textContent = `${track.artist ? `${track.artist} — ` : ""}${track.title}`;
          name.title = name.textContent;
        }
        const detail = row.querySelector(".job-track-detail");
        if (detail) {
          detail.textContent = trackDetail(track);
          detail.title = detail.textContent;
        }
        if (track.error || track.hint) {
          row.title = [track.error, track.hint].filter(Boolean).join("\n");
        }
        fragment.append(row);
      }
    }
    list.replaceChildren(fragment);
    list.scrollTop = scrollTop;

    const logs = Array.isArray(full.logs) ? full.logs : [];
    const pre = card.querySelector(".job-logs pre");
    if (pre) {
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 32;
      pre.textContent = logs.length ? logs.join("\n") : "Журнал пока пуст.";
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
    const summary = card.querySelector(".job-logs summary");
    if (summary) {
      summary.textContent = logs.length
        ? `Журнал загрузки (${logs.length})`
        : "Журнал загрузки";
    }
  }

  async function loadJobDetails(jobId, force = false) {
    if (!jobId || state.detailLoading.has(jobId)) return;
    const card = state.jobCards.get(jobId);
    if (!card || !state.detailsOpen.has(jobId)) return;

    const last = state.detailFetchedAt.get(jobId) || 0;
    if (!force && Date.now() - last < DETAIL_REFRESH) return;

    state.detailLoading.add(jobId);
    if (!state.details.has(jobId)) setDetailsPlaceholder(card, "Загружаю треки…");
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      state.details.set(jobId, data.job);
      state.detailFetchedAt.set(jobId, Date.now());
      const currentCard = state.jobCards.get(jobId);
      if (currentCard && state.detailsOpen.has(jobId)) {
        renderJobDetails(currentCard, data.job);
      }
    } catch (error) {
      if (error?.status === 404) {
        removeJobCard(jobId);
        toast("Задание больше недоступно.", "warning");
      } else {
        setDetailsPlaceholder(card, error?.message || "Не удалось загрузить детали.");
      }
    } finally {
      state.detailLoading.delete(jobId);
    }
  }

  function toggleJobDetails(jobId) {
    const card = state.jobCards.get(jobId);
    if (!card) return;
    const open = !state.detailsOpen.has(jobId);
    if (open) state.detailsOpen.add(jobId);
    else state.detailsOpen.delete(jobId);

    card.classList.toggle("details-open", open);
    card.querySelector(".job-details")?.classList.toggle("hidden", !open);
    const detailsLabel = card.querySelector('[data-job-action="details"] span');
    if (detailsLabel) detailsLabel.textContent = open ? "Свернуть" : "Подробнее";

    if (open) {
      if (state.details.has(jobId)) {
        renderJobDetails(card, state.details.get(jobId));
      } else {
        setDetailsPlaceholder(card, "Загружаю треки…");
      }
      loadJobDetails(jobId, true);
    }
  }

  function notifyJobFinished(job) {
    const done = (Number(job.done) || 0) + (Number(job.skipped) || 0);
    const title = shortTitle(job.title);
    const counts = `${nf(done)} из ${nf(job.total)}`;
    if (job.status === "completed") {
      toast(`«${title}» — ${counts}.`, "success", "Загрузка завершена");
    } else if (job.status === "completed_with_errors") {
      toast(
        `«${title}» — ${counts}, ошибок: ${nf(job.failed)}. Подробности — в «Подробнее».`,
        "warning",
        "Загрузка завершена с ошибками",
      );
    } else if (job.status === "error") {
      toast(`«${title}» — загрузка не удалась. Откройте журнал задания.`, "error", "Ошибка загрузки");
    } else if (job.status === "cancelled") {
      toast(`«${title}» — загрузка остановлена.`, "info", "Отменено");
    }
  }

  function applyJobs(jobs) {
    const previous = state.jobs;
    const next = new Map();
    const terminalTransitions = [];

    for (const snapshot of jobs) {
      if (!snapshot?.id) continue;
      const old = previous.get(snapshot.id);
      if (old && !TERMINAL.has(old.status) && TERMINAL.has(snapshot.status)) {
        terminalTransitions.push(snapshot);
      }
      next.set(snapshot.id, snapshot);
      const card = ensureJobCard(snapshot);
      updateJobCard(snapshot);
    }

    for (const [jobId, card] of [...state.jobCards]) {
      if (next.has(jobId)) continue;
      card.remove();
      state.jobCards.delete(jobId);
      state.details.delete(jobId);
      state.detailFetchedAt.delete(jobId);
      state.detailsOpen.delete(jobId);
      state.detailLoading.delete(jobId);
    }

    state.jobs = next;
    reorderJobCards();
    el.jobsSection.classList.toggle("hidden", next.size === 0);

    for (const job of terminalTransitions) {
      notifyJobFinished(job);
      if (state.detailsOpen.has(job.id)) loadJobDetails(job.id, true);
    }
  }

  function hasActiveJobs() {
    return [...state.jobs.values()].some((job) => !TERMINAL.has(job.status));
  }

  function schedulePoll(delay) {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    const wait =
      delay ?? (hasActiveJobs() ? POLL_ACTIVE : POLL_IDLE);
    state.pollTimer = window.setTimeout(() => {
      state.pollTimer = null;
      pollJobs();
    }, wait);
  }

  async function pollJobs() {
    if (state.pollBusy) {
      schedulePoll(POLL_ACTIVE);
      return;
    }
    if (document.hidden) {
      schedulePoll(POLL_IDLE);
      return;
    }
    state.pollBusy = true;
    try {
      const data = await api("/api/jobs");
      applyJobs(Array.isArray(data.jobs) ? data.jobs : []);
      for (const jobId of [...state.detailsOpen]) {
        const job = state.jobs.get(jobId);
        if (!job) continue;
        const last = state.detailFetchedAt.get(jobId) || 0;
        if (!TERMINAL.has(job.status) && Date.now() - last >= DETAIL_REFRESH) {
          loadJobDetails(jobId, false);
        }
      }
      schedulePoll(hasActiveJobs() ? POLL_ACTIVE : POLL_IDLE);
    } catch {
      schedulePoll(POLL_IDLE);
    } finally {
      state.pollBusy = false;
    }
  }

  function removeJobCard(jobId) {
    const card = state.jobCards.get(jobId);
    if (card) card.remove();
    state.jobCards.delete(jobId);
    state.jobs.delete(jobId);
    state.details.delete(jobId);
    state.detailFetchedAt.delete(jobId);
    state.detailsOpen.delete(jobId);
    state.detailLoading.delete(jobId);
    if (state.jobCards.size === 0) el.jobsSection.classList.add("hidden");
  }

  async function openFolder(path) {
    try {
      await api("/api/open-folder", {
        method: "POST",
        body: path ? { path } : {},
      });
    } catch (error) {
      toast(error?.message || "Не удалось открыть папку.", "error", "Папка");
    }
  }

  async function cancelJob(jobId) {
    const card = state.jobCards.get(jobId);
    const button = card?.querySelector('[data-job-action="cancel"]');
    if (button) button.disabled = true;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
      });
      state.jobs.set(jobId, data.job);
      if (card) updateJobCard(data.job);
      toast("Останавливаю текущие загрузки…", "info", "Отмена");
      schedulePoll(300);
    } catch (error) {
      toast(error?.message || "Не удалось остановить задание.", "error", "Ошибка");
      if (button) button.disabled = false;
    }
  }

  async function retryJob(jobId) {
    const card = state.jobCards.get(jobId);
    const button = card?.querySelector('[data-job-action="retry"]');
    if (button) button.disabled = true;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
        method: "POST",
      });
      addJob(data.job);
      toast("Ошибочные и отменённые треки будут загружены заново.", "success", "Повторная попытка");
      schedulePoll(300);
    } catch (error) {
      toast(error?.message || "Не удалось повторить загрузку.", "error", "Ошибка");
      if (button) button.disabled = false;
    }
  }

  async function clearFinished() {
    if (!state.jobCards.size) return;
    el.clearFinished.disabled = true;
    try {
      const data = await api("/api/jobs/clear", { method: "POST", body: {} });
      const removed = Array.isArray(data.removed) ? data.removed : [];
      for (const jobId of removed) removeJobCard(jobId);
      if (removed.length) {
        toast(`Убрано заданий: ${nf(removed.length)}.`, "success", "Готово");
      } else {
        toast("Завершённых или отменённых загрузок пока нет.", "info");
      }
      schedulePoll(300);
    } catch (error) {
      toast(error?.message || "Не удалось очистить список.", "error", "Ошибка");
    } finally {
      el.clearFinished.disabled = false;
    }
  }

  async function handleJobsClick(event) {
    const button = event.target.closest("[data-job-action]");
    if (!button) return;
    const card = button.closest(".job-card");
    const jobId = card?.dataset.jobId;
    if (!jobId) return;
    const action = button.dataset.jobAction;
    if (action === "details") {
      toggleJobDetails(jobId);
    } else if (action === "cancel") {
      await cancelJob(jobId);
    } else if (action === "retry") {
      await retryJob(jobId);
    } else if (action === "open") {
      await openFolder(state.jobs.get(jobId)?.playlist_dir || "");
    }
  }

  function hideSettingsError() {
    el.settingsError.classList.add("hidden");
  }

  function showSettingsError(error) {
    const message =
      typeof error === "string" ? error : error?.message || "Неизвестная ошибка.";
    const hint = typeof error === "string" ? "" : error?.hint || "";
    el.settingsErrorMessage.textContent = message;
    el.settingsErrorHint.textContent = hint;
    el.settingsErrorHint.classList.toggle("hidden", !hint);
    el.settingsError.classList.remove("hidden");
    el.settingsError.scrollIntoView({ block: "nearest" });
  }

  function updateSecretUi() {
    const flags = state.settings?.secrets_set || {};
    for (const item of secretControls) {
      if (!item.input || !item.button || !item.hint) continue;
      const isSet = Boolean(flags[item.key]);
      const clearing = state.clearFlags.has(item.key);
      const typed = item.input.value.trim().length > 0;

      item.button.classList.toggle("hidden", !isSet || clearing || typed);
      item.input.placeholder = clearing
        ? "Будет удалён после сохранения"
        : isSet && !typed
          ? "Сохранён — оставьте пустым"
          : "Не задан";

      if (clearing) {
        item.hint.textContent = "Секрет будет удалён после сохранения.";
      } else if (typed) {
        item.hint.textContent = "Новый секрет будет сохранён.";
      } else if (isSet) {
        item.hint.textContent =
          "Секрет сохранён. Оставьте поле пустым, чтобы не менять.";
      } else {
        item.hint.textContent = "Не задан.";
      }
    }
  }

  function fillSettingsForm(settings) {
    const s = settings || {};
    el.setOutput.value = s.output_dir || "";
    fillQualitySelect(el.setQuality, s.format || "mp3", s.quality ?? 320);
    el.setConcurrency.value = String(s.concurrency || 2);
    el.setFolder.checked = s.playlist_folder !== false;
    el.setIndex.checked = s.index_prefix !== false;
    el.setMaxTracks.value = String(s.max_tracks || 1000);
    el.setMatchSource.value = s.match_source || "auto";
    el.setCookies.value = s.cookies_browser || "";
    el.setProxy.value = s.proxy || "";
    el.setSpotifyId.value = s.spotify_client_id || "";
    el.setSpotifyMarket.value = s.spotify_market || "US";
    el.setSpotifySecret.value = "";
    el.setVkToken.value = "";
    el.setFfmpeg.value = s.ffmpeg_path || "";

    state.clearFlags.clear();
    updateSecretUi();

    const detected = s.ffmpeg_detected || state.status?.ffmpeg?.path || "";
    el.settingsFfmpegStatus.textContent = detected
      ? `Найден: ${detected}`
      : "FFmpeg не найден. Установите его или укажите полный путь к ffmpeg.exe.";
  }

  async function openSettings({ focusFfmpeg = false } = {}) {
    if (!el.settingsModal.classList.contains("hidden")) {
      if (focusFfmpeg) el.setFfmpeg.focus();
      return;
    }
    state.settingsReturnFocus = document.activeElement;
    hideSettingsError();
    el.settingsModal.classList.remove("hidden");
    document.body.style.overflow = "hidden";

    if (!state.settings) await loadStatus({ quiet: true });
    fillSettingsForm(state.settings || {});

    window.setTimeout(() => {
      if (focusFfmpeg) el.setFfmpeg.focus();
      else el.setOutput.focus();
    }, 40);
  }

  function closeSettings() {
    if (el.settingsModal.classList.contains("hidden")) return;
    el.settingsModal.classList.add("hidden");
    document.body.style.overflow = "";
    state.clearFlags.clear();
    const target = state.settingsReturnFocus;
    state.settingsReturnFocus = null;
    if (target && typeof target.focus === "function") target.focus();
  }

  function setSettingsSaving(busy) {
    el.settingsSave.disabled = busy;
    const label = el.settingsSave.querySelector(".btn-label");
    const spinner = el.settingsSave.querySelector(".spinner");
    if (label) label.textContent = busy ? "Сохраняю…" : "Сохранить";
    if (spinner) spinner.classList.toggle("hidden", !busy);
  }

  async function pickOutputFolder() {
    el.setOutputPick.disabled = true;
    try {
      const data = await api("/api/folder/pick", {
        method: "POST",
        body: { initial: el.setOutput.value.trim() },
      });
      if (data.path) {
        el.setOutput.value = data.path;
        hideSettingsError();
      }
    } catch (error) {
      showSettingsError(error);
    } finally {
      el.setOutputPick.disabled = false;
    }
  }

  async function openSettingsOutput() {
    const path = el.setOutput.value.trim();
    await openFolder(path);
  }

  async function handleSaveSettings(event) {
    event.preventDefault();
    hideSettingsError();

    const outputDir = el.setOutput.value.trim();
    if (!outputDir) {
      showSettingsError(new ApiError("Укажите папку для музыки."));
      el.setOutput.focus();
      return;
    }
    const maxTracks = Number.parseInt(el.setMaxTracks.value, 10);
    if (!Number.isFinite(maxTracks) || maxTracks < 1) {
      showSettingsError(new ApiError("Максимум треков должен быть числом от 1."));
      el.setMaxTracks.focus();
      return;
    }
    const { format, quality } = parseQuality(el.setQuality.value);
    const body = {
      output_dir: outputDir,
      format,
      quality,
      concurrency: Number.parseInt(el.setConcurrency.value, 10) || 2,
      playlist_folder: el.setFolder.checked,
      index_prefix: el.setIndex.checked,
      max_tracks: maxTracks,
      match_source: el.setMatchSource.value,
      cookies_browser: el.setCookies.value,
      proxy: el.setProxy.value.trim(),
      spotify_client_id: el.setSpotifyId.value.trim(),
      spotify_market: el.setSpotifyMarket.value.trim().toUpperCase(),
      ffmpeg_path: el.setFfmpeg.value.trim(),
    };
    for (const item of secretControls) {
      if (state.clearFlags.has(item.key)) {
        body[item.key] = "__clear__";
      } else if (item.input.value.trim()) {
        body[item.key] = item.input.value.trim();
      }
    }

    setSettingsSaving(true);
    try {
      const data = await api("/api/settings", { method: "POST", body });
      state.settings = data.settings;
      if (state.status) {
        state.status.settings = data.settings;
        state.status.ffmpeg = {
          found: Boolean(data.ffmpeg?.found),
          path: data.ffmpeg?.path || "",
          configured: data.settings?.ffmpeg_path || "",
        };
      }
      updateFfmpeg(data.ffmpeg);
      fillSettingsForm(data.settings);

      if (state.playlist) {
        const current = parseQuality(el.previewQuality.value);
        fillQualitySelect(el.previewQuality, current.format, current.quality);
        updateFolderNote();
        updateFooterNote();
      }

      closeSettings();
      for (const warning of data.warnings || []) {
        toast(warning, "warning", "Настройки");
      }
      toast("Новые параметры будут применяться к следующим загрузкам.", "success", "Настройки сохранены");
      schedulePoll(300);
    } catch (error) {
      showSettingsError(error);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function pasteFromClipboard() {
    try {
      if (!navigator.clipboard?.readText) throw new Error("unsupported");
      const text = await navigator.clipboard.readText();
      if (text?.trim()) {
        el.urlInput.value = text.trim();
        el.urlInput.focus();
      } else {
        toast("Буфер обмена пуст.", "info");
      }
    } catch {
      el.urlInput.focus();
      toast(
        "Не удалось прочитать буфер обмена. Нажмите Ctrl+V в поле ссылки.",
        "warning",
        "Вставьте вручную",
      );
    }
  }

  let filterTimer = 0;

  function handleFilterInput() {
    window.clearTimeout(filterTimer);
    filterTimer = window.setTimeout(renderTracks, 120);
  }

  function handleKeyDown(event) {
    if (event.key !== "Escape") return;
    if (!el.settingsModal.classList.contains("hidden")) {
      event.preventDefault();
      closeSettings();
    }
  }

  function bindEvents() {
    el.resolveForm.addEventListener("submit", handleResolve);
    el.pasteBtn.addEventListener("click", pasteFromClipboard);
    el.errorClose.addEventListener("click", hideError);
    el.ffmpegChip.addEventListener("click", () => openSettings({ focusFfmpeg: true }));
    el.settingsBtn.addEventListener("click", () => openSettings());

    for (const node of document.querySelectorAll("[data-close-settings]")) {
      node.addEventListener("click", closeSettings);
    }
    el.settingsForm.addEventListener("submit", handleSaveSettings);
    el.setOutputPick.addEventListener("click", pickOutputFolder);
    el.setOutputOpen.addEventListener("click", openSettingsOutput);

    for (const item of secretControls) {
      item.button?.addEventListener("click", () => {
        state.clearFlags.add(item.key);
        item.input.value = "";
        updateSecretUi();
        item.input.focus();
      });
      item.input.addEventListener("input", () => {
        if (item.input.value.trim()) state.clearFlags.delete(item.key);
        updateSecretUi();
      });
    }

    el.selectAll.addEventListener("change", handleSelectAll);
    el.trackFilter.addEventListener("input", handleFilterInput);
    el.collapseTracks.addEventListener("click", () => {
      setTracksCollapsed(!el.trackList.classList.contains("collapsed"));
    });
    el.previewQuality.addEventListener("change", () => {
      state.previewQualityTouched = true;
      updateFooterNote();
    });
    el.previewFolder.addEventListener("change", updateFolderNote);
    el.previewIndex.addEventListener("change", updateFooterNote);

    el.openOutputBtn.addEventListener("click", () => openFolder(""));
    el.downloadSelected.addEventListener("click", () => startDownload("selected"));
    el.downloadAll.addEventListener("click", () => startDownload("all"));

    el.trackList.addEventListener("change", handleTrackChange);
    el.jobsList.addEventListener("click", handleJobsClick);
    el.clearFinished.addEventListener("click", clearFinished);

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) schedulePoll(0);
    });
  }

  async function init() {
    if (state.started) return;
    state.started = true;
    bindEvents();

    const initialUrl = new URLSearchParams(window.location.search).get("url");
    if (initialUrl) el.urlInput.value = initialUrl;

    await loadStatus();
    schedulePoll(80);
  }

  init();
})();
