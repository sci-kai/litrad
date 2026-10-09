# Web UI

```bash
litresearch serve                    # where and on which port: web.host / web.port in the config
litresearch serve --list-networks    # which networks this computer has
litresearch serve --network wlan0    # serve only on the WLAN (or eth0, all, local, an IP)
litresearch serve --choose           # pick the network interactively, optionally save it
```

`serve` only starts the web server and prints the address(es) to open; it
never opens a browser on the server itself, so a Raspberry Pi without a
screen never shows a text-mode browser. The UI loads nothing from the
internet (no fonts, CDNs or trackers). POST requests from other websites are
rejected. Stop the server with Ctrl+C.

## Where the web server is reachable

| `web.host` / `--network` | Reachable from |
|---|---|
| `local` (default) | only this computer (`127.0.0.1`); no password needed |
| `all` | every network of this computer (cable, WLAN, VPN, …) |
| `eth0`, `wlan0`, … | only that one network; other networks and `127.0.0.1` are refused |
| an IPv4 address of this computer, e.g. `192.168.50.2` | only that address |

Prefer interface **names** over addresses: the address is looked up at every
start, so a new DHCP address does not break your setup.
`litresearch serve --list-networks` shows the names, for example:

```
  local    this computer only (127.0.0.1)
  all      every network below
  eth0     cable (Ethernet): 192.168.50.2
  wlan0    WLAN: 10.100.7.134
```

If the configured network does not exist (e.g. the WLAN is down), the server
does not start and lists the available choices.

## Using it from your laptop (local network)

Run the UI on one machine (e.g. the Raspberry Pi) and open it from any device
in the same network:

1. Set a password once (stored only as a salted scrypt hash in
   `web.password_hash`):

   ```bash
   litresearch set-password
   ```

2. Choose the network: e.g. `web.host: wlan0` in `config/config.yaml`,
   `litresearch serve --network wlan0`, or `litresearch serve --choose`.
3. Start it; the server prints the address to open:

   ```
   litresearch web server on wlan0, WLAN
     open http://10.100.7.134:8080/
     only reachable via 10.100.7.134, also from this computer itself
     login required (password set with litresearch set-password)
   ```

4. Open that address on the laptop and log in. The login stays valid for 30
   days (HttpOnly cookie); **Log out** is at the bottom of the sidebar.

Safety rules:

- Without a password the server refuses to listen on the network. `--no-login`
  overrides this, which is not recommended.
- Changing the password (`set-password` again) signs out all devices;
  `set-password --remove` removes it, after which the UI only starts locally.
- A wrong password costs a one-second delay, which slows down guessing.
- The connection is plain HTTP. That is fine in your own home network, but do
  not expose the port to the internet; use a VPN such as Tailscale or
  WireGuard for access from outside.
- To keep it running in the background, start it with `nohup` or a systemd
  user service (planned: deployment files).

The pages also work on a phone browser in the same network; an installable
phone app (PWA) is planned.

## Pages

| Page | What you can do |
|---|---|
| 🏠 **Dashboard** | your up-next paper with a one-click reading log, the weekly quest, hints for what to do next, this week's picks, queue, recent XP |
| ✨ **This week** | the weekly recommendations, ranked by score (you choose the reading depth), with score bars, an **evidence tree** per paper, the filter **funnel** and the settings used; add a paper to your queue or mark it not relevant; build or rebuild the batch; browse earlier weeks |
| 📚 **Reading queue** | the ordered queue (q1 = up next): log a reading (level, star rating, notes), reorder, remove; add papers by DOI, PubMed ID, citation key, title words or title only |
| 📁 **Paperpile** | your library without PDFs (search, folder and read-status filters, queue buttons), last import and notices when it is outdated, upload of a new BibTeX export with checks, re-import, export instructions, import history |
| 🎮 **Profile** | set up your game (all rewards, penalty, levels, badge targets), then see the locked rules; save, download, load, compare profiles; start over |
| 🔍 **Find & add** | search your library and all known papers (title, author, citation key, DOI, folder) and queue them; add any DOI |
| 📄 **Paper** | abstract, TL;DR, metrics, notes (timestamped), status, every recommendation of it with its evidence tree, the XP it earned |
| 🏆 **Progress** | level ring, streak, Reading Impact Factor, discipline and depth, XP per week (12 weeks), reading calendar (26 weeks), personal bests, badge gallery with progress, career ladder |
| 🗓️ **Weekly reports** | one report card per week: grade, XP, papers, points by kind, badges |
| 💰 **XP ledger** | every transaction with running balance, filter by kind, totals, TSV export |
| 🔎 **How it works** | all recommendation criteria with your current values, and the game rules |
| ⚙️ **Settings** | topics, excluded terms and every recommendation, queue and game parameter |

Keyboard shortcuts (outside text fields): `n` go to the queue, `r`
recommendations, `p` progress.

## Rewards and feedback

- An **XP toast** counts up the points of an action and lists every bonus or
  penalty.
- **Confetti** for earned points; more for a completed weekly quest.
- A **pop-up** for level-ups and unlocked badges.
- The up-next paper glows; the streak flame flickers.
- **Penalties** show as a red, shaking toast. A confirmation dialog warns
  before you remove a picked recommendation unread.
- With your system's *reduce motion* setting on, all animations are off.

## Removing papers from the queue

Removing is free for papers you added yourself or from your library. A paper
you picked **from the recommendations** costs the penalty of your game profile
(default 15) if it leaves the queue before you at least skimmed it: removed,
or marked not relevant. The queue shows this as a red chip, and the remove
button names the cost.

## Settings

Saving writes to `config/config.yaml`:

- comments and order are kept;
- the previous version is kept as `config/config.yaml.bak`;
- invalid values (e.g. more than 5 papers, all weights 0) are rejected and
  the file is left unchanged.

Paths, API keys, embeddings, LLM and web settings are only editable in the
file. Fields of a topic that the UI does not show (`mesh_terms`,
`seed_dois`) are kept.
