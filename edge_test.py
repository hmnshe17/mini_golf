#!/usr/bin/env python3
"""Edge detection test: shows the camera and its Canny edges. Press q to quit."""
import os
os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")  # avoids the imshow crash on the Radxa
import cv2

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
print("opened:", cap.isOpened())
while True:
    ok, f = cap.read()
    if not ok:
        print("no frame")
        break
    g = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    cv2.imshow("edges", cv2.Canny(g, 50, 150))
    cv2.imshow("camera", f)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
cap.release()
cv2.destroyAllWindows()
