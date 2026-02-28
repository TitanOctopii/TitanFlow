# Ghost Publishing — Setup Blockers & Fix Instructions
**Date:** 2026-02-28
**Status:** BLOCKED — 2 hard blockers prevent publishing
**Drafts ready at:** `docs/ghost-post-drafts.md` (8 posts)
**Publisher script:** `scripts/ghost-publish-queue.py`

---

## Blocker 1 — GHOST_ADMIN_KEY_FLOW not in Ollie's launchd plist

### Symptom
`echo $GHOST_ADMIN_KEY_FLOW` returns empty inside Ollie's TitanFlow process.
The ghost_publish plugin falls back to `""` and JWT generation fails.

### Fix — 2 steps

**Step 1: Add the key to Ollie's plist**

```bash
# Edit Ollie's launchd plist
nano ~/Library/LaunchAgents/com.titanarray.titanflow-ollie.plist
```

Find the `<key>EnvironmentVariables</key>` block. Add these two entries inside the `<dict>`:

```xml
<key>GHOST_ADMIN_KEY_FLOW</key>
<string>YOUR_KID_HERE:YOUR_HEX_SECRET_HERE</string>

<key>GHOST_API_URL</key>
<string>https://titanflow.space</string>
```

The full key format is `kid:hex_secret` — copy it from Ghost Admin → Settings → Integrations → Custom Integration → Admin API Key.

**Step 2: Restart Ollie**

```bash
# Get Ollie's current PID
pgrep -f "titanflow-ollie"

# Kill it — launchd KeepAlive will auto-respawn
kill <PID>

# Confirm new PID
sleep 3 && pgrep -f "titanflow-ollie"
```

### Verify the fix

```bash
# Check Ollie can see the key (requires SSH or local terminal on MBA)
launchctl print user/$(id -u)/com.titanarray.titanflow-ollie | grep GHOST
```

---

## Blocker 2 — DNS SERVFAIL for titanflow.space from MBA

### Symptom
```
$ host titanflow.space
;; communications error to [dns-server]: timed out
Host titanflow.space not found: 2(SERVFAIL)

$ curl https://titanflow.space
curl: (6) Could not resolve host: titanflow.space
```

Ghost publish calls fail immediately with `URLError: [Errno 8] nodename nor servname provided`.

### Diagnosis
The domain resolves fine externally but fails from MBA (10.0.0.10).
Likely cause: Technitium / AdGuard / Pihole on TitanStream is intercepting the query and returning SERVFAIL, probably because `titanflow.space` was added to a local override or blocklist.

### Fix — check in order

**Option A: Check Technitium for a broken local override**

Open Technitium DNS admin → Zones → look for `titanflow.space`.
If a local zone exists with a broken/empty A record, delete it or fix it to point to your Ghost server's actual IP.

**Option B: Check blocklists**

```
AdGuard Home → Filters → DNS blocklist → search "titanflow.space"
Pihole → Blacklist → search "titanflow.space"
```

If listed, whitelist it.

**Option C: Temporary workaround — hardcode /etc/hosts on MBA**

Find the public IP of titanflow.space (run this from Sarge or externally):
```bash
dig titanflow.space +short @8.8.8.8
```

Then on MBA:
```bash
sudo nano /etc/hosts
# Add line:
<IP-FROM-DIG>  titanflow.space
```

This bypasses local DNS entirely. Remove the line once DNS is fixed.

**Option D: Force MBA to use upstream DNS temporarily**

```bash
# On MBA, temporarily point to Cloudflare
networksetup -setdnsservers Wi-Fi 1.1.1.1

# Test
curl -I https://titanflow.space

# Restore after done
networksetup -setdnsservers Wi-Fi 10.0.0.1   # or your local DNS IP
```

---

## Publishing — Once Both Blockers Are Fixed

### Option 1: One-shot script (fastest)

```bash
cd /Users/kamaldatta/Projects/TitanFlow

# Dry run first — verify all 8 posts look right
python3 scripts/ghost-publish-queue.py --dry-run

# Publish all 8 posts
GHOST_ADMIN_KEY_FLOW="kid:hexsecret" python3 scripts/ghost-publish-queue.py

# Publish only post 1 for testing
GHOST_ADMIN_KEY_FLOW="kid:hexsecret" python3 scripts/ghost-publish-queue.py --post 1

# Publish as drafts first for review
GHOST_ADMIN_KEY_FLOW="kid:hexsecret" python3 scripts/ghost-publish-queue.py --status draft
```

State is saved to `~/.titanflow/ghost-publish-queue-state.json`.
Re-running the script will skip already-published posts (use `--force` to override).

### Option 2: Via Telegram to Flow

Once `GHOST_ADMIN_KEY_FLOW` is in Flow's YAML and DNS resolves from Sarge:

```
Flow, publish a test post titled "Test" with content "Ghost integration test." and tags "test"
```

Confirm URL returns. Then send each post title from the drafts file.

### Option 3: Via Ollie auto-publisher

Once the key is in Ollie's plist and DNS resolves from MBA, the `ghost-autopublish` background module runs every 60 minutes automatically. No manual action needed — it reads TitanPipeline/logs/ and publishes new entries on schedule.

---

## Ghost Credentials Reference

| Key | Location |
|-----|----------|
| Admin API Key | Ghost Admin → Settings → Integrations → Custom Integration |
| Key format | `kid:hex_secret` (colon-separated) |
| Env var name | `GHOST_ADMIN_KEY_FLOW` |
| API endpoint | `https://titanflow.space/ghost/api/admin/posts/?source=html` |
| JWT TTL | 5 minutes (generated fresh per request) |
| JWT audience | `/admin/` |

---

## State Files

| File | Purpose |
|------|---------|
| `~/.titanflow/ghost-publish-queue-state.json` | Tracks which draft posts have been published (by title) |
| `~/.titanflow/ghost-autopublish-state.json` | Tracks which log file lines have been auto-published |

---

*CC wrote the publisher script. CX wrote this blocker doc.*
*Once fixed: run `scripts/ghost-publish-queue.py` and all 8 posts go live immediately.*
