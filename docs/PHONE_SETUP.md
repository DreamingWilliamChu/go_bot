# Using the app from your iPhone

The Mac runs everything: the web server, KataGo and photo recognition. The iPhone only shows the web page and takes photos. Nothing is sent to the internet.

## 1. Start the server for the local network

On the Mac:

```bash
cd ~/go_bot
./scripts/start.sh --lan
```

The terminal prints a box like this:

```
| gobot is running:  http://localhost:8000/  (on this Mac)
| Phone (same Wi-Fi):  http://192.168.1.23:8000/
| Pairing link:        http://192.168.1.23:8000/#pair=Xa7...
```

It also prints a **QR code**. The same QR code is shown on the Mac under **Engine** at http://localhost:8000.

- The Mac and the iPhone must be on the **same Wi-Fi network**. Guest networks often block devices from seeing each other.
- macOS may ask whether Python may accept incoming network connections. Choose **Allow**.
- `--lan` binds to all network interfaces of the Mac but only answers devices that have the pairing code. The code is stored in `data/token`; `--new-token` replaces it, which logs out paired phones. Do not use `--lan` on public networks.

## 2. Pair the iPhone

Point the iPhone **Camera** app at the QR code and tap the link. The page opens in Safari and pairs automatically. The pairing is remembered in a cookie, so later you can just open `http://<mac-ip>:8000/` (or add it to the Home Screen with Share → Add to Home Screen).

If the Mac's IP address changes (another network, a router restart), use the new address printed in the terminal; the pairing code stays the same.

## 3. Taking photos

On the **Photo** tab:

- **📷 Take a photo** opens the iPhone camera. The photo is sent to the Mac in full resolution. This works over plain `http://` and is the recommended way.
- **🖼 Choose a photo** picks from the photo library. iOS normally converts HEIC to JPEG on upload; if it sends HEIC, the Mac decodes it.

## 4. Optional: live camera preview (needs HTTPS)

Safari only allows the *live camera preview* (`getUserMedia`) on secure pages. `localhost` counts as secure, but `http://192.168.x.x` does not. To use the live preview from the iPhone:

```bash
./scripts/start.sh --lan --https
```

This creates a private certificate authority for this Mac (in `data/certs/`) and a server certificate for the Mac's LAN address. On the iPhone, once:

1. Open `http://<mac-ip>:8080/setup` in Safari (the address is printed in the terminal), tap **Download the gobot certificate**, then **Allow**.
2. **Settings → General → VPN & Device Management** (or the "Profile Downloaded" item at the top of Settings) → *gobot local CA* → **Install**.
3. **Settings → General → About → Certificate Trust Settings** → turn on full trust for **gobot local CA**.
4. Scan the pairing QR code again. The app is now at `https://<mac-ip>:8000/` and the Photo tab offers **Use live camera view**.

The certificate authority only exists on your Mac and phone. You can remove it at any time under VPN & Device Management. If the Mac's address changes, a new server certificate is issued automatically from the same authority, so the phone does not need to re-install anything.

## Troubleshooting

| Problem | Fix |
|---|---|
| The page doesn't load on the phone | Both devices on the same Wi-Fi? Mac firewall prompt allowed? Use the IP printed by `start.sh --lan`, not `localhost`. |
| "Pair this device" | Scan the QR code again (or type the code from `data/token`). |
| "Connecting to the Mac…" stays | The server was stopped or the Mac went to sleep. Restart `./scripts/start.sh --lan`. |
| HTTPS warning in Safari | The certificate trust step (3) is missing, or you opened the IP over https before installing the profile. |
| Live camera button missing | You are on `http://`. Use "Take a photo" or set up HTTPS as above. |
