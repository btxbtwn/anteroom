// eBay Create listing page model, for checking Anteroom's eBay form against eBay's own.
//
// Paste into the console (or inject) on a scratch draft's page, ebay.com/lstng?draftId=…
// Opening ebay.com/lstng?mode=AddItem&categoryId=<id> creates that draft. Nothing here lists,
// saves for later or publishes; useCategory() changes only the scratch draft's category.
//
//   await ebaySweep.useCategory('T-Shirts', "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts")
//   location.reload()             // the item specifics model is rendered with the page
//   ebaySweep.save('15687')       // downloads ebay-model-15687.json
//
// Then: compare.py 15687=ebay-model-15687.json …
(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const text = display => display?.textSpans?.map(span => span.text).join('') || '';

  // Each item specific as the page renders it: group (REQUIRED, RECOMMENDED, ADDITIONAL), choices, limits.
  function model() {
    const source = [...document.scripts].map(script => script.textContent).find(body => body.includes('"name":"attributes.'));
    if (!source) return null;
    const objectAt = start => {
      let depth = 0, quoted = false, escaped = false;
      for (let at = start; at < source.length; at++) {
        const char = source[at];
        if (quoted) { if (escaped) escaped = false; else if (char === '\\') escaped = true; else if (char === '"') quoted = false; continue; }
        if (char === '"') { quoted = true; continue; }
        if (char === '{') depth++;
        else if (char === '}' && --depth === 0) return source.slice(start, at + 1);
      }
      return null;
    };
    const rows = [];
    for (let at = source.indexOf('{"name":"attributes.'); at >= 0; at = source.indexOf('{"name":"attributes.', at + 10)) {
      try { rows.push(JSON.parse(objectAt(at))); } catch { /* not a complete attribute object */ }
    }
    return rows.filter(row => row.groups).map(row => ({
      name: row.name.slice('attributes.'.length), label: text(row.label), group: row.groups[0],
      multi: Boolean(row.multiSelectEnabled), custom: row.customValuesAllowed, maxLength: row.maxLength,
      options: (row.options || []).map(option => option.value), note: text(row.subText),
    }));
  }

  // Picks a leaf in the scratch draft's Item category dialog by its full path.
  async function useCategory(query, path) {
    const setValue = (input, value) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    let dialog = [...document.querySelectorAll('[role=dialog]')].find(node => node.querySelector('input[aria-label="Item category"]'));
    if (!dialog) {
      [...document.querySelectorAll('button')].find(button => /^Edit$/.test(button.innerText.trim())
        && button.closest('section,div')?.innerText.includes('ITEM CATEGORY')).click();
      await sleep(1200);
      const fields = [...document.querySelectorAll('[role=dialog]')].find(node => node.innerText.startsWith('Item category'));
      fields.querySelector('button.se-field-card__body').click();
      await sleep(1500);
      dialog = [...document.querySelectorAll('[role=dialog]')].find(node => node.querySelector('input[aria-label="Item category"]'));
    }
    setValue(dialog.querySelector('input[aria-label="Item category"]'), query);
    await sleep(2500);
    const want = path.replace(/\s*>\s*/g, '>').toLowerCase();
    const norm = node => (node.innerText || '').replace(/\s*>\s*/g, '>').trim().toLowerCase();
    const leaf = [...dialog.querySelectorAll('*')].filter(node => norm(node) === want)
      .filter(node => ![...node.children].some(child => norm(child) === want)).pop();
    if (!leaf) throw new Error(`eBay did not offer ${path}`);
    (leaf.closest('button,[role=option],li,a,[role=button]') || leaf).click();
    await sleep(1500);
    [...document.querySelectorAll('[role=dialog] button')].find(button => button.innerText.trim() === 'Done'
      && button.getBoundingClientRect().width > 0)?.click();
    await sleep(3000);
  }

  function save(categoryId) {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(model())], { type: 'application/json' }));
    link.download = `ebay-model-${categoryId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  window.ebaySweep = { model, useCategory, save };
})();
