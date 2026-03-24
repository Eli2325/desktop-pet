"""GIF / QMovie loading and AnimatedLabel — extracted from pet_core for clarity."""

from __future__ import annotations

import os
from typing import Any, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QMovie, QPixmap, QTransform
from PyQt6.QtWidgets import QLabel

# 默认尺寸与缩放（与桌宠显示盒一致）
PET_SIZE_DEFAULT = 192
MIN_SIZE = 128
MAX_SIZE = 320
SIZE_STEP = 8


class AnimatedLabel(QLabel):
    """
    用 QMovie 但支持“水平翻转”。
    每帧取 currentPixmap() 做镜像后 setPixmap。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._movie = None
        self._movie_key = None
        self._flip = False

    def stop_movie(self):
        if self._movie is not None:
            try:
                self._movie.frameChanged.disconnect(self._on_frame_changed)
            except Exception:
                pass
            self._movie.stop()
        self._movie = None
        self._movie_key = None

    def play_movie(self, key: str, movie: QMovie, flip: bool):
        if self._movie_key == key and self._movie is movie:
            self._flip = flip
            self._render_frame()
            return

        self.stop_movie()
        self._movie = movie
        self._movie_key = key
        self._flip = flip

        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.start()
        self._render_frame()

    def set_flip(self, flip: bool):
        self._flip = flip

    def _on_frame_changed(self, _):
        self._render_frame()

    def _render_frame(self):
        if self._movie is None:
            return
        pm = self._movie.currentPixmap()
        if pm.isNull():
            return
        if self._flip:
            pm = pm.transformed(QTransform().scale(-1, 1))
        self.setPixmap(pm)


def load_movie_for_key(
    assets_dir: str, key: str, filename: str, pet_width: int, pet_height: int
) -> Optional[QMovie]:
    path = os.path.join(assets_dir, filename)
    if not os.path.exists(path):
        # Fallback: case-insensitive filename match (for packaged assets on mixed-case filesystems)
        target = filename.lower()
        try:
            for name in os.listdir(assets_dir):
                if name.lower() == target:
                    path = os.path.join(assets_dir, name)
                    break
            else:
                return None
        except Exception:
            return None
    mv = QMovie(path)
    if not mv.isValid():
        return None
    mv.setCacheMode(QMovie.CacheMode.CacheAll)
    mv.setScaledSize(QSize(pet_width, pet_height))
    return mv


def rebuild_pet_movies(pet: Any) -> None:
    """Stop label + old movies, rebuild pet.movies from pet.movie_files."""
    pet.label.stop_movie()
    pet.current_anim = None

    for mv in pet.movies.values():
        if mv is not None:
            try:
                mv.stop()
            except Exception:
                pass

    pet.movies = {}
    for k, fn in pet.movie_files.items():
        pet.movies[k] = load_movie_for_key(
            pet.assets_dir, k, fn, pet.pet_width, pet.pet_height
        )
