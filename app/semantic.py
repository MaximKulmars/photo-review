from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort


LABELS = ("screenshot", "document", "saved", "accidental", "photo")


class LocalJunkClassifier:
    """Small fixed ONNX model over locally calculated visual features."""

    def __init__(self) -> None:
        configured = os.getenv("PHOTO_REVIEW_MODEL")
        self.path = (
            Path(configured)
            if configured
            else Path(__file__).parent / "models" / "junk_classifier_v1.onnx"
        )
        self._session: ort.InferenceSession | None = None
        self._failed = False

    @property
    def available(self) -> bool:
        return self.path.is_file() and not self._failed

    def predict(self, features: list[float]) -> dict[str, float]:
        if not self.available:
            return {}
        try:
            if self._session is None:
                self._session = ort.InferenceSession(
                    str(self.path), providers=["CPUExecutionProvider"]
                )
            output = self._session.run(
                None, {"features": np.asarray([features], dtype=np.float32)}
            )[0][0]
            return {label: float(output[index]) for index, label in enumerate(LABELS)}
        except Exception:
            self._failed = True
            return {}


classifier = LocalJunkClassifier()


def semantic_findings(
    *,
    brightness: float,
    contrast: float,
    sharpness: float,
    edge_density: float,
    text_length: int,
    no_camera: bool,
    common_screen: bool,
    ratio: float,
) -> list[tuple[str, str, float]]:
    features = [
        min(max(brightness / 255, 0), 1),
        min(max(contrast / 128, 0), 1),
        min(max(np.log1p(sharpness) / 10, 0), 1),
        min(max(edge_density * 10, 0), 1),
        min(max(text_length / 200, 0), 1),
        float(no_camera),
        float(common_screen),
        min(max(ratio / 3, 0), 1),
    ]
    scores = classifier.predict(features)
    reasons = {
        "screenshot": "Локальная модель: изображение похоже на скриншот",
        "document": "Локальная модель: изображение похоже на документ или экран",
        "saved": "Локальная модель: возможно, это сохранённая картинка или мем",
        "accidental": "Локальная модель: возможно, это случайный кадр",
    }
    return [
        (label, reasons[label], score)
        for label, score in scores.items()
        if label in reasons and score >= 0.70
    ]

