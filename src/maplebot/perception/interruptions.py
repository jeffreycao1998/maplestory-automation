"""Conservative screen interruptions. Flag and pause; never answer/dismiss a prompt."""
import cv2
import numpy as np


def central_panel(image):
    """Large, rectangular, mostly neutral bright central panel; heuristic, not OCR."""
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    neutral = cv2.inRange(hsv, (0,0,130), (179,65,255))
    contours, _ = cv2.findContours(neutral, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x,y,bw,bh = cv2.boundingRect(contour)
        area = bw*bh
        if (bw < 120 or bh < 60 or not .03*w*h <= area <= .65*w*h or
                not .25*w <= x+bw/2 <= .75*w or not .2*h <= y+bh/2 <= .8*h):
            continue
        polygon = cv2.approxPolyDP(contour, .015*cv2.arcLength(contour,True), True)
        if (len(polygon)==4 and cv2.isContourConvex(polygon) and
                cv2.contourArea(contour)/area > .92 and
                np.count_nonzero(neutral[y:y+bh,x:x+bw])/area > .85):
            return True
    return False


class AlertMatcher:
    def __init__(self, paths):
        self.templates = []
        for path in paths or []:
            try:
                image = cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
            except (ValueError,OSError):
                continue
            if image is not None and min(image.shape)>=8 and image.std()>5:
                self.templates.append(image)
        if len(self.templates)>16:
            raise ValueError("Use at most 16 interruption reference pictures")

    def matches(self, gray, threshold):
        for template in self.templates:
            if template.shape[0]>gray.shape[0] or template.shape[1]>gray.shape[1]:
                continue
            scores = cv2.matchTemplate(gray,template,cv2.TM_CCOEFF_NORMED)
            if np.nanmax(scores, initial=-1) >= threshold:
                return True
        return False
