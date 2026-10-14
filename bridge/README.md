# Garage bridge: reaching the gateway's local API

The Powerwall gateway only answers its local API (TEDAPI, at `192.168.91.1`) on its own
Wi-Fi network, `TEG-23A`. percula can't hear that network from the house, so a Raspberry Pi
3 B+ called `garage-bridge` joins it and forwards percula's requests:

```
percula ──home network──▶ garage-bridge (eth0) ── (wlan0) TEG-23A Wi-Fi ──▶ gateway (192.168.91.1)
```

- **`eth0`**, the Pi's Ethernet port, plugs into a switch on the home network (Fishtank-IOT).
- **`wlan0`**, the Pi's built-in Wi-Fi, joins `TEG-23A`.

[`setup-bridge.sh`](setup-bridge.sh) does the routing. Everything else is below.

No password goes in a file, a command line or this repo. The TEG-23A password is typed at an
`nmcli --ask` prompt, and NetworkManager keeps it on the Pi in a root-only file so it can
reconnect after a reboot.

## 1. Write Raspberry Pi OS (on your Mac)

1. Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. Choose **Raspberry Pi 3**, then **Raspberry Pi OS (other) ▸ Raspberry Pi OS Lite (64-bit)**.
   "Lite" has no desktop, which is all a bridge needs.
3. Choose the target: the microSD card (through a card reader), or a USB stick. The Pi 3 B+
   can boot from either; with a USB stick, leave the SD slot empty.
4. When it offers to customise the OS, set:
   - **Hostname:** `garage-bridge`
   - **Username and password:** your own (not `pi`)
   - **Wireless LAN:** leave the network name and password empty, and set only the
     **country** (e.g. US).
   - **Services:** turn on **SSH**. If you already have an SSH key on your Mac, pick
     "public-key authentication only" and Imager fills it in for you.
5. Write it. It erases the card or stick.

## 2. First boot

1. Put the card or stick in the Pi, plug Ethernet into your network, then plug in the kit's
   power supply (a weak phone charger makes the Wi-Fi drop out). Give it a couple of minutes.
2. From your Mac: `ssh <user>@garage-bridge.local`. If the name doesn't resolve, find its
   address in your router's client list.
3. Update it, install git, and make sure Wi-Fi isn't blocked:
   ```
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git
   sudo raspi-config nonint do_wifi_country US
   sudo rfkill unblock wifi
   sudo reboot
   ```
4. After it comes back, `nmcli device` should list `eth0` (connected) and `wlan0`
   (disconnected).

## 3. Lock down SSH

Skip the first block if Imager already set up key-only login.

On your Mac (the first line only if `ls ~/.ssh/id_ed25519.pub` finds nothing):
```
ssh-keygen -t ed25519
ssh-copy-id <user>@garage-bridge.local
ssh <user>@garage-bridge.local
```

Once that last line logs you in without the Pi's password, turn password logins off on the Pi.
Keep that session open and test from a new Terminal window, so a typo can't lock you out:
```
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/10-keys-only.conf
sudo systemctl restart ssh
```

Your account is already in the `sudo` group. To stop sudo asking for your password
(reasonable on a key-only, single-purpose box):
```
echo '<user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/010-<user>
sudo chmod 440 /etc/sudoers.d/010-<user>
sudo visudo -c
```
`visudo -c` should say "parsed OK".

## 4. Optional: move from a USB stick to the SD card

If you booted from a USB stick and want to run from the SD card instead, put the card in the
Pi's slot and clone the running system onto it:
```
git clone https://github.com/geerlingguy/rpi-clone.git
sudo cp rpi-clone/rpi-clone /usr/local/sbin/
lsblk
sudo rpi-clone mmcblk0
sudo poweroff
```
`lsblk` should show the card as `mmcblk0`, about the card's size. rpi-clone asks before it
wipes the card. After the power-off, unplug the USB stick and power the Pi back on: it boots
from the card with everything above already in place. Staying on the USB stick is fine too.

## 5. Put it in place

Move the Pi to where it will live: Ethernet into a switch on the home network, and close
enough to the gateway to hear TEG-23A. Check the signal from there:
```
nmcli device wifi rescan ifname wlan0; nmcli device wifi list ifname wlan0 | grep TEG
```
The SIGNAL column should be above about 30. If it's lower, move the Pi closer to the gateway.

## 6. Join TEG-23A

It asks for the Wi-Fi password printed on the gateway label:
```
sudo nmcli --ask device wifi connect TEG-23A ifname wlan0
sudo nmcli connection modify TEG-23A ipv4.never-default yes ipv6.method disabled connection.autoconnect-retries 0
sudo nmcli connection up TEG-23A
```

`never-default` keeps the Pi's internet on Ethernet. `autoconnect-retries 0` means it keeps
trying forever if the gateway's Wi-Fi blips.

Check: `ip -br addr` should show a `192.168.91.x` address on `wlan0` and a home address on
`eth0`, and this should print a 3-digit code:
```
curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://192.168.91.1/
```

## 7. Turn on forwarding

```
git clone https://github.com/Da-Real-Frog/powerwall-mcp-agent.git
sudo ./powerwall-mcp-agent/bridge/setup-bridge.sh wlan0 eth0
sudo reboot
```

The script enables IP forwarding, lets the home network reach `192.168.91.1` on port 443 (and
ping) and nothing else on TEG-23A, saves those rules so they come back after a reboot, and
turns off Wi-Fi power saving. It also sets "ignore lid close", which does nothing on a Pi.
It's safe to run again.

## 8. Give the Pi a fixed address

In your router's admin page, find the DHCP reservations (sometimes "address reservation" or
"static lease") and reserve an address for the Pi's **Ethernet** MAC address
(`ip link show eth0`, the `link/ether` value). Reboot the Pi and check it got that address.

That address must be on the same network as percula (for example both `192.168.2.x`). If the
switch the Pi is on hands out a different range from percula's, say so in the project thread.

## 9. Point percula at it

On percula, remove the earlier attempts first. Then find which of percula's connections is on
the Pi's network: `ip route get <pi-ip>` names the device (`dev …`), and the connection list
maps devices to connection names. percula has a VLAN for the IoT network, so this may not be
its main wired connection. `<pi-ip>` is the reserved address from step 8.

```
sudo ip route del 192.168.91.1 2>/dev/null
nmcli connection show | grep TEG
ip route get <pi-ip>
nmcli -t -f NAME,DEVICE connection show --active
```

If the second line lists a TEG connection from the USB adapter test, delete it with
`sudo nmcli connection delete <its-name>`. `ip route get <pi-ip>` should show only a `dev`, no
`via`; if it shows a `via`, the Pi is on a different network from percula. Take the
connection name (before the colon) whose device matches, and add the route to it:

```
sudo nmcli connection modify "<connection-name>" +ipv4.routes "192.168.91.1/32 <pi-ip>"
sudo nmcli connection up "<connection-name>"
ip route get 192.168.91.1
curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://192.168.91.1/
```

`ip route get` should say `via <pi-ip>` on that device, and curl should print `200`. If curl
prints `000`, `sudo iptables -L TEG_BRIDGE -v -n` on the Pi shows whether percula's packets
reach it at all.

## 10. Check that TEDAPI answers

With `PW_GW_PWD=<label password>` in percula's `mcp-server.env`, this logs in to the gateway's
local API through the bridge and prints the per-string readings of both Powerwall+ inverters:

```
docker run --rm --env-file /home/nicolaskeller/mcp-server.env powerwall-mcp-server uv run --no-sync python -c "
import os, json
from pypowerwall.tedapi import TEDAPI
t = TEDAPI(os.environ['PW_GW_PWD'])
print('logged in:', bool(t.get_din()))
v = t.vitals() or {}
print(json.dumps({k: v[k] for k in v if k.startswith(('PVAC', 'PVS'))}, indent=1))"
```

`logged in: True` and two `PVAC--…` entries mean the bridge is done.

## Living in the garage

- It boots by itself when power comes back after an outage, and rejoins TEG-23A on its own.
- Check it from your Mac with `ssh <user>@<pi-ip> 'nmcli device; ip -br addr'`.
- Keep it on the kit's power supply. `vcgencmd get_throttled` printing anything other than
  `throttled=0x0` means the supply is too weak or it's running hot.
- The same script works on any Debian or Ubuntu machine with NetworkManager, for example the
  white MacBook, with its own interface names in place of `wlan0` and `eth0`.
