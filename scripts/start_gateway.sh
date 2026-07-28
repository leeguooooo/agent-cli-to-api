#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROVIDER="codex"
HOST="127.0.0.1"
PORT="8000"
ENV_FILE="$ROOT_DIR/.env"

usage() {
	cat <<'EOF'
Usage: scripts/start_gateway.sh [provider] [options]

Options:
  --host <host>          Bind host (default: 127.0.0.1)
  --port <port>          Bind port (default: 8000)
  --env-file <path>      Env file for agent-cli-to-api (default: .env)
  -h, --help             Show help

Env controls:
  CODEX_AUTO_UPDATE_CLI=0            Disable startup Codex CLI auto-update
  CODEX_AUTO_UPDATE_INTERVAL_SECONDS Override update check interval (default: 86400)
  CODEX_AUTO_UPDATE_REQUIRED=1       Fail startup if the update check fails
EOF
}

if [[ $# -gt 0 && "$1" != --* ]]; then
	PROVIDER="$1"
	shift
fi

while [[ $# -gt 0 ]]; do
	case "$1" in
	--host)
		HOST="$2"
		shift 2
		;;
	--port)
		PORT="$2"
		shift 2
		;;
	--env-file)
		ENV_FILE="$2"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "Unknown option: $1" >&2
		usage
		exit 1
		;;
	esac
done

env_file_value() {
	local key="$1"
	[[ -f "$ENV_FILE" ]] || return 0
	awk -F= -v key="$key" '
		$1 == key {
			value = substr($0, index($0, "=") + 1)
			gsub(/^[ \t]+|[ \t]+$/, "", value)
			gsub(/^["'\'']|["'\'']$/, "", value)
			print value
			exit
		}
	' "$ENV_FILE"
}

is_truthy() {
	case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
	1 | true | t | yes | y | on) return 0 ;;
	*) return 1 ;;
	esac
}

is_falsey() {
	case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
	0 | false | f | no | n | off) return 0 ;;
	*) return 1 ;;
	esac
}

resolve_bin() {
	local name="$1"
	shift
	local found
	found="$(command -v "$name" 2>/dev/null || true)"
	if [[ -n "$found" ]]; then
		printf '%s\n' "$found"
		return 0
	fi
	for candidate in "$@"; do
		if [[ -x "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	return 1
}

maybe_update_codex_cli() {
	local enabled interval required cache_dir stamp now last elapsed
	enabled="${CODEX_AUTO_UPDATE_CLI:-$(env_file_value CODEX_AUTO_UPDATE_CLI)}"
	if is_falsey "${enabled:-1}"; then
		return 0
	fi

	interval="${CODEX_AUTO_UPDATE_INTERVAL_SECONDS:-$(env_file_value CODEX_AUTO_UPDATE_INTERVAL_SECONDS)}"
	interval="${interval:-86400}"
	if ! [[ "$interval" =~ ^[0-9]+$ ]]; then
		interval=86400
	fi

	cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/agent-cli-to-api"
	stamp="$cache_dir/codex-cli-auto-update.stamp"
	now="$(date +%s)"
	last="0"
	if [[ -f "$stamp" ]]; then
		last="$(stat -f %m "$stamp" 2>/dev/null || stat -c %Y "$stamp" 2>/dev/null || printf '0')"
	fi
	elapsed=$((now - last))
	if (( elapsed < interval )); then
		return 0
	fi

	mkdir -p "$cache_dir"
	required="${CODEX_AUTO_UPDATE_REQUIRED:-$(env_file_value CODEX_AUTO_UPDATE_REQUIRED)}"
	echo "Checking Codex CLI update before gateway startup..."
	if curl -fsSL https://chatgpt.com/codex/install.sh | sh; then
		touch "$stamp"
		return 0
	fi

	echo "Codex CLI auto-update failed." >&2
	if is_truthy "$required"; then
		return 1
	fi
	touch "$stamp"
	return 0
}

maybe_update_codex_cli

UV_BIN="${UV_BIN:-$(resolve_bin uv "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv)}"
exec "$UV_BIN" run agent-cli-to-api "$PROVIDER" --host "$HOST" --port "$PORT" --env-file "$ENV_FILE"
