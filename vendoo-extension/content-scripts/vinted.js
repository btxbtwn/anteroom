// Fills the seller's Vinted upload form and clicks Upload.
// Save draft is never used. Upload runs only after Studio has confirm: true.

function classifyVintedControl(label) {
  const text = String(label || '').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!text) return 'ignore';
  if (text === 'save draft' || text === 'save as draft') return 'save';
  if (text === 'upload' || text === 'publish' || text === 'upload item') return 'publish';
  return 'ignore';
}

function vintedListControl(label) {
  return classifyVintedControl(label) === 'publish';
}

function vintedConditionCandidates(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const table = {
    'new with tags': ['New with tags', 'New'],
    'new without tags': ['New without tags', 'Like new'],
    'like new': ['Like new', 'New without tags'],
    'new': ['New', 'New with tags'],
    'very good': ['Very good'],
    'good': ['Good'],
    'satisfactory': ['Satisfactory'],
  };
  if (table[text]) return table[text];
  const trimmed = String(value || '').trim();
  return trimmed ? [trimmed] : [];
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { classifyVintedControl, vintedListControl, vintedConditionCandidates };
}

if (typeof document !== 'undefined') {
  (function () {
    'use strict';

    const PLATFORM = 'vinted';

    function controlLabel(el) {
      return (el.innerText || el.textContent || el.getAttribute('aria-label') || el.value || '')
        .replace(/\s+/g, ' ')
        .trim();
    }

    function setNativeValue(el, value) {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(el, value);
      else el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function findField(labels) {
      const wanted = labels.map((label) => label.toLowerCase());
      for (const label of document.querySelectorAll('label')) {
        const text = controlLabel(label).toLowerCase();
        if (!wanted.some((item) => text === item || text.startsWith(item))) continue;
        const id = label.getAttribute('for');
        if (id) {
          const found = document.getElementById(id);
          if (found) return found;
        }
        const nested = label.querySelector('input, textarea');
        if (nested) return nested;
      }
      for (const selector of [
        '#title', 'input[name="title"]', '#description', 'textarea[name="description"]',
        '#price', 'input[name="price"]', 'input[name="brand"]',
      ]) {
        const el = document.querySelector(selector);
        if (!el) continue;
        const hint = `${el.getAttribute('name') || ''} ${el.getAttribute('id') || ''} ${el.getAttribute('placeholder') || ''}`.toLowerCase();
        if (wanted.some((item) => hint.includes(item))) return el;
      }
      return null;
    }

    function clickMatching(candidates) {
      const wanted = candidates.map((item) => item.toLowerCase());
      const controls = document.querySelectorAll('button, [role="option"], [role="radio"], label, li');
      for (const el of controls) {
        if (classifyVintedControl(controlLabel(el)) === 'publish') continue;
        const text = controlLabel(el).toLowerCase();
        if (wanted.some((item) => text === item)) {
          el.click();
          return true;
        }
      }
      return false;
    }

    function clickUpload() {
      for (const el of document.querySelectorAll('button, [role="button"]')) {
        if (!vintedListControl(controlLabel(el))) continue;
        el.click();
        return true;
      }
      return false;
    }

    async function chooseCategory(path) {
      const parts = String(path || '').split('>').map((part) => part.trim()).filter(Boolean);
      if (!parts.length) return false;
      const opener = findField(['category']);
      if (opener) opener.click();
      else clickMatching(['category']);
      await new Promise((resolve) => setTimeout(resolve, 400));
      const search = document.querySelector('input[type="search"], input[placeholder*="Search" i], input[placeholder*="Categor" i]');
      const leaf = parts[parts.length - 1];
      if (search) {
        setNativeValue(search, leaf);
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
      const crumb = parts.join(' > ').toLowerCase();
      const options = document.querySelectorAll('[role="option"], li, button');
      for (const el of options) {
        const text = controlLabel(el).toLowerCase();
        if (text === crumb || text.endsWith(leaf.toLowerCase())) {
          if (classifyVintedControl(text) === 'publish') continue;
          el.click();
          return true;
        }
      }
      return false;
    }

    function attachPhotos(files) {
      const input = document.querySelector('input[type="file"]');
      if (!input || !files?.length || typeof DataTransfer === 'undefined') return false;
      const transfer = new DataTransfer();
      for (const file of files) {
        if (!file?.data) continue;
        const binary = atob(file.data);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        transfer.items.add(new File([bytes], file.name || 'photo.jpg', { type: file.type || 'image/jpeg' }));
      }
      if (!transfer.files.length) return false;
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }

    async function fillAndList(data, files) {
      const specifics = data?.vinted_specifics && typeof data.vinted_specifics === 'object' ? data.vinted_specifics : {};
      const signedOut = document.querySelector('input[type="password"]')
        && !document.querySelector('#title, input[name="title"], textarea[name="description"]');
      if (signedOut || /\/(login|sign)/.test(location.pathname)) {
        return { ok: false, error: 'Sign in to Vinted in this Chrome window, then list again. Nothing was published.' };
      }
      const title = findField(['title']);
      const description = findField(['description']);
      const price = findField(['price']);
      if (!title && !description) {
        return { ok: false, error: 'Vinted\'s listing form was not on the page. Nothing was published.' };
      }
      if (title && data.title) setNativeValue(title, String(data.title).slice(0, 100));
      if (description && data.description) setNativeValue(description, String(data.description));
      if (price && data.price != null) setNativeValue(price, String(data.price));
      const brand = findField(['brand']);
      const brandValue = String(specifics.brand || data.brand || '').trim() || 'No brand';
      if (brand) {
        setNativeValue(brand, brandValue);
        await new Promise((resolve) => setTimeout(resolve, 300));
        clickMatching([brandValue, 'No brand']);
      }
      const category = specifics.category || data.marketplace_categories?.vinted || '';
      if (category) await chooseCategory(category);
      const conditions = vintedConditionCandidates(specifics.condition || data.condition);
      if (conditions.length) clickMatching(conditions);
      const size = String(specifics.size || data.size || '').trim();
      if (size) clickMatching([size]);
      const color = String(specifics.color || data.primaryColor || '').trim();
      if (color) clickMatching([color]);
      const packageSize = String(specifics.packageSize || '').trim();
      if (packageSize) clickMatching([packageSize]);
      if (files?.length) attachPhotos(files);
      await new Promise((resolve) => setTimeout(resolve, 300));
      if (!clickUpload()) {
        return { ok: false, error: 'Upload was not on the Vinted page. Nothing was published.' };
      }
      return { ok: true, url: location.href, listed: true };
    }

    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (msg.type === 'PING') {
        sendResponse({ ok: true, platform: PLATFORM });
        return true;
      }
      if (msg.type === 'START_VINTED') {
        if (msg.confirm !== true) {
          sendResponse({ ok: false, error: 'Confirm the list before it is sent to Vinted. Nothing was published.' });
          return true;
        }
        fillAndList(msg.data || {}, msg.files || []).then(sendResponse).catch((err) => {
          sendResponse({ ok: false, error: err.message || 'Vinted listing was not published' });
        });
        return true;
      }
      return false;
    });
  }());
}
