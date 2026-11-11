# WeFlow

**Card in, photos out.** WeFlow is a small desktop app for event photographers on
macOS and Windows. Put a memory card in, and it:

1. copies every JPEG and checks each copy against the card,
2. tells you when it is **Safe to remove**, so the card can go back in the camera,
3. fixes white balance and tone, and adds your watermark if you want one,
4. uploads the finished photos to SmugMug or Flickr.

You make every choice once, before the shoot, by setting up an *event*.
After that, inserting a card asks nothing.

## Install

- **Mac with Apple silicon, macOS 13 or later:** open `WeFlow-<version>-arm64.dmg` and drag
  WeFlow to Applications. Intel Macs are not supported.
- **Windows 10 or 11:** run `WeFlow-Setup-<version>.exe`.

No Python or other software is needed.

Installers are at <https://github.com/TheOrangeStuff/weflow-releases/releases/latest>.

## First start

WeFlow asks three things, once:

1. **Where to keep photos.** This is your *library*. Each event gets its own folder there.
2. **Which service to connect** (SmugMug or Flickr). You can skip this and do it later in Settings.
   You need your own API key from that service; the **Get an API key…** link opens the page.
3. **Your first event:** a name, and where its photos should go.

## At the shoot

The main window shows one thing at a time. From across the room you can read which
event is active and whether the card can come out.

| You see | It means |
|---|---|
| **Insert a card** | WeFlow is waiting. The line below names the event photos will go to. |
| **Copying — keep card in** | Photos are being copied and checked. Leave the card in. |
| **Safe to remove** (green) | Every photo is copied and verified. Take the card out, or press **Eject**. |
| **Processing / Uploading** | The look is being applied and photos are going up. Keep shooting. |
| **Ready for review** | Uploads are on hold for this event. Check the folder, then press **Release**. |
| **All done** | Everything from that card is finished. |
| **Needs attention** (red) | Something failed. The line below says what to do; then press **Retry**. |
| **Don't remove yet — copy failed** | The copy did not finish. Put the card back in to try again. |

Good to know:

- When a card goes in, WeFlow shows "Card *name* → *event*" for 5 seconds. Press **Cancel**
  if it is the wrong card or the wrong event.
- A card you insert twice is not copied twice. Photos already imported are skipped.
- Bad Wi-Fi is fine. WeFlow keeps trying, and picks up where it left off after a restart.
- WeFlow never changes or deletes anything on your card.
- **Pause** stops WeFlow from reacting to cards. Work already started carries on.
- Closing the window while work is running keeps WeFlow going in the menu bar or tray.

## Events

Press **Switch** on the event card to change events, create one, or edit the current one.
A new event starts as a copy of the current one.

- **Destination:** this computer only, SmugMug (paste the gallery link) or Flickr (type an
  album title; it is created if it does not exist). Turn on **Hold uploads for review** to
  look through the photos before they go up.
- **Look:** auto white balance (with a strength slider) and auto tone.
- **Watermark:** a PNG file, its corner, size and opacity.
- **More:** JPEG quality, resize, watermark margin.

Choose a sample photo to see a live before/after preview; drag the line to compare.

**Import folder…** (also under **Switch**) runs the same steps on any folder of JPEGs.

## Where your photos are

```
<Library>/<event>/originals/    exact, verified copies from the card (renamed by date)
<Library>/<event>/processed/    finished photos: look applied, watermark added
```

Deleting an event in WeFlow never deletes its photos.

## Settings

- **Library folder.**
- **SmugMug / Flickr:** paste your own API key and secret, press **Connect**, approve
  WeFlow in your browser, and paste the code it shows. The key and sign-in details are kept
  in the macOS Keychain or Windows Credential Manager, never in plain files, and they stay
  there when you install a newer WeFlow.
- **Card-detected sound** and **Start at login**.
- **Check for new versions:** when a newer WeFlow is out, a bar offers **Download**, which
  opens the download page. **Skip** hides that version; Settings still links to it.
  WeFlow never installs anything by itself.
- After an update, WeFlow shows **What's new** once.

## Good to know

- WeFlow works with JPEGs. RAW files on the card are left alone and not copied.
- It uploads to one service per event.
- Logs are in the app's data folder under `logs/` if support ever asks for them
  (macOS: `~/Library/Application Support/WeFlow`, Windows: `%APPDATA%\WeFlow`).

---

## For developers

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy --strict WeFlow/core
QT_QPA_PLATFORM=offscreen pytest -q
python -m WeFlow

# simulate a card without hardware
python tests/fixtures/make_card.py tests/fixtures/volumes/CARD_A --count 25
WeFlow_FAKE_VOLUMES=tests/fixtures/volumes python -m WeFlow
```

- `SPEC.md` is the build specification; `DECISIONS.md` records every deviation from it.
- `WeFlow/core` is the engine and never imports Qt. The UI reaches it only through
  `WeFlow/ui/bridge.py`.
- Tests render every main-window state to `tests/artifacts/*.png` for review.
- `packaging/README.md` covers installers, signing and API keys;
  `docs/manual-test.md` is the checklist for real cards and real accounts.
- Icons are from [Lucide](https://lucide.dev) (ISC licence, see
  `WeFlow/assets/icons/LUCIDE-LICENSE.txt`).
