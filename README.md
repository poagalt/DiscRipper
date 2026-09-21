# DiscRipper

Archival-grade optical disc ripper for Windows. One tool for **audio CDs, DVDs,
Blu-rays (incl. UHD, where your drive and MakeMKV key support it), and data
discs**, from any drive Windows can see — internal, USB-A, or USB-C. Defaults to
full quality, and keeps reading discs that other tools give up on.

| Disc | Default output | Why it's the quality/size optimum |
|---|---|---|
| Audio CD | FLAC (level 8) | Lossless, ~50–60 % of WAV, bit-identical, AccurateRip-verified |
| DVD / Blu-ray | MKV remux (MakeMKV) | The original video/audio bitstreams copied out untouched — zero generation loss; only menus and disc padding dropped |
| Data disc | Raw ISO | Bit-exact image, hashed while reading |

An optional compression stage (AV1/x265/x264, CPU or GPU) exists for when you
want a viewing copy instead of an archive.

> **Status: pre-1.0.** The rip and salvage paths are in daily use against a real
> library. Settings keys and CLI flags may still change before 1.0. There is no
> support promise — issues are read when they're read.

---

## Quick start

1. **Python 3.11+.**

2. **Install the ripping engines** (asks before each one):

   ```
   rip.cmd setup
   ```

   [MakeMKV](https://www.makemkv.com) (DVD/BD decryption + remux),
   [cyanrip](https://github.com/cyanreg/cyanrip) (audio CDs, AccurateRip +
   MusicBrainz), [ffmpeg](https://ffmpeg.org) (compression/remux/verify), and
   optionally HandBrakeCLI.

3. **Insert a disc and pick a front end.** A window:

   ```
   gui.cmd
   ```

   …or the text wizard, which exposes exactly the same options:

   ```
   rip.cmd
   ```

   Either detects the disc type and walks you through it. `rip.cmd rip` skips
   straight to a rip with your saved defaults.

   The window opens with no terminal behind it and never puts one on screen: it
   re-executes under `pythonw` and spawns every engine call with
   `CREATE_NO_WINDOW`, so launching it from a shell hands your prompt back and
   nothing flashes black windows at you mid-rip.

4. **Check your setup any time:** `rip.cmd doctor`.

> The GUI needs `tkinter`, which a normal Python install includes. The
> *embeddable* Python that `rip.cmd bundle` downloads deliberately omits it, so
> on a bundled-only machine use the wizard — nothing is missing from it.

---

## Two modes, one decision

Pick one on the **Rip tab**, or `rip.cmd mode portable`. Both write real
settings, so the tabs afterwards say what will actually happen and stay
editable. Change anything by hand and the mode reads **Custom**, which is not a
complaint.

| | **Archival** *(default)* | **Portable** |
|---|---|---|
| what it does | copies the disc's streams bit for bit | re-encodes to AV1 on the GPU |
| video | untouched MPEG-2 / H.264 | `av1_nvenc`, borders trimmed |
| DVD | ~6 GB | ~1.8 GB, in 1.5 minutes |
| Blu-ray | 25–40 GB | ~5–8 GB *(estimated)* |
| languages | every one on the disc | English audio + subs only |
| extras | SHA-256 + `.ripinfo.json` sidecar | just the video file |
| lossless copy | kept | made, then deleted |

**Portable's settings are measured, not chosen.** VMAF against the lossless
source, CRF 18, 30-second samples, preset slow, on real discs: `-tune grain`
wins on every kind of content — including animation, because a DVD's own MPEG-2
noise is what is actually in the picture — and it beats spending the same bytes
on a lower CRF. Content decides how close you get, so the mode says so when you
pick it: animation comes out near-indistinguishable, live action lands a few
points short, and grainy live action costs far more space than clean CG at the
same setting.

Blu-rays get their own numbers, because they are eight times the pixels:
`video.hd_quality` and `video.hd_preset` replace `quality` and `preset` for any
source taller than 576 lines, classified by the source's height rather than by
the disc.

Audio is often the bigger lever. On one measured encode: 561 MiB of video
against **1,158 MiB of audio** — the French and Spanish 5.1 tracks alone were
790 MiB, more than the entire video stream. That is why Portable keeps English
only.

---

## When a disc is damaged

Library discs are scratched, and an ordinary rip that fails throws away
everything it read. Measured on one disc: five attempts, ~14.5 GB read and
deleted each time, six hours for nothing.

`rip.cmd salvage` — or **Rip this disc → Image it and rebuild the film** in the
window — is the answer. Every step is resumable, and cheapest runs first.

**The keys come first.** Blu-ray content is AACS-encrypted and deriving the keys
needs the volume ID, which the drive reads from a physical region no sector copy
contains — so an image of a Blu-ray cannot be decrypted on its own. A
non-decrypted backup, stopped the moment it writes `discatt.dat`, captures it in
about a minute. From then on every retry can happen with no disc in the drive.

**Then it reads the film, and by default only the film.** A Blu-ray is around
39 GB of which the feature is 27; the rest is menus, trailers, deleted scenes
and eight dubs of a coming-soon reel. Which sectors are the film is a question
the disc answers itself, so it costs seconds to work out and saves about 30 % of
the reading — and on a struggling drive, 30 % less chance of losing the disc
before the film is off. `--whole` reads everything.

Picking the right playlist is the hard part: a retail disc ships hundreds, and
on one disc the two *longest* claim fifteen hours each, out of 900 play items
all pointing at the same 190 MB clip. Picking the longest picks a decoy. What
catches them is arithmetic — no Blu-ray was authored at 3.5 KB/s — so any
playlist claiming a runtime impossible for the bytes it references is dropped.

**The read keeps everything it gets.** A failed block is recorded and the sweep
moves on; failures are re-read so one dead sector doesn't cost the whole block;
a dead region is crossed by probing ahead rather than plodding through, taking a
contiguous dead region from twenty-two hours to about four minutes. A band that
would cost more than it is worth is recorded as **skipped, not unreadable**,
because nothing measured those sectors individually — `--retry-skipped` picks
them up later, or on another drive.

**A lost drive is waited out, not obeyed.** Some damage takes the drive off the
bus entirely, and a run used to just end there — one loss at 25.1 GB once wrote
off 16.5 GB of perfectly readable film as "never tried". Now the imager waits
for the drive to come back, remembers the exact range that killed it, resumes
with a 32 MB berth around it rather than stepping on the same block again, and
holds the speed cap for the rest of the run.

**Then it checks whether the drive was telling the truth.** A drive that has
given up on a region does not always say so — it hands back its own read buffer,
with no error, and a naive imager records that as recovered. Measured on one
band: 86.7 % of the sectors were duplicates of each other, every one marked as a
clean read. DiscRipper counts the duplicate rate and calls it what it is.

---

## Settings

**Every setting is on the Settings tabs in the GUI**, each with a description,
and the wizard exposes the same ones. You never have to edit a config file.

If you'd rather read them all in one place, [`config.example.toml`](config.example.toml)
lists all 201 with the same descriptions. It is generated from the engine, so it
cannot drift. Copy it to `config.toml`, or just run the app — it writes one from
the same defaults on first start.

`portable.txt` in this folder keeps config and logs *inside* the folder, so the
whole directory can be copied to another machine and still work. Delete it to
use `%APPDATA%\DiscRipper` instead.

---

## The MakeMKV key

MakeMKV is free while in beta but wants a key. Grab the current beta key from
the pinned post at `forum.makemkv.com/forum/viewtopic.php?t=1053` and enter it
in the MakeMKV GUI (Help → Register), or run `rip.cmd key`. `makemkvcon`, which
DiscRipper drives, uses the same key.

| Setup | DVD | Blu-ray | Needs network? |
|---|---|---|---|
| Pinned MakeMKV, no key | ✅ forever | trial period only | no |
| Pinned MakeMKV + free beta key | ✅ forever | ✅ until the key lapses (~2 months) | to fetch each new key |
| **Pinned MakeMKV + purchased key** | ✅ forever | ✅ **forever** | **no — validated locally** |

Pinning freezes the binary, not the key: the key is validated inside MakeMKV's
own code against an expiry date carried in the key itself. The bottom row is the
genuinely self-sufficient configuration. DVDs never need a key at all.

---

## Troubleshooting

- **Drive not detected** — `rip.cmd doctor`. On USB, try a direct port; many
  bus-powered USB-C hubs can't supply enough power for a Blu-ray drive.
- **Blu-ray won't scan** — new releases sometimes need the latest MakeMKV; check
  the key is registered. UHD discs additionally need a UHD-friendly drive.
- **Video rip stalls near the end, then "title failed"** — almost always a
  physical read error; the outer edge of the disc holds the last data and takes
  the most handling damage. Clean the disc (wipe inner-to-outer, never in
  circles) → retry → salvage. A reproducible hard failure at a fixed offset is
  *not* proof of permanent damage: clean it before concluding anything.
- **`READ OF SCRAMBLED SECTOR WITHOUT AUTHENTICATION`** — a CSS authentication
  failure, not a scratch, and usually a drive-region mismatch. Set the region in
  Device Manager → DVD/CD-ROM drives → Properties → DVD Region. Windows permits
  five changes *ever*, then locks the drive permanently.
- **Slow audio rip** — that's the secure read on a scratched disc doing its job.
  Lower `audio.retries` for speed, clean the disc for quality.
- **Desktop popups never appear** — `rip.cmd alerts --test-notify`. Windows
  silently discards toasts from unregistered applications; if they're still
  suppressed, set `general.notify_style = messagebox`.
- **The GUI won't start** — `discripper.py gui` reports why. Almost always a
  Python without `tkinter`; use the wizard, which has every option.

---

## Legal

DiscRipper is a tool for making personal archival copies of discs you own.

**It contains no decryption code.** CSS, AACS and BD+ are handled entirely by
[MakeMKV](https://www.makemkv.com), a separate third-party application you
install yourself and which is governed by its own licence. DiscRipper
orchestrates MakeMKV, ffmpeg and cyanrip; it does not circumvent anything on its
own behalf.

Laws on copying media you own differ by country, and in some of them they differ
from what seems reasonable. Check your own jurisdiction. You are responsible for
how you use this.

## Licence

[MIT](LICENSE). Provided as is, with no warranty — which is worth reading
literally for software that drives a laser at discs you may not be able to
replace.
