#!/usr/bin/env bash
# Writes a throwaway service-account key file (never registered anywhere) to the path
# given as $1. `dbt compile --target bigquery --no-introspect --no-populate-cache` needs a
# parseable key to build a client object, but never makes a network call.
set -euo pipefail
out="${1:?usage: fake_bq_keyfile.sh <output.json>}"
key="$(openssl genrsa 2048 2>/dev/null)"
KEY_PEM="$key" python3 - "$out" <<'PY'
import json
import os
import sys

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(
        {
            "type": "service_account",
            "project_id": "offline-compile-check",
            "private_key_id": "0" * 40,
            "private_key": os.environ["KEY_PEM"],
            "client_email": "offline@offline-compile-check.iam.gserviceaccount.com",
            "client_id": "0",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        fh,
    )
PY
chmod 600 "$out"
