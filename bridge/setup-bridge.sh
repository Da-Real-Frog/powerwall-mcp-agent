#!/usr/bin/env bash
# Turns this machine into a one-address router: hosts on the home network can
# reach the Powerwall gateway's local API (192.168.91.1) through the gateway's
# own Wi-Fi (TEG-xxx), and nothing else on that network.
#
# Usage: sudo ./setup-bridge.sh <teg-interface> <home-interface>
#   <teg-interface>   the Wi-Fi interface joined to TEG-xxx (e.g. wlp3s0)
#   <home-interface>  the interface on the home network (e.g. enp0s10 or wlx...)
#
# Safe to re-run. Holds no passwords: Wi-Fi is joined separately with nmcli --ask.
set -euo pipefail

GATEWAY=192.168.91.1

if [[ $# -ne 2 ]]; then
  echo "Usage: sudo $0 <teg-interface> <home-interface>" >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo." >&2
  exit 1
fi

TEG_IF=$1
HOME_IF=$2
for ifname in "$TEG_IF" "$HOME_IF"; do
  if ! ip link show "$ifname" >/dev/null 2>&1; then
    echo "No interface named '$ifname'. Check 'ip -br link'." >&2
    exit 1
  fi
done
if [[ "$TEG_IF" == "$HOME_IF" ]]; then
  echo "The TEG and home interfaces must be different." >&2
  exit 1
fi

echo "==> Enabling IP forwarding"
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/90-teg-bridge.conf
sysctl -q --system

echo "==> Installing iptables-persistent"
export DEBIAN_FRONTEND=noninteractive
echo 'iptables-persistent iptables-persistent/autosave_v4 boolean false' | debconf-set-selections
echo 'iptables-persistent iptables-persistent/autosave_v6 boolean false' | debconf-set-selections
apt-get install -y -qq iptables-persistent >/dev/null

echo "==> Writing firewall rules"
# Our rules live in their own chains, so re-running replaces them cleanly.
iptables -N TEG_BRIDGE 2>/dev/null || iptables -F TEG_BRIDGE
iptables -A TEG_BRIDGE -i "$HOME_IF" -o "$TEG_IF" -d "$GATEWAY" -p tcp --dport 443 -j ACCEPT
iptables -A TEG_BRIDGE -i "$HOME_IF" -o "$TEG_IF" -d "$GATEWAY" -p icmp -j ACCEPT
iptables -A TEG_BRIDGE -i "$TEG_IF" -o "$HOME_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A TEG_BRIDGE -o "$TEG_IF" -j DROP
iptables -A TEG_BRIDGE -i "$TEG_IF" -j DROP
iptables -C FORWARD -j TEG_BRIDGE 2>/dev/null || iptables -I FORWARD 1 -j TEG_BRIDGE

iptables -t nat -N TEG_BRIDGE_NAT 2>/dev/null || iptables -t nat -F TEG_BRIDGE_NAT
iptables -t nat -A TEG_BRIDGE_NAT -o "$TEG_IF" -d "$GATEWAY" -j MASQUERADE
iptables -t nat -C POSTROUTING -j TEG_BRIDGE_NAT 2>/dev/null || iptables -t nat -I POSTROUTING 1 -j TEG_BRIDGE_NAT

netfilter-persistent save >/dev/null

echo "==> Keeping it awake with the lid closed"
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/90-teg-bridge.conf <<'CONF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
CONF

if [[ -d /etc/NetworkManager/conf.d ]]; then
  echo "==> Turning off Wi-Fi power saving"
  cat > /etc/NetworkManager/conf.d/90-teg-bridge.conf <<'CONF'
[connection]
wifi.powersave=2
CONF
fi

echo
echo "Done. Reboot once (sudo reboot) so the lid and Wi-Fi settings take effect."
# shellcheck disable=SC2028  # the \n is meant to print literally
echo "Then, from percula: curl -sk -m 5 -o /dev/null -w '%{http_code}\\n' https://$GATEWAY/"
