# ocr_runtime.py
# ★★★ 修正: ログ出力時に数値の.0を除去して表示するように変更 ★★★

import cv2
import re
import os
import sys
import numpy as np
import pytesseract
from datetime import datetime
from pathlib import Path
from pytesseract import Output
from PIL import Image

# Windows API用
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

# デバッグ出力/クロップ保存を停止（必要に応じて環境変数で再有効化）
DEBUG_SAVE_OCR_REGION = False
# 座標デバッグ用フラグ（詳細なROI情報をログに出す）
DEBUG_OCR_COORDS = False

class OCRRuntimeEvaluator:
    """
    自動化ループ内で使用するOCR判定クラス。
    """

    @staticmethod
    def _save_debug_ocr_images(raw_roi: np.ndarray, processed_roi: np.ndarray, capture_method: str = 'mss'):
        if not DEBUG_SAVE_OCR_REGION:
            return
        try:
            base_dir = Path(__file__).resolve().parent
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_path = base_dir / f"ocr_debug_raw_{capture_method}_{timestamp}.png"
            proc_path = base_dir / f"ocr_debug_processed_{capture_method}_{timestamp}.png"
            cv2.imwrite(str(raw_path), raw_roi)
            cv2.imwrite(str(proc_path), processed_roi)
            if DEBUG_OCR_COORDS:
                print(f"[OCR DEBUG] Saved debug images: {raw_path.name}, {proc_path.name}")
        except Exception as e:
            # デバッグ用のため、失敗してもアプリ動作に影響を与えない
            if DEBUG_OCR_COORDS:
                print(f"[OCR DEBUG] Failed to save debug images: {e}")
            pass

    @staticmethod
    def _get_precise_window_offset(hwnd):
        # クライアント領域基準のため補正不要
        return 0, 0

    @staticmethod
    def evaluate(screen_image: np.ndarray, parent_pos: tuple, ocr_settings: dict, item_settings: dict = None, current_scale: float = 1.0, capture_scale: float = 1.0, hwnd=None, capture_method: str = 'mss') -> tuple[bool, str, str, float]:
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

        # --- 座標計算 ---
        # parent_pos はテンプレートマッチ結果の左上座標
        # roi はその左上からの相対座標（プレビューで設定した値）
        px, py = parent_pos
        rx, ry, rw, rh = roi

        # 旧版ロジック: item_settings の ROI オフセットを考慮し、ROI 全体に current_scale を乗算
        roi_offset_x = 0
        roi_offset_y = 0
        if item_settings and item_settings.get("roi_enabled", False):
            roi_mode = item_settings.get("roi_mode", "fixed")
            rec_roi_rect = item_settings.get("roi_rect_variable") if roi_mode == "variable" else item_settings.get("roi_rect")
            if rec_roi_rect:
                # ★★★ 重要: roi_offsetはtemplate_roi_rectから取得される ★★★
                # template_roi_rectは認識範囲（recognition_area）内の座標系で保存されている
                # parent_posも認識範囲内の座標系なので、roi_offsetはそのまま使用できる
                roi_offset_x = max(0, rec_roi_rect[0])
                roi_offset_y = max(0, rec_roi_rect[1])

        scaled_rx = (rx - roi_offset_x) * current_scale
        scaled_ry = (ry - roi_offset_y) * current_scale
        scaled_rw = rw * current_scale
        scaled_rh = rh * current_scale

        # 端数切り捨てによるズレを避けるため、丸めて整数化
        x1 = int(round(px + scaled_rx))
        y1 = int(round(py + scaled_ry))
        x2 = int(round(px + scaled_rx + scaled_rw))
        y2 = int(round(py + scaled_ry + scaled_rh))

        # ★★★ WindowsだけX方向に+4px補正（さらに右側へ寄せる） ★★★
        if sys.platform == 'win32':
            x1 += 4
            x2 += 4

        # 画像範囲内にクランプ
        h_img, w_img = screen_image.shape[:2]
        x1 = max(0, min(x1, w_img))
        y1 = max(0, min(y1, h_img))
        x2 = max(0, min(x2, w_img))
        y2 = max(0, min(y2, h_img))

        roi_info = f"[ROI: P({px},{py}) Offset(-{roi_offset_x}, -{roi_offset_y}) Scale({current_scale:.3f}) -> ({x1},{y1})-({x2},{y2})]"

        # DEBUG_OCR_COORDS が無効のため出力を停止

        if x1 >= x2 or y1 >= y2:
            return False, f"OCR Error: Invalid ROI {roi_info}", "", 0.0

        crop_img = screen_image[y1:y2, x1:x2]
        
        if crop_img.size == 0:
            return False, "OCR Error: Empty crop", "", 0.0

        # ★★★ 原因特定: DXCamとMSSでキャプチャされた画像の同一座標の内容を比較するため、周辺領域も保存 ★★★
        # 1-2ピクセル程度のずれを検出するため、クロップ領域の周辺（±5ピクセル）も保存
        if DEBUG_SAVE_OCR_REGION and DEBUG_OCR_COORDS:
            try:
                h_img, w_img = screen_image.shape[:2]
                # 周辺領域を拡張（±5ピクセル）
                expand = 5
                x1_expanded = max(0, x1 - expand)
                y1_expanded = max(0, y1 - expand)
                x2_expanded = min(w_img, x2 + expand)
                y2_expanded = min(h_img, y2 + expand)
                expanded_crop = screen_image[y1_expanded:y2_expanded, x1_expanded:x2_expanded]
                if expanded_crop.size > 0:
                    base_dir = Path(__file__).resolve().parent
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    expanded_path = base_dir / f"ocr_debug_expanded_{capture_method}_{timestamp}.png"
                    cv2.imwrite(str(expanded_path), expanded_crop)
                    print(f"[OCR DEBUG] Saved expanded crop: {expanded_path.name} (crop=({x1},{y1},{x2-x1},{y2-y1}) expanded=({x1_expanded},{y1_expanded},{x2_expanded-x1_expanded},{y2_expanded-y1_expanded}))")
            except Exception as e:
                if DEBUG_OCR_COORDS:
                    print(f"[OCR DEBUG] Failed to save expanded crop: {e}")

        processed_img = OCRRuntimeEvaluator._preprocess_image(crop_img, config)
        OCRRuntimeEvaluator._save_debug_ocr_images(crop_img, processed_img, capture_method)

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
            return False, f"OCR Engine Error: {str(e)}", "", 0.0

        # --- 判定ロジック ---
        operator_raw = condition.get("operator", "==")
        target_value_raw = condition.get("value", "")

        # ★★★ 修正: 演算子の値を正規化（翻訳された文字列から実際の演算子値を抽出） ★★★
        operator = OCRRuntimeEvaluator._normalize_operator(operator_raw, is_numeric_mode)
        
        # ★★★ デバッグ: 演算子の正規化結果をログに出力 ★★★
        if DEBUG_OCR_COORDS and operator_raw != operator:
            print(f"[OCR DEBUG] Operator normalized: '{operator_raw}' -> '{operator}' (numeric_mode={is_numeric_mode})")

        if is_numeric_mode:
            numeric_val = OCRRuntimeEvaluator._extract_first_number(raw_text)
            
            if numeric_val is None:
                return False, f"OCR Failed: No number in {log_text_display}", raw_text, avg_conf

            try:
                target_val = float(str(target_value_raw))
            except ValueError:
                return False, f"Config Error: Invalid target '{target_value_raw}'", raw_text, avg_conf

            # ★★★ 修正: 演算子がNoneや空文字列の場合もエラーとして扱う ★★★
            if not operator or operator not in [">=", "<=", "==", "!=", ">", "<"]:
                return False, f"Config Error: Invalid operator '{operator_raw}' (normalized: '{operator}')", raw_text, avg_conf

            result = False
            if operator == ">=": result = (numeric_val >= target_val)
            elif operator == "<=": result = (numeric_val <= target_val)
            elif operator == "==": result = (numeric_val == target_val)
            elif operator == "!=": result = (numeric_val != target_val)
            elif operator == ">": result = (numeric_val > target_val)
            elif operator == "<": result = (numeric_val < target_val)
            else:
                # ★★★ 追加: 想定外の演算子の場合はFalseを返す（安全性のため） ★★★
                return False, f"Config Error: Unsupported operator '{operator}'", raw_text, avg_conf
            
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
            # 文章モード: 条件値が空の場合は「常に一致」になって事故りやすい（Contains/Regexが常にTrue）ため設定エラー扱いで不一致にする
            tgt_str = "" if target_value_raw is None else str(target_value_raw)
            if tgt_str.strip() == "":
                return False, "Config Error: Text condition value is empty", raw_text, avg_conf
            tgt_lower = tgt_str.lower()
            
            # ★★★ 修正: 演算子がNoneや空文字列の場合もエラーとして扱う ★★★
            if not operator or operator not in ["Equals", "Contains", "Regex"]:
                return False, f"Config Error: Invalid operator '{operator_raw}' (normalized: '{operator}')", raw_text, avg_conf
            
            re画sult = False
            if operator == "Equals":
                result = (res_lower == tgt_lower)
            elif operator == "Contains":
                result = (tgt_lower in res_lower)
            elif operator == "Regex":
                try:
                    result = bool(re.search(tgt_str, raw_text, re.IGNORECASE))
                except:
                    return False, "Regex Error", raw_text, avg_conf
            else:
                # ★★★ 追加: 想定外の演算子の場合はFalseを返す（安全性のため） ★★★
                return False, f"Config Error: Unsupported operator '{operator}'", raw_text, avg_conf
            
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

    @staticmethod
    def _normalize_operator(operator_raw, is_numeric_mode: bool) -> str:
        """
        演算子の値を正規化します。
        翻訳された文字列やNoneの場合でも、実際の演算子値に変換します。
        
        Args:
            operator_raw: 保存されている演算子値（翻訳された文字列の可能性あり）
            is_numeric_mode: 数字モードかどうか
            
        Returns:
            正規化された演算子値（">=", "<=", "==", "!=", ">", "<" または "Equals", "Contains", "Regex"）
        """
        if not operator_raw:
            return ">=" if is_numeric_mode else "Contains"
        
        operator_str = str(operator_raw).strip()
        
        if is_numeric_mode:
            # 数字モード: 既に正しい値の場合はそのまま返す
            if operator_str in [">=", "<=", "==", "!=", ">", "<"]:
                return operator_str
            
            # 翻訳された文字列から演算子を抽出
            operator_lower = operator_str.lower()
            if ">=" in operator_str or "gte" in operator_lower or "以上" in operator_str:
                return ">="
            elif "<=" in operator_str or "lte" in operator_lower or "以下" in operator_str:
                return "<="
            elif "==" in operator_str or "eq" in operator_lower or "一致" in operator_str:
                return "=="
            elif "!=" in operator_str or "neq" in operator_lower or "一致しない" in operator_str:
                return "!="
            elif ">" in operator_str and "=" not in operator_str or "gt" in operator_lower or "より大きい" in operator_str:
                return ">"
            elif "<" in operator_str and "=" not in operator_str or "lt" in operator_lower or "より小さい" in operator_str:
                return "<"
            else:
                return ">="  # デフォルト値
        else:
            # 文章モード: 既に正しい値の場合はそのまま返す
            if operator_str in ["Equals", "Contains", "Regex"]:
                return operator_str
            
            # 翻訳された文字列から演算子を抽出
            operator_lower = operator_str.lower()
            if "contains" in operator_lower or "含む" in operator_str:
                return "Contains"
            elif "equals" in operator_lower or "等しい" in operator_str:
                return "Equals"
            elif "regex" in operator_lower or "正規表現" in operator_str:
                return "Regex"
            else:
                return "Contains"  # デフォルト値