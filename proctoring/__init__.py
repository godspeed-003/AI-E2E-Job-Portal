"""Video proctoring package.

``analyzer`` wraps the CV backends (YuNet face detection, MediaPipe head pose,
YOLO object detection). ``rules`` turns per-frame analyses into debounced
events with severity weights. ``services/proctor_service`` persists events and
computes the final integrity score.

All three layers degrade gracefully: a missing model or import error silences
the relevant signal rather than taking down the interview.
"""
