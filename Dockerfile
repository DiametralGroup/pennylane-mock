# Dedicated, non-root, minimal image — the same shape as the four sibling
# mocks (boondmanager-mock, entra-mock, linkedin-mock, ga-mock).
#
# Runtime dependencies are FastAPI and uvicorn, and nothing else: the cursor is
# base64 of JSON and the auth is a static bearer, so no crypto library is
# needed. Keeping that true is what keeps the image small and its attack
# surface flat.
#
# `python:3.12-slim` pulled directly rather than through the Harbor proxy:
# this image must ALSO build outside the VPN (dev laptops, GitHub Actions),
# where harbor.build.graal.systems is unreachable. In Tekton CI, kaniko then
# pushes to Harbor, where the pods pull from — the Kyverno
# `only-harbor-images` policy stays satisfied cluster-side.
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# `--frozen`      : the build never resolves, it installs the lockfile.
# `--no-dev`      : pytest and ruff have no place in the shipped image.
# `--no-editable` : WITHOUT THIS OPTION uv installs the project in editable
#                   mode, i.e. a link to /build/src — which does not exist in
#                   the final stage. The image then builds fine and fails at
#                   startup with "No module named pennylane_mock".
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Fixed uid/gid and non-root: the consumer namespace runs under the `baseline`
# PSA (because of Spilo), but everything WE ship stays restricted-clean. The
# chart sets runAsNonRoot + seccomp + drop ALL; the image must comply,
# otherwise the declaration is a lie.
RUN groupadd --gid 65532 mock && \
    useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin mock

COPY --from=builder /opt/venv /opt/venv

USER 65532:65532
EXPOSE 8000

# The original compose service had no healthcheck even though /health existed:
# the consumer waited on `service_started` and could query a mock that was not
# ready yet.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["python", "-m", "pennylane_mock"]
