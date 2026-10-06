#!/usr/bin/env bash
# Remove everything install-fedora.sh installed.
#
#   ./uninstall-fedora.sh          remove, but keep /etc config
#   ./uninstall-fedora.sh --purge  also remove /etc config

set -euo pipefail

NAME=tuxedo-power-profiles-adapter
BUS=org.freedesktop.UPower.PowerProfiles

FILES=(
  /usr/bin/$NAME
  /usr/lib/systemd/system/$NAME.service
  /usr/share/dbus-1/system.d/$BUS.conf
  /usr/share/dbus-1/system-services/$BUS.service
  /usr/share/polkit-1/actions/$NAME.policy
)

# Fails harmlessly if the service was never installed.
sudo systemctl disable --now $NAME.service 2>/dev/null || true

sudo rm -fv "${FILES[@]}"

if [[ "${1:-}" == --purge ]]; then
  sudo rm -rfv /etc/$NAME
else
  echo "Kept /etc/$NAME (use --purge to remove it)"
fi

sudo systemctl daemon-reload
sudo systemctl reload dbus.service

echo "Uninstalled. To get Fedora's default power profiles back: sudo dnf install tuned-ppd"
