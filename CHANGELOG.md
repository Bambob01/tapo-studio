# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] - 2026-11-04

### Added
- **Vocals in Smart music.** A new meter and a "Vocals only" pulse option that follows the singing. It reacts to melody, so a lead guitar or synth can trigger it too.
- **Better drum detection.** Bass notes, kick clicks and hi-hats no longer set off the wrong trigger as often, and tempo finding now uses all the drums.
- **Fast bass.** A separate fine-grained kick detector follows double kicks, and real hits now always count alongside predicted beats. Predicted beats stop when the music does.
- **Custom effects on newer firmware.** If a light ignores a custom effect, it is resent as the closest scene animation, or as still colours with a warning.

### Notes
- Music detection was tested on simulated music. The firmware fix has not been tried on a real light yet.

## [0.1.0] - 2026-11-04

First public release (alpha).

### Added
- **Painting on newer firmware.** Strips on firmware 1.4.x (seen on an L930-5, 1.4.3) accept the usual painting request but never apply it. If the read-back shows that, the app now switches to the scene (segment) request, which that firmware does apply (type `none`, one colour per zone, at most 4 display colours), and remembers it. Live modes and brightness changes use it too.
- **Fix painting** (Settings → Troubleshooting): a guided test that tries each way of painting, reports what the light itself says and any error it returns, and lets you confirm the red/blue test pattern.
- Local web control panel for Tapo L930 and L920 light strips: colour, white, per-zone painting, gradients (Oklab), palettes (including from a photo).
- Effect library and editor: all 17 TP-Link effects (editable), 14 extra effects, 55 built-in scene animations, custom scene animations, playlists, schedules, saved scenes.
- Live modes: smart music (kick/snare/hat detection, auto-gain, tempo lock with beat prediction, built-in demo track), screen sync, PC animations, daylight white.
- Basic support for other Tapo lights: L900, L430/L530/L535/L630 (colour, white, brightness) and L510/L520/L610 (brightness). Features a light can't do are hidden automatically.
- Demo mode (`--mock`) with simulated lights.
- Security: loopback-only server, per-launch key delivered in the URL fragment, Host/Origin/Sec-Fetch checks, strict Content-Security-Policy, DPAPI-encrypted password, login-free network search, hardware-ID pinning, input validation.
- Windows launchers (`start.bat`, `start-demo.bat`, `install-extras.bat`).
- Test suite, CI for Windows and Linux on Python 3.11-3.13.

### Added
- After painting or playing an effect, the app reads back whether the light is showing *that* effect (its id and colours, not just "some effect is on"). If not, it retries with a fresh effect id, then clears whatever is running first, remembers what worked for that light, and explains when a scene animation or phone music sync is overriding it.
- Settings → Troubleshooting → Run diagnostics: a copyable report (no passwords, hardware IDs or full IP addresses) with a red/blue test pattern.

### Fixed
- Custom scene animations sent a zone list that did not match their colours, which the light rejects. They now use one band per colour.
- A request the light refused used to show as "Something went wrong". The light's own error is now shown.
- A light could be refused with "hardware ID changed" right after being added, because the ID from the network search was compared with the ID the light reports after login. Each source is now only compared with itself.
- A light selection remembered by the browser from an earlier run (for example the demo lights) no longer hides the Paint zones and Effects tabs or makes commands fail with "No strips selected".

### Notes
- Tested with simulated devices only. Real-hardware reports are welcome.
