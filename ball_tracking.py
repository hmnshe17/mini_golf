import argparse
import math
import time
from collections import deque

import cv2

# to run the video tracking script, use:
# .\.venv\Scripts\python.exe ball_tracking.py --video output.avi


def nothing(_):
    pass


parser = argparse.ArgumentParser(description="Track a ball in video or camera frames")
parser.add_argument("--video", help="Video file path; omit to use a camera")
parser.add_argument("--camera", type=int, default=0, help="Camera index")
parser.add_argument(
    "--dark", action="store_true", help="Track a dark ball on a light background"
)
args = parser.parse_args()

is_video = args.video is not None
source = args.video if is_video else args.camera
cap = cv2.VideoCapture(source)

if not cap.isOpened():
    raise SystemExit(f"Could not open source: {source}")

fps = cap.get(cv2.CAP_PROP_FPS)
if is_video and (not math.isfinite(fps) or fps <= 0):
    cap.release()
    raise SystemExit("Video FPS unavailable; check the video file.")

cv2.namedWindow("Controls", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Controls", 420, 150)
cv2.moveWindow("Controls", 20, 20)

cv2.namedWindow("Ball tracking", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Ball tracking", 600, 450)
cv2.moveWindow("Ball tracking", 20, 240)

cv2.namedWindow("Detection mask", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Detection mask", 600, 450)
cv2.moveWindow("Detection mask", 640, 240)

cv2.createTrackbar("Threshold", "Controls", 20, 255, nothing)
cv2.createTrackbar("Min radius", "Controls", 5, 150, nothing)
cv2.createTrackbar("Max radius", "Controls", 100, 300, nothing)
cv2.createTrackbar("Dark ball", "Controls", int(args.dark), 1, nothing)

trail = deque(maxlen=80)
previous_position = None
previous_time = None
filtered_velocity = None

frame_number = 0
paused = False
last_frame = None

try:
    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                if is_video:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_number = 0
                    previous_position = None
                    previous_time = None
                    filtered_velocity = None
                    trail.clear()
                    continue

                print("Camera frame unavailable.")
                break

            last_frame = frame

            # Video time is based on recorded frame rate, not playback speed.
            # Live camera time uses a monotonic clock.
            timestamp = (
                frame_number / fps if is_video else time.perf_counter()
            )
            frame_number += 1

        frame = last_frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        threshold = cv2.getTrackbarPos("Threshold", "Controls")
        min_radius = cv2.getTrackbarPos("Min radius", "Controls")
        max_radius = cv2.getTrackbarPos("Max radius", "Controls")
        dark_ball = cv2.getTrackbarPos("Dark ball", "Controls")

        threshold_type = (
            cv2.THRESH_BINARY_INV if dark_ball else cv2.THRESH_BINARY
        )
        _, mask = cv2.threshold(gray, threshold, 255, threshold_type)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        candidates = []
        height, width = gray.shape

        for contour in contours:
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)

            if area <= 0 or perimeter <= 0:
                continue

            (x, y), radius = cv2.minEnclosingCircle(contour)
            circularity = 4 * math.pi * area / (perimeter * perimeter)
            fill_ratio = area / (math.pi * radius * radius)

            bx, by, bw, bh = cv2.boundingRect(contour)

            # Reject the background and objects touching the image boundary.
            touches_edge = (
                bx <= 0 or by <= 0
                or bx + bw >= width or by + bh >= height
            )

            if (
                min_radius <= radius <= max_radius
                and circularity >= 0.65
                and fill_ratio >= 0.65
                and not touches_edge
            ):
                candidates.append((x, y, radius, area))

        ball = None

        if candidates:
            if previous_position is None:
                ball = max(candidates, key=lambda item: item[3])
            else:
                # Prefer continuity over jumping to another circular object.
                ball = min(
                    candidates,
                    key=lambda item: math.hypot(
                        item[0] - previous_position[0],
                        item[1] - previous_position[1],
                    ),
                )

        if ball is not None:
            x, y, radius, _ = ball

            if not paused:
                if previous_position is not None and previous_time is not None:
                    dt = timestamp - previous_time

                    if dt > 0:
                        vx = (x - previous_position[0]) / dt
                        vy = (y - previous_position[1]) / dt

                        # Exponential smoothing reduces velocity noise.
                        if filtered_velocity is None:
                            filtered_velocity = (vx, vy)
                        else:
                            alpha = 0.3
                            filtered_velocity = (
                                alpha * vx
                                + (1 - alpha) * filtered_velocity[0],
                                alpha * vy
                                + (1 - alpha) * filtered_velocity[1],
                            )

                previous_position = (x, y)
                previous_time = timestamp
                trail.append((round(x), round(y)))

            center = (round(x), round(y))
            cv2.circle(frame, center, round(radius), (0, 255, 0), 2)
            cv2.circle(frame, center, 3, (0, 0, 255), -1)

            status = f"Position: x={x:.1f}, y={y:.1f} px"

            if filtered_velocity is not None:
                vx, vy = filtered_velocity
                speed = math.hypot(vx, vy)
                velocity_text = (
                    f"Velocity: vx={vx:.1f}, vy={vy:.1f} px/s"
                    f"   Speed={speed:.1f}"
                )
            else:
                velocity_text = "Velocity: waiting for next detection"

        else:
            status = "BALL LOST"
            velocity_text = ""

            if not paused:
                previous_position = None
                previous_time = None
                filtered_velocity = None
                trail.clear()

        # Package one measurement for the future controller.
        measurement = {
            "time_s": timestamp,
            "detected": ball is not None,
            "x_px": None,
            "y_px": None,
            "vx_px_s": None,
            "vy_px_s": None,
        }

        if ball is not None:
            measurement["x_px"] = float(x)
            measurement["y_px"] = float(y)

            if filtered_velocity is not None:
                measurement["vx_px_s"] = float(filtered_velocity[0])
                measurement["vy_px_s"] = float(filtered_velocity[1])

        # Print only when processing a new frame.
        # if not paused:
        #     print(measurement)
        
        points = list(trail)
        for start, end in zip(points, points[1:]):
            cv2.line(frame, start, end, (0, 200, 255), 2)

        # Text background improves readability on either polarity.
        cv2.rectangle(frame, (0, 0), (width, 85), (0, 0, 0), -1)
        cv2.putText(
            frame, status, (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
        )
        cv2.putText(
            frame, velocity_text, (10, 50),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
        )
        cv2.putText(
            frame, "Q: quit   Space: pause   R: reset",
            (10, 75), cv2.FONT_HERSHEY_SIMPLEX,
            0.5, (255, 255, 255), 1,
        )

        cv2.imshow("Ball tracking", frame)
        cv2.imshow("Detection mask", mask)

        delay = max(1, round(1000 / fps)) if is_video else 1
        key = cv2.waitKey(delay) & 0xFF

        if key == ord("q"):
            break
        elif key == ord(" ") and is_video:
            paused = not paused
        elif key == ord("r"):
            previous_position = None
            previous_time = None
            filtered_velocity = None
            trail.clear()

finally:
    cap.release()
    cv2.destroyAllWindows()