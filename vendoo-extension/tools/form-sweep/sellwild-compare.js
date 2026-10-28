#!/usr/bin/env node
// Compares Anteroom's Sellwild catalog with the tables sellwild-sweep.js read from Sellwild's
// sell page. Anteroom's Sellwild form is built from that catalog
// (skills/list-this/references/sellwild-native-catalog.json) and the vehicle list in
// vendoo-studio/server/vendoo_studio/services/sellwild_listing.py.
// Usage: node vendoo-extension/tools/form-sweep/sellwild-compare.js <folder with sellwild-page.json>
// Exits 1 when anything differs.
const fs = require('node:fs');
const path = require('node:path');

const folder = process.argv[2];
if (!folder) { console.error('Usage: sellwild-compare.js <folder>'); process.exit(2); }
const root = path.resolve(__dirname, '../../..');
const page = JSON.parse(fs.readFileSync(path.join(folder, 'sellwild-page.json'), 'utf8'));
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'skills/list-this/references/sellwild-native-catalog.json'), 'utf8'));
const listingCode = fs.readFileSync(path.join(root, 'vendoo-studio/server/vendoo_studio/services/sellwild_listing.py'), 'utf8');
const vehicles = (listingCode.match(/VEHICLE_CATEGORIES = frozenset\(\{([\d,\s]+)\}\)/) || [, ''])[1].split(',').map(Number).filter(Boolean);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const problems = [];
const difference = (problem, anteroom, sellwild) => {
  if (same(anteroom, sellwild)) return;
  const key = row => JSON.stringify(row);
  problems.push({ problem, onlyAnteroom: anteroom.filter(row => !sellwild.some(other => key(other) === key(row))),
    onlySellwild: sellwild.filter(row => !anteroom.some(other => key(other) === key(row))) });
};

// The picker: groups by sort order ("All" is not a group), each group's categories by sort order.
// Pets > Adoption (206) is in the table but hidden on the page (read 2026-10-27).
const HIDDEN = new Set([206]);
const groups = page.groups.filter(group => group.id !== 0).sort((a, b) => a.sortOrder - b.sortOrder);
const picker = groups.flatMap(group => page.categories
  .filter(row => row.group === group.id && !HIDDEN.has(row.id))
  .sort((a, b) => a.sortOrder - b.sortOrder)
  .map(row => ({ id: row.id, label: `${group.label} > ${row.label}` })));
difference('categories', catalog.categories, picker);
difference('conditions', catalog.conditions, page.conditions);
difference('vehicle categories', [...vehicles].sort((a, b) => a - b), [...page.vehicles].sort((a, b) => a - b));

// Packages offered, boxes first, then envelopes and other packaging, each in the page's order.
const order = ['box', 'envelope', 'other'];
const packages = page.packages.filter(row => row.offered)
  .sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group))
  .map(row => ({ id: row.id, label: row.label, length: row.length, width: row.width, height: row.height,
    flat_rate: row.flat_rate, featured: row.group === 'box' }));
difference('packages', catalog.parcel_templates, packages);

console.log(JSON.stringify({ categories: picker.length, packages: packages.length, mismatches: problems.length, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
