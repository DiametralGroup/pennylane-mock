"""Container entry point: `python -m pennylane_mock`.

Deliberately minimal — no CLI, no options. Everything is configured via
environment variables (cf. settings.py), because that's the only mechanism
that works identically in docker compose, a Kubernetes Deployment and a
GitHub Actions service.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "pennylane_mock:app",
        host=os.environ.get("PENNYLANE_MOCK_HOST", "0.0.0.0"),
        port=int(os.environ.get("PENNYLANE_MOCK_PORT", "8000")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
