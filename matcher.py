# matcher.py
# ★★★ (リファクタリング) マッチングロジックを戦略ごとに分割し、可読性を向上 ★★★

import cv2
from PIL import Image
import imagehash
import numpy as np

def calculate_phash(image):
    """
    OpenCVの画像(Numpy配列)からpHashを計算します。
    """
    if image is None:
        return None
    try:
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        return imagehash.phash(pil_image)
    except Exception:
        return None

def _match_template_task(screen_image, template_image, template_data, screen_shape, template_shape, effective_strict_color: bool):
    """
    メインのマッチングタスク関数。
    条件に応じて適切なマッチング戦略関数を呼び出します。
    """
    settings = template_data['settings']
    threshold = settings.get('threshold', 0.8)

    s_h, s_w = screen_shape
    t_h, t_w = template_shape

    # サイズチェック: テンプレートがスクリーンより大きい場合はスキップ
    if t_h > s_h or t_w > s_w:
        return None

    # --- 戦略の分岐 ---
    try:
        if effective_strict_color:
            result_val, result_loc = _match_strict_color(screen_image, template_data['template'])
        else:
            result_val, result_loc = _match_standard(screen_image, template_data['template'])
            
    except Exception:
        return None

    # --- 結果の判定 ---
    if result_val >= threshold:
        return {
            'path': template_data['path'],
            'settings': settings,
            'location': result_loc,
            'confidence': result_val,
            'scale': template_data['scale'],
            'rect': (result_loc[0], result_loc[1], result_loc[0] + t_w, result_loc[1] + t_h)
        }
    
    return None

def _match_strict_color(screen_image, template_image_data):
    """
    色調厳格モード (Strict Color Matching)
    RGB各チャンネルごとにマッチングを行い、最小値(Min)を採用します。
    """
    # 必要な場合のみデータを取得（UMat -> Numpy）
    screen_np = screen_image.get() if hasattr(screen_image, 'get') else screen_image
    template_np = template_image_data.get() if hasattr(template_image_data, 'get') else template_image_data
    
    # 安全性チェック: データ不正やチャンネル不足
    if screen_np is None or template_np is None:
        return -1.0, (-1, -1)
    if len(screen_np.shape) < 3 or screen_np.shape[2] != 3:
        return -1.0, (-1, -1)

    # チャンネル分離
    try:
        s_b, s_g, s_r = cv2.split(screen_np)
        t_b, t_g, t_r = cv2.split(template_np)
        
        # 3回マッチング
        result_b = cv2.matchTemplate(s_b, t_b, cv2.TM_CCOEFF_NORMED)
        result_g = cv2.matchTemplate(s_g, t_g, cv2.TM_CCOEFF_NORMED)
        result_r = cv2.matchTemplate(s_r, t_r, cv2.TM_CCOEFF_NORMED)
        
        # 最小値(Min)ロジック: 全ての色が合致している箇所を探す
        result_min = cv2.min(result_b, cv2.min(result_g, result_r))
        
        _, max_val, _, max_loc = cv2.minMaxLoc(result_min)
        return max_val, max_loc
        
    except cv2.error:
        return -1.0, (-1, -1)

def _match_standard(screen_image, template_image_data):
    """
    通常マッチングモード
    OpenCL (UMat) と Numpy の型不一致を自動解決して処理します。
    """
    # 型不一致 (UMat vs Numpy) の解決
    is_screen_umat = isinstance(screen_image, cv2.UMat)
    is_template_umat = isinstance(template_image_data, cv2.UMat)

    if is_screen_umat and not is_template_umat:
        screen_image = screen_image.get()
    elif not is_screen_umat and is_template_umat:
        template_image_data = template_image_data.get()

    try:
        result = cv2.matchTemplate(screen_image, template_image_data, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return max_val, max_loc
    except cv2.error:
        return -1.0, (-1, -1)
