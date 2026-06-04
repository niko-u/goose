# Goose iOS — install + homelab sync runbook

The Mac side is **fully done**. The remaining work is purely Xcode UI clicks +
your physical iPhone. Lightweight path: rebuild via Xcode every 7 days when
the developer signature expires. Upgrade to the $99/yr Apple Developer Program
later if the weekly ritual gets tedious.

---

## What's already done for you

- ✅ Repo cloned to `/Users/jarvis/code/goose`
- ✅ Rust toolchain installed; iOS targets installed
- ✅ Goose Rust core compiled for iOS device (`Rust/iphoneos/libgoose_core.a`)
- ✅ `GooseSwift/Sync/HomelabSync.swift` — HMAC sync engine + Keychain config
- ✅ `GooseSwift/Sync/HealthDataStore+HomelabSync.swift` — bridge that turns
  the latest score reports into a Goose reading and ships them
- ✅ `GooseSwift/Sync/HomelabSync.plist` — pre-filled with `goose.compute.casa`
   URL + real HMAC secret from `homelab/.env` (gitignored)
- ✅ All three files registered in `GooseSwift.xcodeproj` (pbxproj edited safely
  via `pbxproj` Python lib; backup at `project.pbxproj.bak`)
- ✅ One-line sync call wired into `runPacketScores()` in
  `HealthDataStore+Snapshots.swift`
- ✅ **Full headless build verified**: `xcodebuild` for `iphoneos` SDK compiles
  cleanly, plist ships in the `.app` bundle, sync hook integrates with Goose's
  internal helpers
- ✅ `goose.compute.casa` backend live + HMAC verified end-to-end
- ✅ Training Hub coach reads WHOOP data from Goose when present

## What's left for you (≈ 5 minutes total)

```bash
open /Users/jarvis/code/goose/GooseSwift.xcodeproj
```

### 1. Signing & team (2 minutes)

- Top of Xcode left sidebar: click the **GooseSwift** project icon
- Click the **GooseSwift** target → **Signing & Capabilities** tab
- **Team**: pick your free Apple ID (sign in if Xcode prompts)
- **Bundle Identifier**: change to `casa.compute.goose` (avoids collision with
  upstream `com.b-nnett.goose` and any other dev's free-tier signing)
- Repeat for the **GooseWorkoutLiveActivityExtension** target — same team
  (Xcode auto-suffixes the bundle ID)

### 2. Run on your iPhone (2 minutes)

- Plug iPhone into the Mac Mini via USB, tap **Trust** on the phone
- Xcode top toolbar → device dropdown → pick your iPhone
- Press **⌘R** (Run)
- First time only, on iPhone:
  **Settings → General → VPN & Device Management** → tap your Apple ID under
  "Developer App" → **Trust**
- Tap Goose on the home screen to launch

### 3. Verify (1 minute)

**Mac terminal:**
```bash
docker logs goose --tail 30 | grep ingest
```
Once your WHOOP has paired with Goose and scores compute, you should see
`POST /api/ingest/reading` lines appearing.

**Browser:**
- `https://goose.compute.casa` → recovery ring matches what the phone shows
- `https://training.compute.casa` → ask the coach "should I run hard today?"
  → response references your real WHOOP recovery score

## Weekly refresh (7-day Apple ID cert expiry)

After ~7 days the developer signature expires and tapping the app does
nothing. Fix:
1. Plug phone into Mac
2. Open Xcode (`GooseSwift.xcodeproj` remembers everything)
3. Press ⌘R
4. ~2 min later you're refreshed for another 7 days

Forgetting for a few days is harmless — data stays in the app sandbox, you
just can't open the app until you re-sign.

## Upgrade path (optional)

When the weekly ritual gets old: pay Apple $99/yr at
[developer.apple.com/programs](https://developer.apple.com/programs/). Add the
team to the project in Xcode — apps now last a year per install. Same project,
same code, same homelab integration.

---

## Troubleshooting

- **"Could not launch Goose"** in Xcode → trust the developer profile on
  iPhone (Settings → VPN & Device Management).
- **Sync log silent** → check the bundled plist secret matches `homelab/.env`:
  ```bash
  grep GOOSE_INGEST_SECRET /Users/jarvis/homelab/.env
  cat /Users/jarvis/code/goose/GooseSwift/Sync/HomelabSync.plist
  ```
- **HTTP 403 invalid signature** in `docker logs goose` → secret drift.
  Re-copy from `.env`, rebuild from Xcode.
- **Project won't open / pbxproj broken** → restore the safety backup:
  ```bash
  cp GooseSwift.xcodeproj/project.pbxproj.bak GooseSwift.xcodeproj/project.pbxproj
  ```

## Future custom edits (no rush)

- Hook `syncWorkout(...)` where Goose marks a workout as ended
- Add a Settings row in the More tab to toggle sync on/off (UI work in
  `MoreView.swift`)
- Schedule a background sync task so readings land even when the app isn't
  foregrounded
