# ocr_manager.py
# ★★★ 修正: 保存先をユーザーディレクトリに戻しました ★★★

import os
import sys
import re
import cv2
import numpy as np
import requests
import pytesseract
import time
from PIL import Image
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal
import shutil

# --- Windows用: Tesseract実行ファイルの自動探索 ---
if sys.platform == 'win32':
    if shutil.which('tesseract') is None:
        possible_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe")
        ]
        for p in possible_paths:
            if os.path.exists(p):
                pytesseract.pytesseract.tesseract_cmd = p
                print(f"[INFO] Tesseract exe found at: {p}")
                break
# ------------------------------------------------

TESSDATA_BASE_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/"

LOCALE_TO_TESS_CODE = {
    "en_US": "eng", "ja_JP": "jpn", "de_DE": "deu", "fr_FR": "fra", "it_IT": "ita",
    "es_ES": "spa", "da_DK": "dan", "fi_FI": "fin", "hi_IN": "hin", "ar_SA": "ara",
    "ko_KR": "kor", "zh_CN": "chi_sim", "pt_BR": "por", "ru_RU": "rus", "pl_PL": "pol",
    "sv_SE": "swe", "no_NO": "nor", "nl_NL": "nld", "tr_TR": "tur"
}

TESS_CODE_DISPLAY_MAP = {
    "eng": "English (eng)", "jpn": "Japanese (jpn)", "deu": "German (deu)",
    "fra": "French (fra)", "ita": "Italian (ita)", "spa": "Spanish (spa)",
    "dan": "Danish (dan)", "fin": "Finnish (fin)", "hin": "Hindi (hin)",
    "ara": "Arabic (ara)", "kor": "Korean (kor)", "chi_sim": "Chinese Simp. (chi_sim)",
    "por": "Portuguese (por)", "rus": "Russian (rus)", "pol": "Polish (pol)",
    "swe": "Swedish (swe)", "nor": "Norwegian (nor)", "nld": "Dutch (nld)",
    "tur": "Turkish (tur)"
}

def get_tess_code_from_locale(locale_code: str) -> str:
    return LOCALE_TO_TESS_CODE.get(locale_code, "eng")

class OCRConfig:
    def __init__(self):
        self.scale = 2.0
        self.threshold = 128
        self.invert = False
        self.lang = "eng"
        self.numeric_mode = False
        self.psm = 7 

class OCRWorker(QThread):
    finished = Signal(str, object, object)
    error = Signal(str)

    def __init__(self, image: np.ndarray, config: OCRConfig, roi_rect: tuple = None, data_dir: str = None):
        super().__init__()
        self.image = image
        self.config = config
        self.roi_rect = roi_rect
        self.data_dir = data_dir

    def run(self):
        try:
            if self.data_dir:
                os.environ['TESSDATA_PREFIX'] = self.data_dir

            processed_img = self._crop_and_process_image()
            pil_img = Image.fromarray(processed_img)
            
            psm_val = getattr(self.config, 'psm', 7)
            custom_config = f'--psm {psm_val}'
            
            if self.config.numeric_mode:
                custom_config += ' -c tessedit_char_whitelist=0123456789.-,'

            lang_str = self.config.lang
            
            text = pytesseract.image_to_string(
                pil_img, 
                lang=lang_str, 
                config=custom_config
            )

            raw_text = text.strip()
            numeric_value = None

            if self.config.numeric_mode:
                numeric_value = self._extract_first_number(raw_text)

            self.finished.emit(raw_text, numeric_value, processed_img)

        except Exception as e:
            error_msg = f"{str(e)} (Lang: {self.config.lang})"
            if "tesseract is not installed" in str(e) and sys.platform == 'win32':
                error_msg += "\n[Hint] Please install Tesseract-OCR software."
            elif "Failed loading language" in str(e):
                error_msg += f"\n[Hint] Check data dir: {self.data_dir}"
            self.error.emit(error_msg)

    def _crop_and_process_image(self) -> np.ndarray:
        h, w = self.image.shape[:2]
        if self.roi_rect:
            rx, ry, rw, rh = self.roi_rect
            # ROI範囲のクランプ処理 (負の値対応)
            x1 = max(0, min(rx, w))
            y1 = max(0, min(ry, h))
            x2 = max(0, min(rx + rw, w))
            y2 = max(0, min(ry + rh, h))
            
            if x2 <= x1 or y2 <= y1:
                 return np.full((10, 10), 255, dtype=np.uint8)
                 
            crop_img = self.image[y1:y2, x1:x2]
            if crop_img.size == 0: return np.full((10, 10), 255, dtype=np.uint8)
        else:
            crop_img = self.image.copy()

        if len(crop_img.shape) == 3: gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
        else: gray = crop_img

        if self.config.scale > 1.0:
            gray = cv2.resize(gray, None, fx=self.config.scale, fy=self.config.scale, interpolation=cv2.INTER_CUBIC)

        if self.config.invert: gray = cv2.bitwise_not(gray)

        _, binary = cv2.threshold(gray, self.config.threshold, 255, cv2.THRESH_BINARY)
        
        return binary

    def _extract_first_number(self, text: str):
        try:
            clean_text = text.replace(',', '')
            match = re.search(r'-?\d+(?:\.\d+)?', clean_text)
            if match:
                return float(match.group())
            return None
        except: return None

class OCRManager(QObject):
    download_progress = Signal(str, int)
    download_finished = Signal(bool, str)
    
    def __init__(self):
        super().__init__()
        # ★★★ 修正: ユーザーディレクトリを使用 ★★★
        self.data_dir = str(Path.home() / "click_pic" / "tessdata")
        
        if not os.path.exists(self.data_dir):
            try:
                os.makedirs(self.data_dir, exist_ok=True)
            except Exception as e:
                print(f"[OCR ERROR] Failed to create tessdata dir: {e}")
                
        os.environ['TESSDATA_PREFIX'] = self.data_dir

    def is_language_ready(self, lang_code: str) -> bool:
        langs = lang_code.split('+')
        for l in langs:
            l = l.strip()
            path = os.path.join(self.data_dir, f"{l}.traineddata")
            if not os.path.exists(path) or os.path.getsize(path) < 1024: return False
        return True

    def get_tessdata_path(self):
        return self.data_dir

    def create_worker(self, image, config, roi_rect):
        return OCRWorker(image, config, roi_rect, self.data_dir)
    
    def download_languages(self, lang_codes: list):
        targets = list(set(lang_codes))
        self.downloader = DownloaderThread(self.data_dir, targets)
        self.downloader.progress.connect(self.download_progress)
        self.downloader.finished.connect(self.download_finished)
        self.downloader.start()

class DownloaderThread(QThread):
    progress = Signal(str, int)
    finished = Signal(bool, str)

    def __init__(self, save_dir, target_langs):
        super().__init__()
        self.save_dir = save_dir
        self.target_langs = target_langs

    def run(self):
        if not os.path.exists(self.save_dir):
            try: os.makedirs(self.save_dir, exist_ok=True)
            except Exception as e:
                self.finished.emit(False, f"Folder Error: {e}")
                return

        success_count = 0
        total = len(self.target_langs)
        MAX_RETRIES = 3
        RETRY_DELAY = 2
        TIMEOUT_SEC = 30
        CHUNK_SIZE = 8192

        for lang in self.target_langs:
            fname = f"{lang}.traineddata"
            url = TESSDATA_BASE_URL + fname
            path = os.path.join(self.save_dir, fname)
            
            if os.path.exists(path) and os.path.getsize(path) > 1024:
                success_count += 1; self.progress.emit(fname, 100); continue

            download_success = False
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    self.progress.emit(fname, 0)
                    res = requests.get(url, stream=True, timeout=TIMEOUT_SEC)
                    res.raise_for_status()
                    total_len = res.headers.get('content-length')
                    with open(path, 'wb') as f:
                        if total_len is None:
                            f.write(res.content); self.progress.emit(fname, 100)
                        else:
                            dl = 0; total_len = int(total_len)
                            for chunk in res.iter_content(chunk_size=CHUNK_SIZE):
                                dl += len(chunk); f.write(chunk)
                                if dl % (CHUNK_SIZE * 20) == 0: self.progress.emit(fname, int(100 * dl / total_len))
                    download_success = True; break
                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY)
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except:
                                pass
            
            if download_success:
                success_count += 1
            else:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass

        if success_count == total: self.finished.emit(True, "All downloads finished.")
        elif success_count > 0: self.finished.emit(True, "Partial download finished.")
        else: self.finished.emit(False, "Download failed.")