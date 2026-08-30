# Remote access

Reach your dashboard from your phone, your laptop, or anywhere else — privately,
without publishing anything to the internet.

The short version: leave the bridge bound to `127.0.0.1` where it is, put a proxy
in front that already knows who you are, and tell the bridge which hostname that
proxy will use. [Tailscale Serve](https://tailscale.com/kb/1312/serve) does this
in one command and is what the rest of this page assumes, but nothing here is
Tailscale-specific — Caddy, nginx, Cloudflare Tunnel or an SSH tunnel all work
the same way.

## Set it up

**1. Put an authenticating proxy in front.** With Tailscale installed and this
machine on your tailnet:

```bash
tailscale serve --bg 4173
```

That gives you `https://<machine>.<tailnet>.ts.net` with a real HTTPS
certificate, reachable by your devices and invisible to everyone else. Your
tailnet membership *is* the authentication.

**2. Tell the bridge that hostname is expected.** In `.env`:

```
REMOTE_HOSTS=your-machine.your-tailnet.ts.net
```

Restart the bridge. That's it — the dashboard itself needs no changes, and it is
already laid out for a phone screen.

Any device signed in to your tailnet can now open the URL. Nothing is exposed to
the public internet, and no port is forwarded.

## Read-only by default

Once `REMOTE_HOSTS` is set, remote visitors can browse, search and read the wiki.
They **cannot** run a skill, import, edit or delete. Working locally on the
machine itself is unaffected.

To let remote callers run queries and imports too:

```
REMOTE_READ_ONLY=0
```

Do that once you are confident the proxy in front really does authenticate —
see below for why that matters.

## Why the bridge won't just listen on the network

It would be simpler to bind the bridge to `0.0.0.0` and be done. The bridge
refuses, deliberately, and it is worth knowing why before you work around it.

`POST /run` executes an agent CLI with file-system access on your machine. The
token that authorizes it is served in the HTML of a page that anyone who can
reach the bridge can load — which means anyone who can reach the bridge can
drive it. That is a perfectly sound design when reaching it at all requires being
on your machine. It becomes a way for strangers to run code on your computer the
moment it doesn't.

Putting an authenticating proxy in front keeps that assumption true. It is why
`REMOTE_HOSTS` is an explicit allowlist rather than a switch, and why the bind
never changes.

Two things to avoid:

- **Don't use `tailscale funnel`.** Same command family, but it publishes to the
  open internet, which removes the assumption everything above rests on.
- **Don't put a publicly-resolvable hostname in `REMOTE_HOSTS`.** Facing this at
  the internet isn't a configuration change — it needs a different authentication
  model than a token in a page.

## Keeping it running when the app is closed

By default the bridge is a child of SecondBrain.app, so quitting the app takes
the dashboard down with it — awkward when the whole point is reaching it from
somewhere else. On macOS, hand it to `launchd` instead:

```bash
./launchd/install.sh              # install; restarts on crash and after reboot
./launchd/install.sh --uninstall  # back to an app-owned bridge
```

The app then *adopts* the running bridge instead of starting its own.

One side effect worth knowing, because it otherwise looks like a bug: an adopted
bridge doesn't receive the environment the app would have injected, so the app's
**Engine and Model menus stop taking effect** and `.env` becomes the only place
that sets them.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `403 forbidden` from the remote URL | The hostname in `REMOTE_HOSTS` doesn't match what the proxy sends. Check the bridge's stderr — it logs every request — and match it exactly. |
| Works locally, not remotely | The bridge only accepts hostnames it was told about. `REMOTE_HOSTS` unset means localhost only. |
| Remote reads fine, writes fail | That's `REMOTE_READ_ONLY`, which is on by default. |
| Dashboard vanishes when you quit the app | Install the LaunchAgent above. |
| Engine/Model menu changes do nothing | Expected with the LaunchAgent installed — use `.env`. |
