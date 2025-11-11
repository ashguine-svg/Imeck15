# matcher.py
# ★★★ 色調厳格モードを (最小値) から (平均値) に変更 ★★★

import cv2
from PIL import Image
import imagehash

def calculate_phash(image):
    """
    OpenCVの画像(Numpy配列)からpHashを計算します。
    画面の安定性チェックなどに利用されます。
    
    Args:
        image: OpenCVの画像 (BGRフォーマットのNumpy配列)。

    Returns:
        成功した場合は imagehash オブジェクト、失敗した場合は None。
    """
    if image is None:
        return None
    try:
        # OpenCVのBGRフォーマットからPillowが扱えるRGBフォーマットに変換
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        return imagehash.phash(pil_image)
    except Exception:
        # 変換やハッシュ計算に失敗した場合
        return None

# --- ▼▼▼ 修正箇所 (5.4) [cite: 58-67] ▼▼▼ ---
def _match_template_task(screen_image, template_image, template_data, screen_shape, template_shape, effective_strict_color: bool):
    """
    画面イメージとテンプレートイメージのマッチングを行うタスク。
    (色調厳格モード対応)
    Args:
        screen_image: 検索対象の画面イメージ (BGR or Gray, UMat or Numpy)。
        template_image: テンプレートイメージ (BGR or Gray, UMat or Numpy)。(core.pyから渡されるが、下位互換性/仕様書のため template_data から再取得)
        template_data: テンプレート画像と設定情報を含む辞書。
        screen_shape: 画面イメージの高さと幅 (h, w)。
        template_shape: テンプレートイメージの高さと幅 (h, w)。
        effective_strict_color (bool): 色調厳格モードを実行するかどうかのフラグ。
    Returns:
        一致度が閾値を超えた場合にマッチ情報を含む辞書、それ以外は None。
    """
    path = template_data['path']
    settings = template_data['settings']
    # core.pyは 'template' キーにも画像データ(UMat/Numpy)を入れて渡す
    template_image_data = template_data['template'] 
    scale = template_data['scale'] 
    
    threshold = settings.get('threshold', 0.8)

    s_h, s_w = screen_shape
    t_h, t_w = template_shape

    # テンプレートがスクリーンより大きい場合はマッチング不可能
    if t_h > s_h or t_w > s_w:
        return None

    max_val = -1.0
    max_loc = (-1, -1)

    try:
        # UMat/Numpy両対応のため .get() を使う (core側でuse_cl=Falseにしたので安全だが念のため)
        # (Numpy配列を取得)
        screen_np = screen_image.get() if hasattr(screen_image, 'get') else screen_image
        template_np = template_image_data.get() if hasattr(template_image_data, 'get') else template_image_data

        if effective_strict_color:
            # --- 色調厳格モード (BGRが前提) ---
            # チャンネル分離
            s_b, s_g, s_r = cv2.split(screen_np)
            t_b, t_g, t_r = cv2.split(template_np)
            
            # 3回マッチング
            result_b = cv2.matchTemplate(s_b, t_b, cv2.TM_CCOEFF_NORMED)
            result_g = cv2.matchTemplate(s_g, t_g, cv2.TM_CCOEFF_NORMED)
            result_r = cv2.matchTemplate(s_r, t_r, cv2.TM_CCOEFF_NORMED)
            
            # --- ▼▼▼ 修正箇所 (判定を「最小値」から「平均値」に変更) ▼▼▼ ---
            # (旧) 最小値マップを作成 
            # result_min = cv2.min(result_b, cv2.min(result_g, result_r))
            # _, max_val, _, max_loc = cv2.minMaxLoc(result_min)

            # (新) 平均値マップを作成 (Numpy配列として計算)
            result_avg = (result_b + result_g + result_r) / 3.0
            
            # 平均値マップから最大スコアを探す
            _, max_val, _, max_loc = cv2.minMaxLoc(result_avg)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---

        else:
            # --- 通常モード (BGR または グレースケール) ---
            # (※ effective_strict_color=True の場合、core.py側で use_cl=False にセットされるため、
            #    ここでは screen_image, template_image_data が Numpy であることが期待される)
            result = cv2.matchTemplate(screen_image, template_image_data, cv2.TM_CCOEFF_NORMED) #
            _, max_val, _, max_loc = cv2.minMaxLoc(result) #

    except cv2.error as e:
        # cv2.split がグレースケール画像で呼ばれた場合や、
        # UMat/Numpyの型不一致などでエラーになった場合
        # [エラーログをLogger経由で出すのは困難なため、ここではNoneを返す]
        # print(f"[MatcherTask ERROR] Path: {path}, Error: {e}")
        return None #
    except Exception as e:
        # print(f"[MatcherTask FATAL] Path: {path}, Error: {e}")
        return None #
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    # 一致度が閾値以上であれば、結果を辞書として返す
    if max_val >= threshold: #
        return {
            'path': path,
            'settings': settings,
            'location': max_loc,
            'confidence': max_val,
            'scale': scale,
            'rect': (max_loc[0], max_loc[1], max_loc[0] + t_w, max_loc[1] + t_h)
        }
    
    # 閾値未満ならNoneを返す
    return None #
