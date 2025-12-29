"""
settings_model.py

第2段階リファクタ:
  - 設定(dict)の型ゆれ/デフォルト/キーの散在を減らすための「正規化」レイヤー。
  - 既存の保存形式(JSON/dict)は維持し、段階的に安全に導入する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


def _to_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off"):
            return False
    return default


def _to_int(v: Any, default: int) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _to_float(v: Any, default: float) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


@dataclass
class ImageItemSettings:
    """
    画像アイテム設定の最小モデル（現時点で“よく使うキー”のみを型で固定）。
    OCR/タイマーなどの詳細は当面 dict のまま（段階的移行のため）。
    """

    image_path: str = ""

    # click settings
    click_position: Optional[list] = None
    click_rect: Optional[list] = None
    point_click: bool = True
    range_click: bool = False
    random_click: bool = False
    right_click: bool = False
    backup_click: bool = False

    # ROI settings
    roi_enabled: bool = False
    roi_mode: str = "fixed"  # "fixed" | "variable"
    roi_rect: Optional[list] = None
    roi_rect_variable: Optional[list] = None

    # matching controls
    interval_time: float = 1.5
    backup_time: float = 300.0
    threshold: float = 0.8
    debounce_time: float = 0.0

    # nested settings (kept as dict for now)
    ocr_settings: Optional[dict] = None
    timer_mode: Optional[dict] = None

    # preserve unknown keys so we don't lose data during refactor
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], *, default_image_path: str = "") -> "ImageItemSettings":
        d = dict(d or {})
        known = {
            "image_path",
            "click_position",
            "click_rect",
            "point_click",
            "range_click",
            "random_click",
            "right_click",
            "backup_click",
            "roi_enabled",
            "roi_mode",
            "roi_rect",
            "roi_rect_variable",
            "interval_time",
            "backup_time",
            "threshold",
            "debounce_time",
            "ocr_settings",
            "timer_mode",
        }
        extra = {k: v for k, v in d.items() if k not in known}

        image_path = str(d.get("image_path") or default_image_path or "")

        # NOTE: click_position/click_rect/roi_rect は tuple/list のどちらでも来るので list に寄せる
        def _as_list(v):
            if v is None:
                return None
            if isinstance(v, (list, tuple)):
                return list(v)
            return None

        roi_mode = str(d.get("roi_mode") or "fixed")
        if roi_mode not in ("fixed", "variable"):
            roi_mode = "fixed"

        ocr_settings = d.get("ocr_settings")
        if ocr_settings is not None and not isinstance(ocr_settings, dict):
            ocr_settings = None

        timer_mode = d.get("timer_mode")
        if timer_mode is not None and not isinstance(timer_mode, dict):
            timer_mode = None

        return cls(
            image_path=image_path,
            click_position=_as_list(d.get("click_position")),
            click_rect=_as_list(d.get("click_rect")),
            point_click=_to_bool(d.get("point_click"), True),
            range_click=_to_bool(d.get("range_click"), False),
            random_click=_to_bool(d.get("random_click"), False),
            right_click=_to_bool(d.get("right_click"), False),
            backup_click=_to_bool(d.get("backup_click"), False),
            roi_enabled=_to_bool(d.get("roi_enabled"), False),
            roi_mode=roi_mode,
            roi_rect=_as_list(d.get("roi_rect")),
            roi_rect_variable=_as_list(d.get("roi_rect_variable")),
            interval_time=_to_float(d.get("interval_time"), 1.5),
            backup_time=_to_float(d.get("backup_time"), 300.0),
            threshold=_to_float(d.get("threshold"), 0.8),
            debounce_time=_to_float(d.get("debounce_time"), 0.0),
            ocr_settings=ocr_settings,
            timer_mode=timer_mode,
            extra=extra,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        out.update(self.extra or {})
        out.update(
            {
                "image_path": self.image_path,
                "click_position": self.click_position,
                "click_rect": self.click_rect,
                "point_click": bool(self.point_click),
                "range_click": bool(self.range_click),
                "random_click": bool(self.random_click),
                "right_click": bool(self.right_click),
                "backup_click": bool(self.backup_click),
                "roi_enabled": bool(self.roi_enabled),
                "roi_mode": self.roi_mode,
                "roi_rect": self.roi_rect,
                "roi_rect_variable": self.roi_rect_variable,
                "interval_time": float(self.interval_time),
                "backup_time": float(self.backup_time),
                "threshold": float(self.threshold),
                "debounce_time": float(self.debounce_time),
                "ocr_settings": self.ocr_settings,
                "timer_mode": self.timer_mode,
            }
        )
        return out


def normalize_image_item_settings(d: Mapping[str, Any], *, default_image_path: str = "") -> Dict[str, Any]:
    """
    既存コードは dict を期待しているため、外部APIは dict で返す。
    内部では dataclass に寄せて型/デフォルトを統一する。
    """
    return ImageItemSettings.from_dict(d or {}, default_image_path=default_image_path).to_dict()


