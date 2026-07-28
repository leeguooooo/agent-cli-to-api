#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.codex-api.gateway"
PROVIDER="codex"
HOST="127.0.0.1"
PORT="8000"
ENV_FILE=""
TOKEN=""
UNINSTALL=0

usage() {
	cat <<'EOF'
Usage: scripts/install_launchd.sh [options]

Options:
  --label <label>        Launchd label (default: com.codex-api.gateway)
  --provider <name>      Provider: codex|gemini|claude|cursor-agent (default: codex)
  --host <host>          Bind host (default: 127.0.0.1)
  --port <port>          Bind port (default: 8000)
  --env-file <path>      Optional env file for agent-cli-to-api
  --token <token>        Optional CODEX_GATEWAY_TOKEN env
  --uninstall            Unload and remove plist
  -h, --help             Show help

Examples:
  scripts/install_launchd.sh --provider codex --host 127.0.0.1 --port 8000
  scripts/install_launchd.sh --env-file "$PWD/.env" --token devtoken
  scripts/install_launchd.sh --uninstall
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--label)
		LABEL="$2"
		shift 2
		;;
	--provider)
		PROVIDER="$2"
		shift 2
		;;
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
	--token)
		TOKEN="$2"
		shift 2
		;;
	--uninstall)
		UNINSTALL=1
		shift
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

PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"
mkdir -p "$LOG_DIR"

if [[ "$UNINSTALL" -eq 1 ]]; then
	launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
	rm -f "$PLIST_PATH"
	echo "Removed $PLIST_PATH"
	exit 0
fi

START_SCRIPT="$ROOT_DIR/scripts/start_gateway.sh"
if [[ ! -x "$START_SCRIPT" ]]; then
	echo "Missing executable start script: $START_SCRIPT" >&2
	exit 1
fi

cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>${LABEL}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>WorkingDirectory</key><string>${ROOT_DIR}</string>
    <key>ProgramArguments</key>
    <array>
      <string>${START_SCRIPT}</string>
      <string>${PROVIDER}</string>
      <string>--host</string>
      <string>${HOST}</string>
      <string>--port</string>
      <string>${PORT}</string>
EOF

if [[ -n "$ENV_FILE" ]]; then
	cat >>"$PLIST_PATH" <<EOF
      <string>--env-file</string>
      <string>${ENV_FILE}</string>
EOF
fi

cat >>"$PLIST_PATH" <<EOF
    </array>
    <key>StandardOutPath</key><string>${LOG_DIR}/${LABEL}.out.log</string>
    <key>StandardErrorPath</key><string>${LOG_DIR}/${LABEL}.err.log</string>
EOF

if [[ -n "$TOKEN" ]]; then
	cat >>"$PLIST_PATH" <<EOF
    <key>EnvironmentVariables</key>
    <dict>
      <key>CODEX_GATEWAY_TOKEN</key><string>${TOKEN}</string>
    </dict>
EOF
fi

cat >>"$PLIST_PATH" <<EOF
  </dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Installed and started: $PLIST_PATH"
