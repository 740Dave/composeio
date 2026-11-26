# MinnowBoard VA-API H.264 hardware decode — provisioning spec

> **For the fleet buildout / a provisioning agent.** Enables VA-API hardware H.264 *decode* on the Intel
> HD Graphics (Gen7, Bay Trail E38xx) of a MinnowBoard MAX/Turbot running Debian 13, usable headless via the
> DRM render node. This is the fleet **capability**; the ComposeUX media widget's opt-in to *use* it is a
> separate code change (see §7). Author: composeux-architect, 2026-07-18. Status: SPEC — verify §5 on first apply.

## 0. The one correctness note that trips everyone

MinnowBoard MAX/Turbot is **Bay Trail (Gen7)**. The VA-API driver for Gen7 is the **legacy `i965`**
(`i965-va-driver`), **NOT** `intel-media-va-driver` (the `iHD` driver). iHD only supports Broadwell/Gen8+ and
will silently fail to drive Bay Trail — the classic "`vainfo` shows nothing" symptom. Since the whole
MinnowBoard family is Bay Trail, `i965` is correct for the entire fleet. (Forward-compat: a future Gen8+ Intel
unit would want `iHD` + `LIBVA_DRIVER_NAME=iHD` — so a good playbook keys the driver off the detected GPU
generation, not a hardcoded `i965`.)

## 1. Packages (apt — idempotent)

```bash
sudo apt-get update
sudo apt-get install -y \
  libva2 libva-drm2 \        # VA-API runtime + DRM (headless) backend — no X needed
  i965-va-driver \           # THE driver for Bay Trail/Gen7 (i965_drv_video.so). NOT intel-media-va-driver.
  vainfo \                   # diagnostics / the acceptance check
  ffmpeg \                   # Debian's ffmpeg is built --enable-vaapi (CLI + libav* with the vaapi hwaccel)
  libavcodec-dev libavformat-dev libavdevice-dev \
  libavutil-dev libavfilter-dev libswscale-dev libswresample-dev \
  pkg-config gcc python3-dev   # to build PyAV against the SYSTEM (vaapi-enabled) ffmpeg (§4)
```

## 2. Render-node permission

VA-API decode uses `/dev/dri/renderD128`; the ComposeUX runtime user must be in the `render` group
(add `video` for safety):

```bash
sudo usermod -aG render,video <composeux-service-user>
# re-login / restart the service so the new group membership takes effect
```

## 3. Pin the driver (fleet robustness)

If both drivers are ever installed, libva autodetect can pick iHD and break Bay Trail. Pin it:

```bash
echo 'LIBVA_DRIVER_NAME=i965' | sudo tee /etc/environment.d/50-vaapi.conf
# or set Environment=LIBVA_DRIVER_NAME=i965 in the ComposeUX service unit
```

## 4. PyAV must link the SYSTEM ffmpeg (the crux)

The pip PyAV **wheel bundles its own ffmpeg with no VA-API codecs** — this is exactly why the first probe on
benchnode.local reported `vaapi codecs: NONE`. Rebuild PyAV from source so it links Debian's vaapi-enabled
libav*:

```bash
pip uninstall -y av
pip install av --no-binary av      # compiles against system libav* (which have vaapi)
```

(Alternative: shell out to the system `ffmpeg` CLI for decode. PyAV-from-source keeps the widget's Python
decode path unchanged, so it's preferred.)

## 5. Acceptance test — REQUIRED (this is the machine-green gate)

```bash
vainfo 2>&1 | grep -E 'Driver version|H264'     # expect: "Intel i965 driver" + VAProfileH264*/VAEntrypointVLD
ffmpeg -hwaccels 2>/dev/null | grep -q vaapi && echo "ffmpeg vaapi: OK"
# a real HW decode that must NOT fall back to software:
ffmpeg -hwaccel vaapi -hwaccel_device /dev/dri/renderD128 -i sample.mp4 -f null - 2>&1 | tail -3
python3 -c "import av; print('PyAV', av.__version__)"
```

**PASS = `vainfo` lists `VAProfileH264Main : VAEntrypointVLD` (and `…High`).** If it doesn't, the culprit is
the render-node permission (§2), group membership not yet active, or the i965 driver not loading — not the
package set.

## 6. Caveats (honest)

- **Not yet verified on hardware.** The initial probe *diagnosed* the gap (no libva, no vainfo, pip-PyAV lacks
  vaapi); no one has installed/confirmed the fix. §5 is the real proof — the executing agent must run it and
  confirm `vainfo` before declaring success.
- **Bay Trail = H.264 decode only.** HEVC/H.265 and hardware *encode* are not accelerated on this silicon.
  Decode-only, which is the goal.
- **`firmware-misc-nonfree`** is usually unnecessary for Bay Trail VA-API, but a cheap belt-and-suspenders add
  if `i915` complains in `dmesg`.

## 7. What provisioning does NOT finish — the widget opt-in

This gives **the system** (ffmpeg CLI + PyAV) the HW-decode *capability*. The ComposeUX **media widget still
decodes in software** until its PyAV decoder requests the `vaapi` hwaccel and `hwdownload`s frames — a code
change in `python/composeio/render/widgets/media/pyav_support.py` (the `hwaccel` opt-in, software-fallback-safe).
That opt-in is tracked/implemented on the composeio side; provisioning a board with this spec is necessary but
not sufficient for the *widget* to hardware-decode. Provision the fleet with §1–§5; the widget opt-in rides
along in the composeio media package.
