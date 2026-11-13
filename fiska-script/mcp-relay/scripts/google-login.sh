#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/google-login.sh /absolute/path/to/oauth-desktop-client.json [gcloud login options]" >&2
  exit 2
fi
relay_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
client_file="$(realpath -- "$1")"
shift
command -v gcloud >/dev/null || { echo "Install gcloud on this host first." >&2; exit 1; }
test -f "$client_file" || { echo "OAuth client file not found." >&2; exit 1; }
mkdir -p "$relay_dir/.secrets/gcloud"
# Isolate the login so it cannot replace the operator's usual ADC credentials.
CLOUDSDK_CONFIG="$relay_dir/.secrets/gcloud" gcloud auth application-default login \
  --client-id-file="$client_file" \
  --scopes=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/drive.readonly \
  "$@"
install -m 600 "$relay_dir/.secrets/gcloud/application_default_credentials.json" "$relay_dir/.secrets/google-adc.json"
echo "Saved .secrets/google-adc.json. Enable google in config.json, then restart the relay."
