# Manual test script

Automated tests cover the engine and the interface with fake cards and fake uploaders.
This script covers what needs real hardware, real accounts and an installed build.
Run it on **macOS (Apple silicon and Intel)** and **Windows 10/11** before a release.

Tick each box. Note the build (`Settings` shows the version) and the machine.

**You need:** the installer for the platform; an SD/CF card with 20–50 in-camera JPEGs
(include a few portrait shots and a RAW+JPEG pair); a card reader; throwaway SmugMug
and Flickr accounts with an empty test gallery / album name; a watermark PNG.

## 1. Install and first run
- [ ] Installer opens with a double-click; no Python needed. (Unsigned builds: note the
      Gatekeeper / SmartScreen warning.)
- [ ] First run shows 3 steps: library folder → connect a service → first event.
- [ ] "Skip for now" on step 2 works; the main window opens after step 3.
- [ ] The window is 560×720, shows the event name, "Insert a card", and only three
      controls: Pause, Settings, Switch.

## 2. Connect services (Settings)
- [ ] SmugMug: Connect opens the browser; pasting the 6-digit code shows **Connected**.
- [ ] Flickr: same, with Flickr's code.
- [ ] A wrong code shows a plain error and stays **Not connected**.
- [ ] Disconnect returns to **Not connected**; Reconnect works.
- [ ] "Use my own API key…" reveals the key fields; connecting with your own key works.
- [ ] macOS Keychain Access / Windows Credential Manager shows entries under `WeFlow`.
- [ ] `settings.json` and `events.json` in the app-data folder contain no keys or tokens.

## 3. Real card: copy and "Safe to remove"
- [ ] Insert the card. Within 3 s: sound, notification, and "Card <label> → <event>".
- [ ] After 5 s copying starts by itself. (Repeat once and press **Cancel**: nothing is copied.)
- [ ] Panel is amber, "Copying — keep card in", with a count and progress bar.
- [ ] Panel turns green, "**Safe to remove**", only after the count is complete.
      Readable from 2 m, along with the event name.
- [ ] "Safe to remove" notification appears; tray icon shows the green dot.
- [ ] `originals/` holds one file per JPEG, named `<event>_<date-time>_<stem>.jpg`.
      RAW files and `._` files were not copied.
- [ ] Compare checksums of 3 originals with the card (`shasum -a 256` / `Get-FileHash`).
- [ ] The card is unchanged: same files, same dates (compare a listing before and after).

## 4. Eject
- [ ] Press **Eject**: the card unmounts and the window says so.
- [ ] Open a card file in another app, then Eject: a plain failure message appears and
      the panel still says Safe to remove.

## 5. Pull the card early
- [ ] Insert a card and pull it out mid-copy. Panel turns red: "Don't remove yet — copy
      failed". It never showed "Safe to remove".
- [ ] Re-insert the card: only the missing photos are copied; it then reaches Safe.

## 6. Look, watermark, processing
- [ ] `processed/` photos look right: neutral colour, sensible brightness.
- [ ] Portrait photos are upright. The watermark is the same size and in the same corner
      on portrait and landscape photos.
- [ ] Capture date and camera EXIF survive (check in Preview / Properties).
- [ ] Event editor: with a sample photo, moving any slider updates the preview within
      half a second; the split line drags.
- [ ] Time a 24 MP photo (NFR-03, ≤ 1 s each with one worker; estimate from the count).
- [ ] With 45 MP files, memory stays under 1 GB (Activity Monitor / Task Manager) (NFR-08).

## 7. Uploads
- [ ] SmugMug event with the gallery link: all photos arrive once, with keywords.
- [ ] Flickr event with a new album title: the album is created once; privacy is as set.
- [ ] Insert the same card again: "nothing new", and no duplicates online.
- [ ] Turn Wi-Fi off mid-upload: the panel says it will keep trying. Turn it on: it finishes
      with no duplicates.
- [ ] Quit WeFlow mid-upload and start it again: it resumes and finishes, no duplicates.
- [ ] Force-quit WeFlow mid-upload (Activity Monitor / Task Manager): same result.
- [ ] Hold uploads on: panel shows "Ready for review"; **Release** uploads everything.
- [ ] Wrong gallery link: "Needs attention" with a plain reason. Fix the event, press
      **Retry**: uploads finish.
- [ ] Disconnect the service mid-job: "Needs attention" tells you to reconnect; after
      reconnecting, **Retry** finishes the job.

## 8. Window, tray, notifications
- [ ] Switch events in two clicks; the new name shows at once.
- [ ] Pause: inserting a card does nothing. Resume: the card is announced.
- [ ] Close the window during an upload: WeFlow stays in the menu bar / tray and finishes.
- [ ] Tray menu: Show WeFlow, Pause watching, Quit all work.
- [ ] Notifications arrive for: card detected, safe to remove, all done, needs attention.
- [ ] Switch the OS between light and dark: WeFlow follows without a restart.
- [ ] Tab reaches every control, with a visible focus ring; Space / Enter activates it.
- [ ] Start at login on: WeFlow starts after signing in again. Off: it does not.

## 9. Volume test
- [ ] Import a card with 1 000+ JPEGs: the window never freezes, the count keeps moving,
      and the copy takes no more than about 1.3× a Finder / Explorer copy of the same card (NFR-02).

## 10. Uninstall
- [ ] macOS: dragging the app to the Bin removes it. Windows: uninstall from Settings works.
- [ ] The library and its photos are untouched.
