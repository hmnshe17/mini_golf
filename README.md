# mini_golf
Camera-guided putting platform with automatic two-axis# Mini Golf: Radxa Setup

## 1. Check the MAC address
```bash
ip link show wlan0
```
The MAC address is the value after `link/ether`.

## 2. Connect to Wi-Fi (AirPennNet-Device)
```bash
sudo nmcli connection up "AirPennNet-Device"
```

## 3. Use the Python environment (mg)
```bash
source ~/mg/bin/activate
```
## 4. Camera settings
Camera settings (exposure, white balance, focus, etc.) are locked so lighting changes don't alter the image. They are saved in `camera_config.json`. Close guvcview first.

```bash
python camera_setup.py --list    # show supported settings and ranges
python camera_setup.py           # live preview to tune settings
python camera_setup.py --apply   # apply saved settings without a window
```

Preview keys:
| Key | Setting | Tip |
|---|---|---|
| `f` / `F` | Focus | Set first, sharpest at ~30 cm |
| `e` / `E` | Exposure | Keep at or below 83 for 120 fps |
| `g` / `G` | Gain | Only if the image is too dark |
| `w` / `W` | White balance | Make the white ball look neutral |
| `b` / `B` | Brightness | Fine adjustment |
| `s` | Save to `camera_config.json` | |
| `q` | Quit | |

Magenta pixels mean overexposed. Keep the ball bright but not magenta.

Settings reset when the camera is unplugged or the board reboots. Open the camera in other scripts with `from camera_setup import open_camera` so the saved settings are applied every time.
