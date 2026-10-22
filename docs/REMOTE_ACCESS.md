# Sign-in and remote access

The app can be reached from anywhere (mobile data, another Wi-Fi) through a **Cloudflare Tunnel**. Every device other than the Mac itself has to **sign in** with your username and password.

## 1. Set the sign-in password (once)

On the Mac, open http://localhost:8000 → **Engine** → **Account**. Choose a username (default `admin`) and a password of at least 8 characters, then **Save**. The Mac itself is always trusted, so it does not ask for the old password.

You can also do it in the terminal:

```bash
uv run gobot account set                    # asks for the new password twice
uv run gobot account set --username alice   # also change the username
uv run gobot account show                   # username, and whether a password is set
uv run gobot account logout-all             # sign out every device
```

Use the terminal command if you forget the password. Changing the password signs out every other device. On other devices, **Engine → Account** can change the username and password too, but it asks for the current password.

## 2. Start with the tunnel

```bash
brew install cloudflared          # once (setup_mac.sh installs it too)
./scripts/start.sh --tunnel       # remote access only
./scripts/start.sh --lan --tunnel # remote access + QR pairing on the Wi-Fi
```

After a few seconds the terminal prints the public address, for example `https://ripe-lemon-tide-forest.trycloudflare.com`, with a QR code. The same QR code appears under **Engine → Remote access** on the Mac. Open the address on any device and sign in.

This is a Cloudflare **quick tunnel**: it needs no Cloudflare account, but it gets a **new random address every time the server starts**, and Cloudflare offers it for testing without uptime guarantees.

## 3. Your own domain, the same on every device

With a domain on Cloudflare (a free plan is enough), the app gets a fixed address such as `https://go.example.com`. Set it up once in the repository; then every Mac that clones or pulls the repository connects to that address when you run `./scripts/start.sh`.

**What is shared through GitHub and what is not:**

| | Where | In git? |
|---|---|---|
| Hostname (`go.example.com`) | `config/tunnel.json` | **yes**: not secret, it is public DNS |
| Tunnel token | `data/tunnel_token` on each device | **no**: anyone with it could run your tunnel and serve their own content on your domain |
| Sign-in password | `data/account.json` on each device | **no**: set it on each device (step 1) |

### Once, in Cloudflare

1. Add your domain to Cloudflare, if it is not there yet: **Add a site** in the dashboard, then change the domain's name servers at your registrar to the two Cloudflare shows.

Then create the tunnel, **either** from the terminal (no Zero Trust sign-up, so no payment card) **or** in the Zero Trust dashboard (steps 2–4).

**From the terminal**, once the domain shows *Active* in Cloudflare:

```bash
uv run gobot tunnel create --hostname go.example.com
```

The first time, a browser window opens: sign in to Cloudflare and choose your domain. gobot then creates a tunnel named `gobot`, adds the DNS record for `go.example.com`, and saves the token on this Mac. Skip steps 2–4 and the `tunnel setup` command below; commit `config/tunnel.json` as shown there. Other devices still get the token with `uv run gobot tunnel token`. Print it on this Mac with `cloudflared tunnel token gobot`, or run `uv run gobot tunnel create` on the other device too, after its own browser sign-in.

**In the Zero Trust dashboard:**

2. Go to **Zero Trust → Networks → Tunnels → Create a tunnel**, choose **Cloudflared** and name it, for example `gobot`.
3. The next page shows an install command containing a long token starting with `eyJ`. Copy it, but **do not run the command**; gobot starts cloudflared itself.
4. Under **Public hostname**, add subdomain `go` and domain `example.com`, with service type **HTTP** and URL **`127.0.0.1:8001`**. If you start the app with `--port N`, use port N+1. Save. Cloudflare creates the DNS record.

### Once, in the repository (on this Mac)

```bash
uv run gobot tunnel setup --hostname go.example.com   # writes config/tunnel.json, then asks for the token
git add config/tunnel.json && git commit -m "Remote access at go.example.com" && git push
```

The token prompt hides what you paste. You can paste the bare token or the whole install command.

### Once on every other device

```bash
git pull                       # or git clone, then ./scripts/setup_mac.sh
uv run gobot tunnel token      # paste the same token
uv run gobot account set       # this device's sign-in password
```

### From then on, on any device

```bash
./scripts/start.sh             # starts the tunnel by itself
./scripts/start.sh --no-tunnel # this time without remote access
```

A device that has pulled the repository but has no token yet starts normally and prints how to add it.

**Run the app with the tunnel on one device at a time.** If two Macs run it at once, Cloudflare spreads visitors over both. They have separate sign-ins and boards, so pages would seem to sign out or change at random. Stop the app on one Mac before starting it on another.

Other commands: `uv run gobot tunnel show` shows the hostname and which tunnel this device's token belongs to. `uv run gobot tunnel forget-token` removes the token from this device. `--no-autostart` on `setup` makes the tunnel start only with `--tunnel`. Without `config/tunnel.json`, `--tunnel` falls back to a quick tunnel. The token can also come from the `GOBOT_TUNNEL_TOKEN` environment variable.

## How it is protected

- **A separate port for the tunnel.** `cloudflared` runs on the Mac, so tunnel traffic would look like it came from the Mac. The app therefore gives the tunnel its own port, `8001`, bound to `127.0.0.1` only. On that port nothing is trusted: the "this Mac is trusted" rule and the Wi-Fi pairing code do not work. Only a sign-in session lets a request through. The pairing QR code and pairing link are never shown through the tunnel.
- **Password storage.** The password is stored as a salted scrypt hash in `data/account.json` (file mode 600).
- **Sessions.** Sessions are random 256-bit cookies, stored as hashes, and last 30 days. The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` on HTTPS and through the tunnel.
- **Guessing limits.** After 5 wrong passwords from one address, sign-in from that address is locked for 30 s, then 60 s, and so on, up to 15 minutes. Over 30 failures a minute from all addresses together pause sign-in for everyone for a minute.
- **Encryption.** Cloudflare encrypts the connection between the phone and Cloudflare (HTTPS) and the tunnel between Cloudflare and the Mac. Cloudflare can see the traffic in between, as with any site it serves.

What the tunnel cannot protect: anyone who knows your password. Choose one you do not use elsewhere. If it may have leaked, change it; that signs every other device out at once. An analysis connection that is already open keeps working until it reconnects.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Remote access is OFF: cloudflared is not installed` | `brew install cloudflared`, then restart. |
| Own domain: "Bad gateway" or error 1033 | The tunnel is not running on any device, or the public hostname in Cloudflare does not point to `127.0.0.1:8001`. |
| Own domain: the server says the token is invalid | Copy the token again from the tunnel's page in Cloudflare and run `uv run gobot tunnel token`. |
| No address printed after ~30 s | Check the Mac's internet connection. Quick tunnels can also fail when Cloudflare rate-limits them; try again in a minute. |
| "No sign-in password has been set yet" | Set one on the Mac (step 1). |
| "Too many attempts" | Wait the time shown. On the Mac, `uv run gobot account set` resets the password. |
| The page loads but stays "Connecting to the Mac…" | The Mac went to sleep, or the server stopped. Keep the Mac awake while you use it remotely. |
