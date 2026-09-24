#!/usr/bin/env bash
# Put the database info the website shows into DEST_DIR.
#   publish_info.sh blastdb/info.json DEST_DIR
# In private mode (LAB_KEY set) only an encrypted copy is published, plus a
# stub telling the website to ask for the lab passphrase.
set -euo pipefail
src="$1"; dest="$2"
if [ -n "${LAB_KEY:-}" ]; then
  /usr/bin/python3 "$(dirname "$0")/labcrypt.py" encrypt "$src" "$dest/info.json.enc"
  echo '{"encrypted": true}' > "$dest/info.json"
else
  cp "$src" "$dest/info.json"
  rm -f "$dest/info.json.enc"
fi
