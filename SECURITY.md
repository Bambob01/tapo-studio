# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository (<https://github.com/Bambob01/tapo-studio/security/advisories/new>).

Include what you found, how to reproduce it, and what an attacker could do with it. We'll acknowledge it, fix it, and credit you if you'd like.

Only the latest release is supported.

## What Tapo Studio protects

Tapo Studio runs a small web server on your computer that can control your lights and holds your Tapo login. The design goal is that **nothing except you, using your browser on your computer, can use it**.

| Threat | Defence |
|---|---|
| Another device on your network or the internet reaches the app | Binds to `127.0.0.1` only. Never opens a port to the network. |
| A website you visit controls your lights (CSRF) | Every API call needs a secret token in a header. Requests must be JSON (which forces a CORS pre-flight that is never approved), and the `Origin` and `Sec-Fetch-Site` headers are checked. |
| A website reaches the app by pointing its own domain at `127.0.0.1` (DNS rebinding) | The `Host` header must be exactly `127.0.0.1:<port>` or `localhost:<port>`. |
| Another program or Windows account on the same PC reads the token from the app | The token is never served. It's created at start-up and handed to your browser only in the URL fragment (`#k=...`), which browsers never send to a server. |
| Script injection via names you type, scene data, or imported JSON | The page only uses `textContent` (a test checks it never uses `innerHTML` or `eval`), data is rebuilt from known fields on save, and a strict Content-Security-Policy blocks inline and external scripts and framing. |
| Your Tapo password is stolen from disk | Encrypted with Windows DPAPI (current user), stored in `%LOCALAPPDATA%\TapoStudio`, never returned by the API or logged. |
| Your password is sent to a device that isn't yours | "Search my network" uses the devices' own announcements and sends no login. Each light's hardware ID is saved, and a different device at the same IP gets no login. Devices announcing an old insecure login scheme are refused. |
| The app is used to reach other networks | Light addresses must be in 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16. |
| Malformed or oversized input crashes the app or the lights | Request-size limit that also covers chunked bodies, `NaN`/`Infinity` rejected, every value clamped, effects capped to sizes the strip accepts. |
| Overlapping requests leave something running that can't be stopped | Live-mode start and stop are serialised (regression test included). |
| Supply chain | Exact dependency versions in `requirements.txt`, installed as prebuilt wheels only (no build scripts run). `start.bat` never runs look-alike programs from its own folder. |

The tests in `tests/test_server_security.py` and `tests/test_core_models.py` encode these attacks as regression tests.

## Known limitations

Please read these before relying on the app:

- **Anyone on your Wi-Fi who knows your Tapo email and password can control your lights**, with this app or any other. That's how the lights work, not something this app can change. Use a strong, unique Tapo password and a network you trust.
- **A device that spoofs both your light's IP address and hardware ID** (possible for someone already on your LAN) could still receive a login attempt. The `tapo` library supports an older login scheme in which the password is only Base64-encoded, so this is a real risk on an untrusted network.
- **Linux and macOS:** the password is stored base64-encoded, not encrypted, in a file readable only by you. Use full-disk encryption, or wait for keyring support (on the roadmap).
- **Dependencies are pinned but not hash-checked.** A compromised PyPI release of an exact pinned version is unlikely but not impossible.
- **The app has had limited testing on real hardware.** Treat it as alpha software, and note it sends commands your lights' firmware may not expect.
- **The settings file lives in your user profile.** Anything running as you can read it (and could ask Windows to decrypt the password, since DPAPI protects against other users, not other programs running as you).
- The control panel is plain HTTP on the loopback interface. That's normal for a local tool, but it means software that can read your local network traffic on the same machine can see it.

## Audit history

The first release was reviewed with real attack scripts against a running copy. This was an AI-assisted review, **not a professional audit**, so please treat it as a starting point and not a guarantee.

It found no critical or high issues. It found four medium issues (the token readable by other local programs, the password reaching non-strip devices, a live-mode race, and loose dependency pinning) and a number of low and informational ones. Everything medium or low was fixed before release and has a regression test, except hash-checking of dependencies. A few informational items remain, such as a small race in the start-up check for a free port.
