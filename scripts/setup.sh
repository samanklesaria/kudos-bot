#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v systemctl &>/dev/null && [[ -d /etc/systemd/system ]]; then
    # Linux: install systemd units
    sudo cp "$PROJECT_DIR"/systemd/*.service "$PROJECT_DIR"/systemd/*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now kudos-dashboard
    sudo systemctl enable --now kudos-backfill.timer
    sudo systemctl enable --now kudos-weekly-reminder.timer
    sudo systemctl enable --now kudos-queue-processor.timer
    echo "Systemd units installed and enabled."
    systemctl list-timers --all | grep kudos
fi
