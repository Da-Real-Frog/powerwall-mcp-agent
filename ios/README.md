# Home Energy for iPhone

A native SwiftUI chat app for asking Claude about the batteries, solar panels, weather and grid from an iPhone on the home Wi-Fi. It's a front end for the [Home Chat service](../web/README.md) on percula: the app sends the conversation to `http://percula.local:8080/api/chat` and shows the answer as it streams in. The Claude API key stays on percula; the app holds no secrets.

The Home Chat service has to be running on percula first (see [web/README.md](../web/README.md)). Check from the Mac with `curl http://percula.local:8080/healthz`, which should print `{"ok":true}`.

## Opening it
Open `ios/HomeEnergy.xcodeproj` in Xcode 16 or later. The code is plain Swift files in `ios/HomeEnergy/`, so it can also be read and reviewed in VS Code.

## Putting it on her iPhone
1. In Xcode, select the **HomeEnergy** target, open **Signing & Capabilities**, and pick your Apple ID under **Team** (Xcode > Settings > Accounts to add it). If Xcode says the bundle identifier is taken, change `net.percula.HomeEnergy` to something else that's yours.
2. Connect her iPhone to the Mac with a cable, unlock it, and tap **Trust**.
3. On her iPhone, turn on **Settings > Privacy & Security > Developer Mode** and restart when asked (the switch appears after step 2).
4. In Xcode, choose her iPhone as the run destination and press Run (⌘R).
5. The first time, the iPhone blocks the app: go to **Settings > General > VPN & Device Management**, tap your Apple ID and **Trust**.
6. When the app first asks to find devices on the local network, tap **Allow**. Without it, the app can't reach percula.

With a free Apple ID, the app stops opening after 7 days and has to be installed again from Xcode (steps 2 and 4). With a paid Apple Developer Program membership ($99 a year) it lasts a year, and TestFlight can install and update it on her phone without a cable.

## Using it
- Type a question or tap one of the suggestions. While tools run, the answer shows what it's doing ("Checking the battery…").
- The stop button ends an answer early. **New chat** starts over. The conversation is kept on the phone between launches.
- The gear opens Settings, where the server address can be changed. If `percula.local` doesn't resolve, use percula's IP address, such as `http://192.168.1.20:8080`, ideally after giving percula a reserved address in the router.

It only works on the home Wi-Fi. Away from home it says it can't reach the house's assistant.

## Files
- `HomeEnergy/ChatClient.swift`: the request to `/api/chat` and the parser for its newline-delimited JSON events.
- `HomeEnergy/ChatModel.swift`: the conversation, streaming updates, stop and new chat, saved on the phone.
- `HomeEnergy/ContentView.swift`: the chat screen, message bubbles and the settings sheet.
- `Info.plist`: allows plain http on the local network and explains the local network prompt.
