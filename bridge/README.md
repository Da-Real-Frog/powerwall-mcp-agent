# Garage bridge: reaching the gateway's local API

The Powerwall gateway only answers its local API (TEDAPI, at `192.168.91.1`) on its own
Wi-Fi network, `TEG-23A`. percula can't hear that network from the house, so an old white
MacBook in the garage joins it and forwards percula's requests:

```
percula ──home network──▶ MacBook ──TEG-23A Wi-Fi──▶ gateway (192.168.91.1)
```

The MacBook needs two network connections, because one Wi-Fi card can't join two networks:

- **Built-in Wi-Fi** joins `TEG-23A`.
- **The home side** is the MacBook's Ethernet port (best, if a cable reaches the garage), or a
  USB Wi-Fi adapter joined to `Fishtank-IOT`.

[`setup-bridge.sh`](setup-bridge.sh) does the routing part. Everything else is below.

No password goes in a file, a command line or this repo. Wi-Fi passwords are typed at
`nmcli --ask` prompts. NetworkManager keeps them on the MacBook in a root-only file so it
can reconnect after a reboot.

## Using a Raspberry Pi instead

A Raspberry Pi 3 B+ or newer does the same job with less setup: its Wi-Fi works out of the
box, and it boots again on its own after a power cut. On a Pi, the TEG interface is `wlan0`
and the home side is `eth0`. Do the steps in this section instead of steps 0 to 3, then carry
on from step 4.

**Write the SD card (on your Mac):**

1. Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. Choose your Pi model, then **Raspberry Pi OS (other) ▸ Raspberry Pi OS Lite (64-bit)**.
   "Lite" has no desktop, which is all a bridge needs. Then choose the microSD card.
3. When it offers to customise the OS, set:
   - **Hostname:** `garage-bridge`
   - **Username and password:** your own (not `pi`)
   - **Wireless LAN:** leave the network name and password empty, and set only the
     **country** (e.g. US). The TEG-23A password is typed later at an `nmcli --ask` prompt.
   - **Services:** turn on **SSH**, with password login or your Mac's public key.
4. Write the card. It erases it.

**First boot (anywhere with Ethernet):**

1. Put the card in the Pi, plug Ethernet into your network, then plug in the kit's power
   supply. Give it a couple of minutes.
2. From your Mac: `ssh <user>@garage-bridge.local`. If the name doesn't resolve, find its
   address in your router's client list.
3. Update it and make sure Wi-Fi isn't blocked:
   ```
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git
   sudo raspi-config nonint do_wifi_country US
   sudo rfkill unblock wifi
   sudo reboot
   ```
4. After it comes back, `nmcli device` should list `eth0` (connected) and `wlan0`
   (disconnected).

Now follow steps 4 to 7 below with `<teg-if>` = `wlan0` and `<home-if>` = `eth0`, so step 5 is
`sudo ./bridge/setup-bridge.sh wlan0 eth0`. Use the power supply from the kit: a weak phone
charger makes the Pi's Wi-Fi drop out.

## 0. Before wiping anything

1. In the garage, open Big Sur's Wi-Fi menu and check that **TEG-23A** shows up with at least
   one bar. Do the same for **Fishtank-IOT** if the home side will be Wi-Fi. If TEG-23A isn't
   there, move the MacBook closer to the gateway before going further.
2. Apple menu ▸ About This Mac ▸ System Report, and note the **Model Identifier**:
   - `MacBook5,2`, `MacBook6,1` or `MacBook7,1` (2009–2010): fine, carry on.
   - `MacBook1,1` or `MacBook2,1` (2006): these have a 32-bit firmware that the Ubuntu USB
     won't boot from. Use the Debian 12 "netinst" image instead; the rest of this guide
     still applies.
   - Big Sur doesn't officially run on any white MacBook, so it was probably installed with
     OpenCore Legacy Patcher. That's fine: the Ubuntu install replaces it.
3. Copy off anything you want to keep. The install erases the whole disk.

## 1. Make the USB stick (on your current Mac)

1. Download **Ubuntu Server 24.04 LTS** (64-bit, "amd64") from
   <https://ubuntu.com/download/server>. 24.04 is picked over newer releases because it is
   well tested on 2009–2010 Macs and supported until 2029.
2. Install **balenaEtcher** from <https://etcher.balena.io>, pick the `.iso`, pick a USB stick
   of 4 GB or more (it gets erased), and click Flash. If macOS says the stick is unreadable
   afterwards, click Ignore.

## 2. Install Ubuntu (on the white MacBook)

Do this somewhere with an Ethernet cable to your router if you can: the installer needs the
internet to fetch the Wi-Fi driver.

1. Plug in the USB stick and Ethernet, power on while holding **Option (⌥)**, and choose the
   orange **EFI Boot** disk.
2. At the menu, pick **Try or Install Ubuntu Server**. If the screen goes black, reboot, press
   `e` on that line, add `nomodeset` just before the `---` on the `linux` line, and press F10.
3. Installer choices:
   - Language and keyboard: yours.
   - Type of install: **Ubuntu Server** (not minimized).
   - Network: the Ethernet port should get an address on its own. Leave Wi-Fi for later.
   - Storage: **Use an entire disk**, the internal one. Untick LVM if you like; either works.
   - Profile: server name `garage-bridge`, your own username and a new password.
   - **Install OpenSSH server**: yes, so you can run everything else from your Mac.
   - **Third-party drivers**: yes, if offered.
   - Featured snaps: none.
4. Remove the USB stick when told, and let it reboot. Log in, and note its home address with
   `ip -br addr`. From now on you can work from your Mac with `ssh <user>@<that-address>`.

## 3. Get the Wi-Fi working

```
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y network-manager
ip -br link
```

If a `wl…` interface is listed (often `wlp3s0` or `wlp2s0`), the Wi-Fi card works. If not,
check which Broadcom chip you have and install Broadcom's driver:

```
lspci -nn | grep -i network
sudo apt install -y bcmwl-kernel-source
sudo reboot
```

`bcmwl-kernel-source` covers the BCM4321, BCM4322 and BCM43224 chips these MacBooks use. After
the reboot, `ip -br link` should show a `wl…` interface. If it still doesn't, paste the
`lspci` line in the project thread.

## 4. Join the networks

Find the names with `nmcli device`. Below, `<teg-if>` is the built-in Wi-Fi and `<home-if>` is
the Ethernet port (`enp…`) or the USB adapter (`wlx…`).

**TEG-23A** (it asks for the password printed on the gateway label):
```
nmcli device wifi list ifname <teg-if> | grep TEG
sudo nmcli --ask device wifi connect TEG-23A ifname <teg-if>
sudo nmcli connection modify TEG-23A ipv4.never-default yes ipv6.method disabled connection.autoconnect-retries 0
sudo nmcli connection up TEG-23A
```

`never-default` keeps the MacBook's internet on the home side. `autoconnect-retries 0` means
it keeps trying forever if the gateway's Wi-Fi blips. The SIGNAL column in the first command
should be above about 30.

**Home side over Ethernet:** nothing to do, Ubuntu already set it up during install.

**Home side over a USB Wi-Fi adapter:**
```
sudo nmcli --ask device wifi connect Fishtank-IOT ifname <home-if>
sudo nmcli connection modify Fishtank-IOT connection.autoconnect-retries 0
```

Check both: `ip -br addr` should show a `192.168.91.x` address on `<teg-if>`, and a home
address on `<home-if>`. Then `curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://192.168.91.1/`
should print a 3-digit code.

## 5. Turn on forwarding

Get the script onto the MacBook (`git clone https://github.com/Da-Real-Frog/powewall-mcp-agent`,
or `scp` it from your local clone), then:

```
sudo ./bridge/setup-bridge.sh <teg-if> <home-if>
sudo reboot
```

It enables IP forwarding, lets the home network reach `192.168.91.1` on port 443 (and ping)
and nothing else on TEG-23A, saves those rules so they come back after a reboot, keeps the
MacBook awake with its lid closed, and turns off Wi-Fi power saving. Installing
`iptables-persistent` removes `ufw`, which Ubuntu ships switched off. It's safe to run again,
for example if an interface name changes.

## 6. Give the MacBook a fixed address

In your router's admin page, find the DHCP reservations (sometimes "address reservation" or
"static lease") and reserve an address for the MacBook's home-side MAC address
(`ip link show <home-if>`, the `link/ether` value). Reboot the MacBook and check it got that
address.

That address must be on the same network as percula (for example both `192.168.2.x`). If
Fishtank-IOT hands out a different range from percula's network, use the Ethernet port, or say
so in the project thread.

## 7. Point percula at it

On percula, remove the earlier attempts first, then add the route permanently. `<macbook-ip>`
is the reserved address from step 6.

```
sudo ip route del 192.168.91.1 2>/dev/null
nmcli connection show | grep TEG
nmcli -t -f NAME,DEVICE connection show --active
sudo nmcli connection modify "<percula-home-connection>" +ipv4.routes "192.168.91.1/32 <macbook-ip>"
sudo nmcli connection up "<percula-home-connection>"
ip route get 192.168.91.1
curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://192.168.91.1/
```

If the second line lists a TEG connection from the USB adapter test, delete it with
`sudo nmcli connection delete <its-name>` so percula doesn't try its own weak Wi-Fi path.
`<percula-home-connection>` is the active connection on percula's wired port from the third
line. `ip route get` should say `via <macbook-ip>`, and curl should print a 3-digit code. If
percula's network isn't run by NetworkManager (the third line prints nothing), use
`sudo ip route replace 192.168.91.1 via <macbook-ip>` for now and ask in the project thread
for the netplan version.

Once curl answers, re-run the `PW_GW_PWD` test from the project thread. Seeing inputs A1 to D1
alongside A to D means TEDAPI works.

## Living in the garage

- Leave it on its charger with the lid closed. A swollen or very old battery is a fire risk
  in a hot garage; if the case bulges, take the battery out and run it on the charger alone.
- After a power cut it comes back on its own only if it boots when power returns. Old MacBooks
  don't do that by themselves, so press the power button after an outage.
- Check it from your Mac with `ssh <user>@<macbook-ip> 'nmcli device; ip -br addr'`.
