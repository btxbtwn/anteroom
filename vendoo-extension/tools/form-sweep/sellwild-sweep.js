// Sellwild sell-page read. Run on https://sellwild.com/sell (signed in or not).
// Read-only. Sellwild builds its sell form from tables in its own page code: category groups,
// categories, vehicle categories, conditions and package templates. This reads those tables
// from the page's script and downloads them as sellwild-page.json. Then:
//   node vendoo-extension/tools/form-sweep/sellwild-compare.js <folder with the download>
(async () => {
  const sources = [...new Set([...document.scripts].map(script => script.src)
    .concat(performance.getEntriesByType('resource').filter(entry => entry.initiatorType === 'script').map(entry => entry.name)))]
    .filter(src => src.startsWith(location.origin));
  let code = '';
  for (const src of sources) {
    const body = await (await fetch(src)).text();
    if (body.includes('heading:[') && body.includes('token:"USPS_')) { code = body; break; }
  }
  if (!code) throw new Error('Sellwild\'s form tables were not found in its page code.');
  const all = (pattern, map) => [...code.matchAll(pattern)].map(map);
  const unescape = value => JSON.parse(`"${value}"`);
  const page = {
    groups: all(/(\d+):\{label:"((?:[^"\\]|\\.)*)",sortOrder:(\d+),slug:"[^"]*"\}/g,
      m => ({ id: Number(m[1]), label: unescape(m[2]), sortOrder: Number(m[3]) })),
    categories: all(/(\d+):\{label:"((?:[^"\\]|\\.)*)",group:(\d+),sortOrder:(\d+),slug:/g,
      m => ({ id: Number(m[1]), label: unescape(m[2]), group: Number(m[3]), sortOrder: Number(m[4]) })),
    conditions: all(/\{label:"((?:[^"\\]|\\.)*)",id:(\d+),description:/g, m => ({ id: Number(m[2]), label: unescape(m[1]) })),
    vehicles: (code.match(/description:""\}\],\w+=\[([\d,]+)\]/) || [, ''])[1].split(',').filter(Boolean).map(Number),
    // A package template is one object literal; its keys come in different orders.
    packages: all(/\{token:"[^"]+"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g, m => {
      const body = m[0];
      const value = key => (body.match(new RegExp(`[,{]${key}:("(?:[^"\\\\]|\\\\.)*"|[\\d.]+|!0|!1)`)) || [])[1];
      const read = raw => raw === undefined ? undefined : raw === '!0' ? true : raw === '!1' ? false : raw.startsWith('"') ? JSON.parse(raw) : Number(raw);
      return { id: read(value('token')), label: read(value('name')), group: read(value('group')), length: read(value('length')),
        width: read(value('width')), height: read(value('height')), offered: read(value('offered')) === true,
        flat_rate: read(value('flatRate')) === true };
    }),
  };
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(page)], { type: 'application/json' }));
  link.download = 'sellwild-page.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  return Object.fromEntries(Object.entries(page).map(([key, rows]) => [key, rows.length]));
})();
