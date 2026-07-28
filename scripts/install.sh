#!/bin/sh
set -eu

REPO_SLUG="${AGENT_CLI_TO_API_REPO:-leeguooooo/agent-cli-to-api}"
REPO_URL="${AGENT_CLI_TO_API_REPO_URL:-https://github.com/${REPO_SLUG}.git}"
INSTALL_DIR="${AGENT_CLI_TO_API_INSTALL_DIR:-$HOME/.agent-cli-to-api}"
REF="${AGENT_CLI_TO_API_REF:-}"
PROVIDER="${AGENT_CLI_TO_API_PROVIDER:-codex}"
HOST="${AGENT_CLI_TO_API_HOST:-127.0.0.1}"
PORT="${AGENT_CLI_TO_API_PORT:-8000}"
ENV_FILE="$INSTALL_DIR/.env"

command_exists() {
	command -v "$1" >/dev/null 2>&1
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

latest_release_ref() {
	curl -fsSL "https://api.github.com/repos/${REPO_SLUG}/releases/latest" 2>/dev/null \
		| sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
		| sed -n '1p'
}

ensure_tooling() {
	if ! command_exists curl; then
		echo "curl is required." >&2
		exit 1
	fi
	if ! command_exists git; then
		echo "git is required." >&2
		exit 1
	fi

	if ! command_exists uv; then
		echo "Installing uv..."
		curl -LsSf https://astral.sh/uv/install.sh | sh
		PATH="$HOME/.local/bin:$PATH"
		export PATH
	fi

	if [ "$PROVIDER" = "codex" ] && ! is_falsey "${INSTALL_CODEX_CLI:-1}"; then
		echo "Installing/updating Codex CLI..."
		curl -fsSL https://chatgpt.com/codex/install.sh | sh
		PATH="$HOME/.local/bin:$PATH"
		export PATH
	fi
}

checkout_repo() {
	if [ -z "$REF" ]; then
		REF="$(latest_release_ref || true)"
	fi
	if [ -z "$REF" ]; then
		REF="main"
	fi

	if [ -d "$INSTALL_DIR/.git" ]; then
		cd "$INSTALL_DIR"
		git fetch --tags origin
		git checkout "$REF"
		if git rev-parse --verify "origin/$REF" >/dev/null 2>&1; then
			git pull --ff-only origin "$REF"
		fi
	else
		if [ -e "$INSTALL_DIR" ] && [ "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | sed -n '1p')" ]; then
			echo "Install dir exists and is not a git checkout: $INSTALL_DIR" >&2
			exit 1
		fi
		mkdir -p "$(dirname "$INSTALL_DIR")"
		git clone "$REPO_URL" "$INSTALL_DIR"
		cd "$INSTALL_DIR"
		git fetch --tags origin
		git checkout "$REF"
	fi
}

ensure_env_line() {
	key="$1"
	value="$2"
	if [ ! -f "$ENV_FILE" ] || ! grep -q "^${key}=" "$ENV_FILE"; then
		printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
	fi
}

write_env() {
	if [ ! -f "$ENV_FILE" ]; then
		{
			printf 'CODEX_PROVIDER=%s\n' "$PROVIDER"
			printf 'CODEX_MODEL=gpt-5.6-sol\n'
			printf 'CODEX_ADVERTISED_MODELS=gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5\n'
			printf 'CODEX_ALLOW_CLIENT_MODEL_OVERRIDE=1\n'
			printf 'CODEX_AUTO_UPDATE_CLI=1\n'
			if [ -n "${CODEX_GATEWAY_TOKEN:-}" ]; then
				printf 'CODEX_GATEWAY_TOKEN=%s\n' "$CODEX_GATEWAY_TOKEN"
			fi
		} >"$ENV_FILE"
	else
		ensure_env_line CODEX_PROVIDER "$PROVIDER"
		ensure_env_line CODEX_MODEL gpt-5.6-sol
		ensure_env_line CODEX_ADVERTISED_MODELS gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.5
		ensure_env_line CODEX_ALLOW_CLIENT_MODEL_OVERRIDE 1
		ensure_env_line CODEX_AUTO_UPDATE_CLI 1
		if [ -n "${CODEX_GATEWAY_TOKEN:-}" ]; then
			ensure_env_line CODEX_GATEWAY_TOKEN "$CODEX_GATEWAY_TOKEN"
		fi
	fi
}

install_gateway() {
	cd "$INSTALL_DIR"
	uv sync
	write_env

	if is_truthy "${INSTALL_LAUNCHD:-0}"; then
		if [ "$(uname -s)" != "Darwin" ]; then
			echo "INSTALL_LAUNCHD=1 is only supported on macOS." >&2
			exit 1
		fi
		./scripts/install_launchd.sh --provider "$PROVIDER" --host "$HOST" --port "$PORT" --env-file "$ENV_FILE"
	fi
}

print_next_steps() {
	cat <<EOF

agent-cli-to-api installed.

Install dir: $INSTALL_DIR
Version ref: $REF
Env file:    $ENV_FILE

Run:
  cd "$INSTALL_DIR"
  uv run agent-cli-to-api "$PROVIDER" --host "$HOST" --port "$PORT" --env-file "$ENV_FILE"

Install or update the macOS launchd service:
  curl -fsSL https://github.com/${REPO_SLUG}/releases/latest/download/install.sh | INSTALL_LAUNCHD=1 sh

EOF
}

ensure_tooling
checkout_repo
install_gateway
print_next_steps
