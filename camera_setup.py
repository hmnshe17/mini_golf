#!/usr/bin/env python3
"""
MiniGolf camera settings - lock exposure / white balance / gain so lighting changes don't
change the image. All settings live in camera_config.json so you can change them later.

  python camera_setup.py --list      # show which settings YOUR camera supports (+ ranges)
  python camera_setup.py             # live preview + adjust with keys, press s to save
  python camera_setup.py --apply     # just apply the saved config (no window)
  python camera_setup.py --headless  # apply config, then print fps / brightness (SSH)

Use the same settings in any other script:
  from camera_setup import open_camera
  cap, cfg = open_camera()           # reads camera_config.json, applies it, returns cv2 capture

Preview keys:
  e / E  exposure  - / +        g / G  gain        - / +
  w / W  white bal - / +        b / B  brightness  - / +      f / F  focus (manual) - / +
  a      toggle auto exposure   s      save to camera_config.json
  q      quit

Tips
  * Exposure is in units of 0.1 ms. For 120 fps the exposure must be < 83 (8.3 ms),
    otherwise the camera silently drops to a lower frame rate. Short exposure needs LIGHT.
  * Aim for: ball bright but NOT clipped (clipped % near 0 on the ball), course mid-gray.
  * power_line_freq = 2 (60 Hz, USA) removes flicker/banding from room lights.
  * Settings reset when the camera is unplugged or the board reboots - open_camera()
    re-applies them every time, so always open the camera through it.
"""
import argparse
import json
import os
import re
import subprocess
import time

os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")  # avoids imshow crash on the Radxa
import cv2
import numpy as np

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_config.json")

DEFAULT_CONFIG = {
    "device": "/dev/video0",
    "width": 640,
    "height": 480,
    "fps": 120,
    "fourcc": "MJPG",
    # Friendly names -> mapped to whatever your camera calls them (see ALIASES).
    # Set a value to null to leave that setting alone.
    "controls": {
        "auto_exposure": 1,          # 1 = manual (locked), 3 = automatic
        "exposure": 80,              # 0.1 ms units; < 83 for 120 fps
        "auto_white_balance": 0,     # 0 = locked, 1 = automatic
        "white_balance": 4600,       # Kelvin
        "gain": 0,
        "brightness": 0,
        "contrast": None,
        "saturation": None,
        "power_line_freq": 2,        # 0 = off, 1 = 50 Hz, 2 = 60 Hz (USA)
        "auto_focus": 0,             # 0 = off (if the camera has autofocus)
        "focus": None,               # manual focus (1..1023 on this camera); tune with f/F
        "dynamic_framerate": 0,      # 0 = never lower fps to get a longer exposure
        "backlight_comp": 0,         # 0 = off (it re-adjusts brightness automatically)
    },
}

# Different cameras / kernels use different names for the same setting.
ALIASES = {
    "auto_exposure": ["auto_exposure", "exposure_auto"],
    "exposure": ["exposure_time_absolute", "exposure_absolute"],
    "auto_white_balance": ["white_balance_automatic", "white_balance_temperature_auto"],
    "white_balance": ["white_balance_temperature"],
    "gain": ["gain"],
    "brightness": ["brightness"],
    "contrast": ["contrast"],
    "saturation": ["saturation"],
    "power_line_freq": ["power_line_frequency"],
    "auto_focus": ["focus_automatic_continuous", "focus_auto"],
    "focus": ["focus_absolute"],
    "dynamic_framerate": ["exposure_dynamic_framerate", "exposure_auto_priority"],
    "backlight_comp": ["backlight_compensation"],
}
# Auto modes must be switched off BEFORE the manual values are set.
APPLY_ORDER = ["auto_exposure", "auto_white_balance", "auto_focus", "dynamic_framerate",
               "backlight_comp", "power_line_freq", "exposure", "white_balance", "focus",
               "gain", "brightness", "contrast", "saturation"]


# ---------------------------------------------------------------- config file
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
        print(f"Created default config: {path}")
    with open(path) as f:
        cfg = json.load(f)
    # fill in anything missing (e.g., after adding new options)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in cfg.items() if k != "controls"})
    merged["controls"].update(cfg.get("controls", {}))
    return merged


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------- v4l2 controls
_LINE = re.compile(r"^\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)$")


def list_controls(device):
    """Return {name: {"type":..., "min":..., "max":..., "value":..., ...}} from v4l2-ctl."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", device, "--list-ctrls"],
                             capture_output=True, text=True, timeout=5).stdout
    except FileNotFoundError:
        raise SystemExit("v4l2-ctl not found. Install it: sudo apt install v4l-utils")
    ctrls = {}
    for line in out.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        name, typ, rest = m.groups()
        info = {"type": typ}
        for key, val in re.findall(r"(\w+)=(-?\w+)", rest):
            info[key] = int(val) if re.fullmatch(r"-?\d+", val) else val
        info["inactive"] = "inactive" in rest
        ctrls[name] = info
    return ctrls


def resolve(friendly, available):
    """Map a friendly name to the camera's real control name (or None)."""
    for real in ALIASES.get(friendly, [friendly]):
        if real in available:
            return real
    return None


def set_control(device, real_name, value):
    r = subprocess.run(["v4l2-ctl", "-d", device, f"--set-ctrl={real_name}={int(value)}"],
                       capture_output=True, text=True, timeout=5)
    return r.returncode == 0, r.stderr.strip()


def apply_controls(cfg, verbose=True):
    """Apply every non-null control in the config. Returns {friendly: real_name} that worked."""
    dev = cfg["device"]
    available = list_controls(dev)
    applied = {}
    for friendly in APPLY_ORDER:
        value = cfg["controls"].get(friendly)
        if value is None:
            continue
        real = resolve(friendly, available)
        if real is None:
            if verbose:
                print(f"  skip {friendly:<18} (camera doesn't have it)")
            continue
        info = available[real]
        if "min" in info and "max" in info:
            value = max(info["min"], min(info["max"], int(value)))
        ok, err = set_control(dev, real, value)
        if ok:
            applied[friendly] = real
        if verbose:
            print(f"  {'ok  ' if ok else 'FAIL'} {friendly:<18} -> {real} = {value}" + ("" if ok else f"  ({err})"))
    return applied


# ---------------------------------------------------------------- open camera
def open_camera(cfg=None, config_path=CONFIG_PATH, verbose=False):
    """Open the camera with the saved settings. Use this in every MiniGolf script."""
    cfg = cfg or load_config(config_path)
    dev = cfg["device"]
    cap = cv2.VideoCapture(int(dev) if str(dev).isdigit() else dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {dev}. Close guvcview; check: v4l2-ctl --list-devices")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg["fourcc"]))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
    cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.read()  # start streaming first: some cameras reset controls when the stream starts
    apply_controls(cfg, verbose=verbose)
    return cap, cfg


# ---------------------------------------------------------------- preview
def image_stats(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()), float((gray >= 250).mean() * 100)  # mean brightness, % clipped


def adjust(cfg, friendly, delta):
    dev = cfg["device"]
    available = list_controls(dev)
    real = resolve(friendly, available)
    if real is None:
        print(f"{friendly}: not supported by this camera")
        return
    info = available[real]
    cur = cfg["controls"].get(friendly)
    cur = info.get("value", 0) if cur is None else cur
    new = int(cur + delta)
    if "min" in info and "max" in info:
        new = max(info["min"], min(info["max"], new))
    ok, err = set_control(dev, real, new)
    if ok:
        cfg["controls"][friendly] = new
        print(f"{friendly} = {new}")
    else:
        print(f"{friendly}: could not set ({err}) - is the matching auto mode still on?")


def preview(cfg, headless=False, seconds=0):
    cap, cfg = open_camera(cfg, verbose=True)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Streaming {w}x{h}, requested {cfg['fps']} fps")
    steps = {"exposure": 5, "gain": 5, "white_balance": 100, "brightness": 4, "focus": 10}
    n, t0, t_print, fps = 0, time.perf_counter(), time.perf_counter(), 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("no frame")
                break
            n += 1
            now = time.perf_counter()
            if now - t_print >= 1.0:
                fps = n / (now - t0)
                mean, clipped = image_stats(frame)
                c = cfg["controls"]
                print(f"fps {fps:5.1f} | brightness {mean:5.1f} | clipped {clipped:4.1f}% | "
                      f"exp {c.get('exposure')} gain {c.get('gain')} wb {c.get('white_balance')}")
                n, t0, t_print = 0, now, now
            if headless:
                if seconds and now - t0 >= seconds:
                    break
                continue

            view = frame.copy()
            view[cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) >= 250] = (255, 0, 255)  # clipped = magenta
            c = cfg["controls"]
            lines = [f"fps {fps:.0f}  (magenta = overexposed)",
                     f"exposure {c.get('exposure')} [e/E]   gain {c.get('gain')} [g/G]",
                     f"white bal {c.get('white_balance')} [w/W]   brightness {c.get('brightness')} [b/B]",
                     f"focus {c.get('focus')} [f/F]   auto exp {'ON' if c.get('auto_exposure') == 3 else 'off'} [a]",
                     "s=save  q=quit"]
            for i, txt in enumerate(lines):
                cv2.putText(view, txt, (10, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
                cv2.putText(view, txt, (10, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
            cv2.imshow("camera setup", view)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            keymap = {"e": ("exposure", -1), "E": ("exposure", 1), "g": ("gain", -1), "G": ("gain", 1),
                      "w": ("white_balance", -1), "W": ("white_balance", 1),
                      "b": ("brightness", -1), "B": ("brightness", 1),
                      "f": ("focus", -1), "F": ("focus", 1)}
            ch = chr(key) if key < 128 else ""
            if ch in keymap:
                name, sign = keymap[ch]
                adjust(cfg, name, sign * steps[name])
            elif ch == "a":
                cfg["controls"]["auto_exposure"] = 3 if cfg["controls"].get("auto_exposure") == 1 else 1
                apply_controls(cfg, verbose=False)
                print("auto exposure", "ON" if cfg["controls"]["auto_exposure"] == 3 else "off (locked)")
            elif ch == "s":
                save_config(cfg)
                print(f"Saved -> {CONFIG_PATH}")
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main():
    p = argparse.ArgumentParser(description="Lock and tune camera settings")
    p.add_argument("--list", action="store_true", help="show supported settings and ranges")
    p.add_argument("--apply", action="store_true", help="apply saved config and exit")
    p.add_argument("--headless", action="store_true", help="no window; print stats")
    p.add_argument("--seconds", type=float, default=10, help="headless run time")
    p.add_argument("--device", help="override device, e.g. /dev/video2 (saved on 's')")
    args = p.parse_args()

    cfg = load_config()
    if args.device:
        cfg["device"] = args.device

    if args.list:
        ctrls = list_controls(cfg["device"])
        if not ctrls:
            print(f"No controls found on {cfg['device']}. Check: v4l2-ctl --list-devices")
            return
        print(f"Controls on {cfg['device']}:")
        for name, info in ctrls.items():
            rng = f"[{info.get('min')}..{info.get('max')}]" if "min" in info else ""
            friendly = next((f for f, al in ALIASES.items() if name in al), "")
            print(f"  {name:<34}{rng:<16} now={info.get('value')!s:<6}"
                  f"{' (inactive)' if info['inactive'] else ''}  {('<- ' + friendly) if friendly else ''}")
        return
    if args.apply:
        apply_controls(cfg)
        return
    preview(cfg, headless=args.headless, seconds=args.seconds)


if __name__ == "__main__":
    main()
