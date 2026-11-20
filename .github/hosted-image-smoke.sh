#!/bin/bash
# Disposable GitHub-hosted runner only. No credentials, host mounts, published
# ports, registry pushes or deployment. The internal network has no egress.
set -euo pipefail
mkdir -p image-validation
git rev-parse HEAD | tee image-validation/source-sha.txt
image=anteroom-image-smoke:ci
network=anteroom-image-smoke
cleanup() {
  docker logs anteroom-image-smoke-pg >image-validation/postgres.log 2>&1 || true
  docker logs anteroom-image-smoke-init >image-validation/init.log 2>&1 || true
  docker logs anteroom-image-smoke-app >image-validation/server.log 2>&1 || true
  docker rm -f anteroom-image-smoke-app anteroom-image-smoke-init anteroom-image-smoke-pg >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# Only tracked files enter the context; no .git credentials or local data.
git archive HEAD | docker build --platform linux/amd64 --progress plain \
  --build-arg VITE_SUPABASE_URL=https://image-smoke.invalid \
  --build-arg VITE_SUPABASE_PUBLISHABLE_KEY=synthetic-public-key \
  -t "$image" -f deploy/fly/Dockerfile -
docker image inspect "$image" --format '{{.Id}} {{.Os}}/{{.Architecture}} user={{.Config.User}}' | tee image-validation/image.txt
test "$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
docker run --rm -i --network none --entrypoint python "$image" - <<'PY'
import io, os, sys
from pathlib import Path
import cryptography.hazmat.primitives.ciphers.aead as aead
import orjson, pillow_heif, psycopg, tree_sitter
from PIL import Image
assert sys.version_info[:2] == (3, 12)
assert os.geteuid() == 10001
assert psycopg.pq.version() > 0
assert orjson.loads(orjson.dumps({'smoke': True})) == {'smoke': True}
key = aead.AESGCM.generate_key(bit_length=128)
cipher = aead.AESGCM(key)
assert cipher.decrypt(b'0' * 12, cipher.encrypt(b'0' * 12, b'smoke', None), None) == b'smoke'
pillow_heif.register_heif_opener()
photo = io.BytesIO()
Image.new('RGB', (4, 4), 'white').save(photo, format='PNG')
assert Image.open(io.BytesIO(photo.getvalue())).size == (4, 4)
assert tree_sitter.Parser
for file in ['/app/web/cloud.html', '/app/web/favicon.png', '/app/skills/list-this/SKILL.md']:
    assert Path(file).is_file(), file
assert list(Path('/app/web/assets').glob('*.js'))
print('PASS Python3.12, nonroot10001, native dependencies, photo codec and packaged assets')
PY
docker network create --internal "$network" >/dev/null
# Synthetic password and empty database, confined to this disposable network.
# Pinned by digest matching .github/workflows/ci.yml.
docker run -d --name anteroom-image-smoke-pg --network "$network" \
  -e POSTGRES_PASSWORD=synthetic-smoke-password -e POSTGRES_DB=smoke \
  postgres:16@sha256:f9a7b9ebf6f56201f949db745e47bc423c0f0f8294bafe991cc10bf74b029657 >/dev/null
# The official entrypoint's temporary initialization server is socket-only.
# Wait for the final TCP listener instead of accepting that temporary server.
for attempt in $(seq 1 60); do
  if docker exec anteroom-image-smoke-pg pg_isready -h 127.0.0.1 -p 5432 -U postgres -d smoke; then break; fi
  sleep 1
done
if ! docker exec anteroom-image-smoke-pg pg_isready -h 127.0.0.1 -p 5432 -U postgres -d smoke; then
  echo 'Postgres failed the final TCP readiness check after polling; see postgres.log.' >&2
  exit 1
fi
database=postgresql://postgres:synthetic-smoke-password@anteroom-image-smoke-pg:5432/smoke
docker run --name anteroom-image-smoke-init --network "$network" \
  -e ANTEROOM_DATABASE_URL="$database" "$image" \
  python -c 'from anteroom.database import init_db; init_db(); print("PASS disposable database migrations")'
docker run -d --name anteroom-image-smoke-app --network "$network" \
  -e ANTEROOM_DATABASE_URL="$database" \
  -e SUPABASE_URL=https://image-smoke.invalid \
  -e SUPABASE_PUBLISHABLE_KEY=synthetic-public-key "$image" >/dev/null
# Exercise the image's unchanged CMD and real HTTP server from inside its
# network namespace; no listener is published onto the runner.
docker exec -i anteroom-image-smoke-app python - <<'PY'
import time
from pathlib import Path
import httpx
with httpx.Client(base_url='http://127.0.0.1:8080', timeout=3, trust_env=False) as client:
    for attempt in range(60):
        try:
            response = client.get('/api/ready')
            if response.status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(1)
    else:
        raise AssertionError('Image did not become ready within60seconds')
    assert response.json()['status'] == 'ok'
    health = client.get('/api/health').raise_for_status().json()
    assert health['service'] == 'anteroom-hosted'
    assert health['version'] == Path('/app/anteroom/VERSION').read_text().strip()
    assert client.get('/api/account').status_code == 401
    assert client.get('/api/auth-config').json()['supabase_url'] == 'https://image-smoke.invalid'
    assert 'text/html' in client.get('/').raise_for_status().headers['content-type']
    asset = next(Path('/app/web/assets').glob('*.js'))
    assert client.get('/assets/' + asset.name).raise_for_status().content
    assert client.get('/favicon.png').raise_for_status().content
print('PASS actual image CMD startup, readiness/schema, health/version, auth refusal and HTTP assets')
PY
