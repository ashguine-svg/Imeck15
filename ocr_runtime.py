# ocr_runtime.py
# ★★★ 修正: ログ出力時に数値の.0を除去して表示するように変更 ★★★

import cv2
import re
import numpy as np
import pytesseract
from pytesseract import Output
from PIL import Image
import os
import sys

# Windows API用
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

class OCRRuntimeEvaluator:
    """
    自動化ループ内で使用するOCR判定クラス。
    """

    @staticmethod
    def _get_precise_window_offset(hwnd):
        # クライアント領域基準のため補正不要
        return 0, 0

    @staticmethod
    def evaluate(screen_image: np.ndarray, parent_pos: tuple, ocr_settings: dict, item_settings: dict = None, current_scale: float = 1.0, hwnd=None) -> tuple[bool, str, str, float]:
        """
        Returns:
            bool: 条件合致(True/False)
            str: ログメッセージ
            str: 読み取った生のテキスト
            float: 信頼度スコア (0.0 - 100.0)
        """
        
        if not ocr_settings or not ocr_settings.get("enabled", False):
            return True, "OCR Skipped (Disabled)", "", 0.0

        roi = ocr_settings.get("roi") 
        config = ocr_settings.get("config", {})
        condition = ocr_settings.get("condition", {}) 

        if not roi:
            return False, "OCR Error: No ROI setting", "", 0.0

        px, py = parent_pos
        rx, ry, rw, rh = roi
        
        roi_offset_x = 0
        roi_offset_y = 0
        
        if item_settings and item_settings.get('roi_enabled', False):
            roi_mode = item_settings.get('roi_mode', 'fixed')
            rec_roi_rect = item_settings.get('roi_rect_variable') if roi_mode == 'variable' else item_settings.get('roi_rect')
            
            if rec_roi_rect:
                roi_offset_x = max(0, rec_roi_rect[0])
                roi_offset_y = max(0, rec_roi_rect[1])

        scaled_rx = (rx - roi_offset_x) * current_scale
        scaled_ry = (ry - roi_offset_y) * current_scale
        scaled_rw = rw * current_scale
        scaled_rh = rh * current_scale

        x1 = int(px + scaled_rx)
        y1 = int(py + scaled_ry)
        x2 = int(px + scaled_rx + scaled_rw)
        y2 = int(py + scaled_ry + scaled_rh)

        h_img, w_img = screen_image.shape[:2]
        x1 = max(0, min(x1, w_img))
        y1 = max(0, min(y1, h_img))
        x2 = max(0, min(x2, w_img))
        y2 = max(0, min(y2, h_img))

        roi_info = f"[ROI: P({px},{py}) Offset(-{roi_offset_x}, -{roi_offset_y}) Scale({current_scale:.3f}) -> ({x1},{y1})]"

        if x1 >= x2 or y1 >= y2:
             return False, f"OCR Error: Invalid ROI {roi_info}", "", 0.0

        crop_img = screen_image[y1:y2, x1:x2]
        
        if crop_img.size == 0:
            return False, f"OCR Error: Empty crop {roi_info}", "", 0.0

        processed_img = OCRRuntimeEvaluator._preprocess_image(crop_img, config)

        try:
            lang = config.get("lang", "eng")
            custom_config = '--psm 7'
            
            is_numeric_mode = config.get("numeric_mode", False)
            if is_numeric_mode:
                custom_config += ' -c tessedit_char_whitelist=0123456789.-'

            pil_img = Image.fromarray(processed_img)
            pil_img.info['dpi'] = (72, 72)
            
            data = pytesseract.image_to_data(pil_img, lang=lang, config=custom_config, output_type=Output.DICT)
            
            valid_texts = []
            confidences = []
            
            n_boxes = len(data['text'])
            for i in range(n_boxes):
                if int(data['conf'][i]) > -1:
                    txt = data['text'][i].strip()
                    if txt:
                        valid_texts.append(txt)
                        confidences.append(int(data['conf'][i]))
            
            raw_text = "".join(valid_texts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            
            log_text_display = f"'{raw_text}'(Conf:{avg_conf:.0f})"
            
            MIN_CONFIDENCE = 40
            if avg_conf < MIN_CONFIDENCE and raw_text:
                return False, f"OCR Low Confidence: {log_text_display} < {MIN_CONFIDENCE}", raw_text, avg_conf

        except Exception as e:
            return False, f"OCR Engine Error: {str(e)} {roi_info}", "", 0.0

        # --- 判定ロジック ---
        operator = condition.get("operator", "==")
        target_value_raw = condition.get("value", "")

        if is_numeric_mode:
            numeric_val = OCRRuntimeEvaluator._extract_first_number(raw_text)
            
            if numeric_val is None:
                return False, f"OCR Failed: No number in {log_text_display} {roi_info}", raw_text, avg_conf

            try:
                target_val = float(str(target_value_raw))
            except ValueError:
                return False, f"Config Error: Invalid target '{target_value_raw}'", raw_text, avg_conf

            result = False
            if operator == ">=": result = (numeric_val >= target_val)
            elif operator == "<=": result = (numeric_val <= target_val)
            elif operator == "==": result = (numeric_val == target_val)
            elif operator == "!=": result = (numeric_val != target_val)
            elif operator == ">": result = (numeric_val > target_val)
            elif operator == "<": result = (numeric_val < target_val)
            
            status = "PASS" if result else "FAIL"
            
            # ★★★ 修正箇所: ログ表示用に数値をフォーマット (.0除去) ★★★
            def fmt_num(n):
                return str(int(n)) if n.is_integer() else str(n)

            disp_numeric = fmt_num(numeric_val)
            disp_target = fmt_num(target_val)
            
            log_msg = f"OCR Comp: {disp_numeric} {operator} {disp_target} -> {status} [Conf:{avg_conf:.0f}]"
            # -----------------------------------------------------------
            
            return result, log_msg, raw_text, avg_conf

        else:
            res_lower = raw_text.lower()
            tgt_lower = str(target_value_raw).lower()
            
            result = False
            if operator == "Equals":
                result = (res_lower == tgt_lower)
            elif operator == "Contains":
                result = (tgt_lower in res_lower)
            elif operator == "Regex":
                try:
                    result = bool(re.search(str(target_value_raw), raw_text, re.IGNORECASE))
                except:
                    return False, "Regex Error", raw_text, avg_conf
            
            status = "PASS" if result else "FAIL"
            log_msg = f"OCR Text: {log_text_display} {operator} '{target_value_raw}' -> {status}"
            return result, log_msg, raw_text, avg_conf

    @staticmethod
    def _preprocess_image(img: np.ndarray, config: dict) -> np.ndarray:
        scale = config.get("scale", 2.0)
        threshold = config.get("threshold", 128)
        invert = config.get("invert", False)
        if len(img.shape) == 3: gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else: gray = img
        if scale > 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if invert: gray = cv2.bitwise_not(gray)
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        return binary

    @staticmethod
    def _extract_first_number(text: str):
        try:
            text = text.replace('l', '1').replace('I', '1').replace('|', '1')
            text = text.replace('O', '0').replace('o', '0')
            text = text.replace('S', '5').replace('s', '5')
            clean_text = re.sub(r'[^\d\.\-]', '', text)
            match = re.search(r'-?\d+(?:\.\d+)?', clean_text)
            if match: return float(match.group())
            return None
        except: return None