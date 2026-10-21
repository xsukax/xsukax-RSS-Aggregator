# Validation record — xsukax RSS Aggregator 0.1.1

The following checks were performed against the corrected source tree.

| Check | Result | Notes |
| --- | --- | --- |
| Python integration tests | Passed (2 test functions, multiple assertions) | Includes feed parsing, authentication, custom admin setup, account protection, article processing, search, multipart OPML import, and password change |
| Live Uvicorn HTTP smoke | Passed | Actual local HTTP server served `/healthz`, `/api/auth/state`, first-run `/api/auth/setup`, and multipart `/api/import/opml` |
| Browser first-run flow | Passed | Chromium exercised actual HTML/JavaScript, backed by FastAPI endpoints via in-process request routing; confirmed password mismatch, administrator creation, logout, login, and zero JS exceptions |
| JavaScript syntax | Passed | `node --check app/static/app.js` |
| Installer shell syntax | Passed | `bash -n install.sh` |
| Compilation | Passed | `python3 -m py_compile app/*.py` |
| Docker image build & full Compose deployment | **Not run locally** | Docker CLI was unavailable in the validation container; GitHub Actions CI has a Docker build and runtime startup smoke test to catch future image startup failures |

The preview images in `docs/` are browser captures of the application UI. `preview-setup.png` shows the first-run setup form with empty credential fields. The desktop and mobile reading screenshots use nonpersonal demonstration articles.

**Reproducing the integration tests on Debian:**

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q
```

**Rebuilding on an existing VPS** (keep `.env` and `data/`):

```bash
sudo docker compose up -d --build
sudo docker compose ps -a
curl -fsS http://127.0.0.1:8090/healthz
```

The `.github/workflows/test.yml` workflow runs Python checks and a separate Docker-image smoke test on GitHub-hosted runners. A passing local Python test suite alone is **not** evidence of a successful Docker build.
