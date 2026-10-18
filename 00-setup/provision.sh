#!/usr/bin/env bash
# Unit 3 provisioning (WSL Debian). Run via: scp this file, then bash it.
# Password is passed in via the SUDO_PW environment variable.
set -uo pipefail

PW="${SUDO_PW:-}"
SUDO="sudo -S -p ''"

echo "=== 0. identity ==="
id
stat -c '%u %g %a %n' "$HOME/.bashrc" "$HOME/.profile"

echo
echo "=== 1. fix ownership of home tree (uid 1000 -> 1001) ==="
# Only touch paths NOT already owned by the current user: safe + idempotent.
BAD=$(find "$HOME" -maxdepth 2 ! -user "$(id -un)" 2>/dev/null | head -50)
if [ -n "$BAD" ]; then
  echo "--- repointing these to $(id -un) ---"
  echo "$BAD"
  echo "$PW" | $SUDO chown -R "$(id -un):$(id -gn)" "$HOME" 2>&1
  echo "chown done"
else
  echo "nothing to fix - all owned by $(id -un)"
fi
echo "--- after ---"
stat -c '%U %a %n' "$HOME/.bashrc" "$HOME/.profile"

echo
echo "=== 2. ensure ~/.local/bin on PATH (idempotent) ==="
if [ -w "$HOME/.bashrc" ]; then
  if grep -qs 'ai-journey PATH' "$HOME/.bashrc"; then
    echo "already present in .bashrc"
  else
    printf '\n# ai-journey PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
    echo "appended to .bashrc"
  fi
  tail -3 "$HOME/.bashrc"
else
  echo "STILL NOT WRITABLE - cannot update .bashrc"
fi

echo
echo "=== 3. uv sanity ==="
export PATH="$HOME/.local/bin:$PATH"
uv --version || echo "uv MISSING"

echo
echo "=== 4. requested python 3.12 for the project ==="
uv python install 3.12 2>&1 | tail -3
uv python list 2>&1 | grep -E '3\.12' | head -3

echo
echo "=== 5. toolchain inventory (for later units) ==="
for c in git curl jq node npm docker psql redis-cli make gcc ffmpeg; do
  if command -v "$c" >/dev/null 2>&1; then printf '  %-10s %s\n' "$c" "$(command -v "$c")"; else printf '  %-10s MISSING\n' "$c"; fi
done

echo
echo "=== DONE ==="
