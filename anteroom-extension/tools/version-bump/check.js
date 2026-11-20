#!/usr/bin/env node
// Fails when the Bridge's shipped code changed since <base> but manifest.json's
// version did not go up. The hosted site compares Chrome's Bridge version with
// its own to tell the seller to update, so the same version must mean the same code.
//
//   node anteroom-extension/tools/version-bump/check.js <base-ref>
const { execFileSync } = require('node:child_process');

// Files Chrome loads. Tests, tools, dotfiles and docs do not change what runs.
function shipped(path) {
  return path.startsWith('anteroom-extension/')
    && !/^anteroom-extension\/(tests|tools)\//.test(path)
    && !path.startsWith('anteroom-extension/.')
    && !path.endsWith('.md');
}

function newer(version, than) {
  const a = String(version).split('.').map(Number), b = String(than).split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const difference = (a[i] ?? 0) - (b[i] ?? 0);
    if (difference) return difference > 0;
  }
  return false;
}

/** The problem with this change, or null. */
function problem(changed, baseVersion, headVersion) {
  const code = changed.filter(shipped);
  if (!code.length || newer(headVersion, baseVersion)) return null;
  return `Anteroom Bridge code changed (${code.join(', ')}) but anteroom-extension/manifest.json is still ${headVersion}. `
    + `Raise its version above ${baseVersion} so the site can tell sellers running the old copy to update.`;
}

function changedPaths(git, base) {
  const lines = git('-c', 'diff.renameLimit=0', 'diff', '--name-status', '-M', base, 'HEAD').split('\n').filter(Boolean);
  const paths = [];
  for (const line of lines) {
    const [status, first, second] = line.split('\t');
    if (status === 'R100') continue;
    const target = status.startsWith('R') ? second : first;
    if (target && target.startsWith('anteroom-extension/')) paths.push(target);
  }
  return paths;
}

function manifestVersion(git, ref) {
  for (const path of ['anteroom-extension/manifest.json', 'vendoo-extension/manifest.json']) {
    try {
      return JSON.parse(git('show', `${ref}:${path}`)).version;
    } catch {
      // Base ref may predate the vendoo-extension -> anteroom-extension rename.
    }
  }
  throw new Error(`No extension manifest found at ${ref}`);
}

function main(base) {
  const git = (...args) => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const changed = changedPaths(git, base);
  const found = problem(changed, manifestVersion(git, base), manifestVersion(git, 'HEAD'));
  if (found) {
    console.error(found);
    process.exit(1);
  }
  console.log('Anteroom Bridge version matches its code.');
}

if (require.main === module) {
  if (!process.argv[2]) {
    console.error('Usage: check.js <base-ref>');
    process.exit(2);
  }
  main(process.argv[2]);
}

module.exports = { problem };
