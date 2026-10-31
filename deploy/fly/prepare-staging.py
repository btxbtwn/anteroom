"""Generate isolated staging config; never create resources or deploy anything."""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def configuration(app: str, supabase_url: str) -> str:
    production = tomllib.loads((ROOT / 'fly.toml').read_text())
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', app) or app == production['app']:
        raise ValueError('Use a separate staging Fly app name')
    url = urlsplit(supabase_url)
    if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.supabase.co')
            or url.username or url.password or url.port or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('Use the HTTPS origin of a separate Supabase staging project')
    origin = f'https://{url.hostname}'
    if origin == production['env']['SUPABASE_URL']:
        raise ValueError('Staging must not use the production Supabase project')
    return f'''# Generated public configuration only. No resources have been created.
app = {json.dumps(app)}
primary_region = {json.dumps(production['primary_region'])}

[build]
  dockerfile = 'Dockerfile'
[build.args]
  VITE_SUPABASE_URL = {json.dumps(origin)}
[env]
  SUPABASE_URL = {json.dumps(origin)}
  ANTEROOM_BACKGROUND_WORKER = '1'
[http_service]
  internal_port = 8080
  force_https = true
  auto_start_machines = true
  auto_stop_machines = 'stop'
  min_machines_running = 0
[[http_service.checks]]
  interval = '30s'
  timeout = '5s'
  grace_period = '15s'
  method = 'GET'
  path = '/api/health'
[[vm]]
  memory = '2gb'
  cpu_kind = 'shared'
  cpus = 1
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--supabase-url', required=True)
    args = parser.parse_args()
    try:
        output = configuration(args.app, args.supabase_url)
    except ValueError as error:
        parser.error(str(error))
    target = ROOT / 'staging.generated.toml'
    target.write_text(output)
    print(f'Prepared {target}. No resources were created or deployed.')


if __name__ == '__main__':
    main()
