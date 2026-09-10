#!/usr/bin/env bash
# One-shot install on a Linux box (VPS, Raspberry Pi, spare laptop).
# Usage:  sudo bash deploy/setup.sh [target-user]
set -euo pipefail

TARGET_USER="${1:-${SUDO_USER:-$USER}}"
APP_DIR=/opt/trade-bot
REPO=https://github.com/bergnathaniel/trade-bot
BRANCH=claude/phone-monetization-options-jf5ug0

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH" && git -C "$APP_DIR" checkout "$BRANCH" && git -C "$APP_DIR" pull origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO" "$APP_DIR"
fi

[ -f "$APP_DIR/config.json" ] || cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
mkdir -p "$APP_DIR/state"
chown -R "$TARGET_USER" "$APP_DIR"

if [ ! -f /etc/tradebot.env ]; then
  TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  cat > /etc/tradebot.env <<ENV
TRADEBOT_DASHBOARD_TOKEN=$TOKEN
# TRADEBOT_TELEGRAM_TOKEN=
# TRADEBOT_TELEGRAM_CHAT_ID=
ENV
  chmod 600 /etc/tradebot.env
  echo "generated dashboard token: $TOKEN"
fi

sed "s/%i/$TARGET_USER/" "$APP_DIR/deploy/tradebot.service" > /etc/systemd/system/tradebot.service
systemctl daemon-reload
systemctl enable --now tradebot
systemctl --no-pager status tradebot | head -5

echo
echo "Dashboard: http://<this-host>:8000/?token=$(grep TRADEBOT_DASHBOARD_TOKEN /etc/tradebot.env | cut -d= -f2)"
echo "Bind it behind Tailscale or an SSH tunnel - it is plain HTTP."
