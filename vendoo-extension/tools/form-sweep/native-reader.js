// Read Anteroom's local form builders for sweep comparisons. No Chrome session.
const path = require('node:path');
const { execFileSync } = require('node:child_process');

function readForms(marketplace, listings) {
  if (!['poshmark', 'depop'].includes(marketplace)) throw new Error('Unsupported local form reader');
  const studio = path.resolve(__dirname, '../../../vendoo-studio');
  const python = process.env.PYTHON || path.join(studio, '.venv', 'bin', 'python');
  const script = [
    'import json, sys',
    'from vendoo_studio.services import depop_form, poshmark_form',
    'build = {"depop": depop_form.form, "poshmark": poshmark_form.form}[sys.argv[1]]',
    'print(json.dumps([build(listing) for listing in json.load(sys.stdin)]))',
  ].join('\n');
  // Each form can include a complete brand catalog. Bound the subprocess
  // output per batch instead of overflowing its buffer on a full sweep.
  const forms = [];
  for (let offset = 0; offset < listings.length; offset += 10) {
    forms.push(...JSON.parse(execFileSync(python, ['-c', script, marketplace], {
      cwd: studio,
      env: { ...process.env, PYTHONPATH: [path.join(studio, 'server'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
      input: JSON.stringify(listings.slice(offset, offset + 10)), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
    })));
  }
  return forms;
}

module.exports = { readForms };
