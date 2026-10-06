#!/usr/bin/env bash
# Install tuxedo-power-profiles-adapter on Fedora.
# Fedora version of the AUR PKGBUILD:
# https://aur.archlinux.org/cgit/aur.git/tree/PKGBUILD?h=tuxedo-power-profiles-adapter-git

set -euo pipefail

NAME=tuxedo-power-profiles-adapter
BUS=org.freedesktop.UPower.PowerProfiles
SRC="$(dirname "$(realpath "$0")")"

write() { sudo install -Dm644 /dev/stdin "$1"; }

rpm -q tuxedo-control-center >/dev/null || {
  echo "Install tuxedo-control-center first (from the TUXEDO repo)." >&2
  exit 1
}

for pkg in tuned-ppd power-profiles-daemon; do
  if rpm -q $pkg >/dev/null; then
    echo "$pkg conflicts with this adapter. Remove it with: sudo dnf remove $pkg" >&2
    exit 1
  fi
done

sudo dnf install -y python3-dbus-next

sudo install -Dm755 "$SRC/adapter.py" /usr/bin/$NAME

if [[ -e /etc/$NAME/config.toml ]]; then
  echo "Keeping existing /etc/$NAME/config.toml"
else
  sudo install -Dm644 "$SRC/config.toml" /etc/$NAME/config.toml
fi

PPD_VER=0.30
PPD_SHA256=528ee5b8ca0a27d8d66128ebf850e23be9571dc130cf2a82dd2463dac7d3a92f

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/ppd.tar.bz2" \
  https://gitlab.freedesktop.org/upower/power-profiles-daemon/-/archive/$PPD_VER/power-profiles-daemon-$PPD_VER.tar.bz2
echo "$PPD_SHA256  $TMP/ppd.tar.bz2" | sha256sum -c --quiet
tar xjf "$TMP/ppd.tar.bz2" -C "$TMP"
TPL="$TMP/power-profiles-daemon-$PPD_VER/data"

fill() {
  local template=$1 dest=$2
  shift 2
  sed -e "s/@dbus_name@/$BUS/g" -e "s/@dbus_iface@/$BUS/g" "$@" "$TPL/$template" | write "$dest"
}

fill power-profiles-daemon.dbus.conf.in /usr/share/dbus-1/system.d/$BUS.conf

fill power-profiles-daemon.dbus.service.in /usr/share/dbus-1/system-services/$BUS.service \
  -e "s/power-profiles-daemon\.service/$NAME.service/"

fill power-profiles-daemon.policy /usr/share/polkit-1/actions/$NAME.policy \
  -e "s|<vendor>.*</vendor>|<vendor>$NAME</vendor>|" \
  -e "s|<vendor_url>.*</vendor_url>|<vendor_url>https://github.com/olwig/$NAME</vendor_url>|"

# tccd's bus name can appear seconds after it starts; RestartSec=3 keeps retries under systemd's start limit.
write /usr/lib/systemd/system/$NAME.service <<EOF
[Unit]
Description=Tuxedo power profiles dbus adapter
$(grep -m1 '^Conflicts=' "$TPL/power-profiles-daemon.service.in") power-profiles-daemon.service
Wants=tccd.service
After=tccd.service

[Service]
Type=dbus
BusName=$BUS
ExecStart=/usr/bin/$NAME
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl reload dbus.service

# Must run before login: DMS checks NameHasOwner once, which doesn't trigger D-Bus activation.
sudo systemctl enable $NAME.service
sudo systemctl restart $NAME.service

echo "Installed. Check it with: systemctl status $NAME.service"
