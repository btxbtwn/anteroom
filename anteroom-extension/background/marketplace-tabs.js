// Opening marketplace tabs in the seller's Chrome.

const WINDOW_WIDTH = 1280;
const WINDOW_HEIGHT = 900;
const SHOW_LEFT = 80;
const SHOW_TOP = 80;
const FALLBACK_WIDTH = 800;
const FALLBACK_HEIGHT = 600;

async function openMarketplaceTab(url, { active = true } = {}) {
  const windows = await chrome.windows.getAll({ windowTypes: ['normal'] });
  const window = windows.find(candidate => candidate.focused) || windows.at(-1);
  if (window?.id != null) return await chrome.tabs.create({ windowId: window.id, url, active });
  // A connected MV3 worker can outlive every Chrome window. Open a blank
  // window first so window creation never repeats a marketplace navigation.
  const created = await createWindowSafe({ url: 'about:blank', type: 'normal', focused: active });
  const tab = created.tabs?.[0];
  if (tab?.id == null) throw new Error('Chrome did not return a marketplace tab. Open Chrome and try again.');
  return url === 'about:blank' ? tab : await chrome.tabs.update(tab.id, { url, active });
}

function isWindowBoundsError(err) {
  const message = String((err && err.message) || err || '');
  return /bounds/i.test(message) && /visible screen/i.test(message);
}

function workAreasFromDisplays(displays) {
  return (displays || [])
    .filter((display) => (
      display
      && display.isEnabled !== false
      && display.workArea
      && Number.isFinite(display.workArea.left)
      && Number.isFinite(display.workArea.top)
      && Number.isFinite(display.workArea.width)
      && display.workArea.width > 0
      && Number.isFinite(display.workArea.height)
      && display.workArea.height > 0
    ))
    .map((display) => display.workArea);
}

function overlapArea(win, area) {
  const left = Math.max(win.left, area.left);
  const top = Math.max(win.top, area.top);
  const right = Math.min(win.left + win.width, area.left + area.width);
  const bottom = Math.min(win.top + win.height, area.top + area.height);
  if (right <= left || bottom <= top) {
    return 0;
  }
  return (right - left) * (bottom - top);
}

function isMostlyOnscreen(win, areas) {
  const total = Number(win && win.width) * Number(win && win.height);
  if (!total || !(areas || []).length) {
    return false;
  }
  let overlap = 0;
  for (const area of areas) {
    overlap += overlapArea(win, area);
  }
  return overlap >= total * 0.5;
}

function boundsInWorkArea(area, width, height) {
  const areaWidth = Number(area && area.width) || FALLBACK_WIDTH;
  const areaHeight = Number(area && area.height) || FALLBACK_HEIGHT;
  const w = Math.max(1, Math.min(width || FALLBACK_WIDTH, areaWidth));
  const h = Math.max(1, Math.min(height || FALLBACK_HEIGHT, areaHeight));
  return {
    left: (Number(area && area.left) || 0) + Math.max(0, Math.floor((areaWidth - w) / 2)),
    top: (Number(area && area.top) || 0) + Math.max(0, Math.floor((areaHeight - h) / 2)),
    width: w,
    height: h,
  };
}

// Chrome refuses windows that are mostly off-screen, so size a new window
// from a visible one and centre it on a display.
function pickOnscreenBounds(windows, displays) {
  const areas = workAreasFromDisplays(displays);
  const primary = (displays || []).find((display) => display && display.isPrimary && display.workArea);
  const fallbackArea = (primary && primary.workArea) || areas[0];
  const visible = (windows || []).find((win) => (
    win
    && win.state !== 'minimized'
    && Number.isFinite(win.left)
    && Number.isFinite(win.top)
    && Number.isFinite(win.width)
    && win.width >= 400
    && Number.isFinite(win.height)
    && win.height >= 300
    && (areas.length ? isMostlyOnscreen(win, areas) : win.left >= 0)
  ));
  if (fallbackArea) {
    return boundsInWorkArea(
      fallbackArea,
      visible ? Math.min(WINDOW_WIDTH, visible.width) : WINDOW_WIDTH,
      visible ? Math.min(WINDOW_HEIGHT, visible.height) : WINDOW_HEIGHT,
    );
  }
  if (!visible) {
    return {
      left: SHOW_LEFT,
      top: SHOW_TOP,
      width: FALLBACK_WIDTH,
      height: FALLBACK_HEIGHT,
    };
  }
  return {
    left: visible.left,
    top: visible.top,
    width: Math.min(WINDOW_WIDTH, visible.width),
    height: Math.min(WINDOW_HEIGHT, visible.height),
  };
}

async function displayInfo() {
  try {
    if (chrome.system && chrome.system.display && chrome.system.display.getInfo) {
      return await chrome.system.display.getInfo();
    }
  } catch (_) {}
  return [];
}

async function safeWindowBounds() {
  const displays = await displayInfo();
  try {
    return pickOnscreenBounds(await chrome.windows.getAll(), displays);
  } catch (_) {
    return pickOnscreenBounds([], displays);
  }
}

async function createWindowSafe(createOptions = {}) {
  const bounds = await safeWindowBounds();
  const options = { type: 'normal', url: 'about:blank', ...createOptions };
  const attempts = [
    { ...bounds, ...options },
    { ...options, left: 0, top: 0, width: FALLBACK_WIDTH, height: FALLBACK_HEIGHT },
    options,
  ];
  let lastError = null;
  for (const attempt of attempts) {
    try {
      return await chrome.windows.create(attempt);
    } catch (err) {
      lastError = err;
      if (!isWindowBoundsError(err)) {
        throw err;
      }
      log(`Chrome rejected window create (${err.message})`);
    }
  }
  throw lastError;
}
