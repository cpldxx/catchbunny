#!/bin/sh
# Install Catchbunny with one line:
#
#   curl -LsSf https://raw.githubusercontent.com/cpldxx/catchbunny/main/install.sh | sh
#
# What it does, in order:
#   1. installs uv (https://docs.astral.sh/uv/), the Python installer Catchbunny uses, if it is not there yet
#   2. installs Catchbunny from GitHub as a command of its own (uv fetches the Python it needs)
#   3. downloads the browser Catchbunny tests with, once
#   4. makes sure new terminals can find the `catchbunny` command
#
# Running it again updates Catchbunny to the latest version. Nothing is installed
# into your app, and nothing runs in the background afterwards.

set -eu

REPO="git+https://github.com/cpldxx/catchbunny"

say() { printf '%s\n' "$*"; }
fail() { printf 'catchbunny install: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin | Linux) ;;
  *) fail "this installer is for macOS and Linux. On other systems, install uv and run: uv tool install $REPO" ;;
esac
command -v curl > /dev/null 2>&1 || fail "curl is needed, and was not found."

START_PATH="$PATH"

# 1. uv
if ! command -v uv > /dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    say "Installing uv, the installer Catchbunny uses..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv > /dev/null 2>&1 || fail "uv was installed but cannot be found. Open a new terminal and run this again."
  fi
fi

# 2. Catchbunny itself. --reinstall, so that running this again brings the latest version.
say "Installing Catchbunny..."
uv tool install --quiet --reinstall "$REPO"
BIN="$(uv tool dir --bin)"
PATH="$BIN:$PATH"
command -v catchbunny > /dev/null 2>&1 || fail "Catchbunny was installed into $BIN but cannot be found there."

# 3. The browser it tests with: downloaded now, so the first run starts at once.
say "Downloading the browser Catchbunny tests with (once, about 100 MB)..."
catchbunny doctor --install-browser > /dev/null 2>&1 || catchbunny doctor || true

# 4. New terminals find the command.
uv tool update-shell > /dev/null 2>&1 || true

say ""
say "Catchbunny is installed. Open the console:"
say ""
case ":$START_PATH:" in
  *":$BIN:"*)
    say "  catchbunny start"
    ;;
  *)
    say "  catchbunny start        (in a new terminal)"
    say ""
    say "Or right away, in this terminal: $BIN/catchbunny start"
    ;;
esac
