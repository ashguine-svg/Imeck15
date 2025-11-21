# matcher.py
# ★★★ (最適化版) 無駄なGPU転送を排除し、型不一致対策とMinロジックを搭載 ★★★

import cv2
from PIL import Image
import imagehash
import numpy as np

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

def _match_template_task(screen_image, template_image, template_data, screen_shape, template_shape, effective_strict_color: bool):
    """
    画面イメージとテンプレートイメージのマッチングを行うタスク。
    (色調厳格モード対応・パフォーマンス最適化済み)
    
    Args:
        screen_image: 検索対象の画面イメージ (BGR or Gray, UMat or Numpy)。
        template_image: テンプレートイメージ (BGR or Gray, UMat or Numpy)。
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
        if effective_strict_color:
            # --- 色調厳格モード (BGRが前提) ---
            # ★ パフォーマンス修正: 必要な場合のみGPUからCPUへ転送する
            screen_np = screen_image.get() if hasattr(screen_image, 'get') else screen_image
            template_np = template_image_data.get() if hasattr(template_image_data, 'get') else template_image_data
            
            # 安全性チェック: データがNone、または3チャンネル(カラー)でない場合は実行不可
            if screen_np is None or template_np is None:
                return None
            if len(screen_np.shape) < 3 or screen_np.shape[2] != 3:
                # グレースケール画像などが渡された場合は厳格モード不可
                return None

            # チャンネル分離
            s_b, s_g, s_r = cv2.split(screen_np)
            t_b, t_g, t_r = cv2.split(template_np)
            
            # 3回マッチング
            result_b = cv2.matchTemplate(s_b, t_b, cv2.TM_CCOEFF_NORMED)
            result_g = cv2.matchTemplate(s_g, t_g, cv2.TM_CCOEFF_NORMED)
            result_r = cv2.matchTemplate(s_r, t_r, cv2.TM_CCOEFF_NORMED)
            
            # --- 最小値(Min)ロジック ---
            # 全てのチャンネルで一致度が高い場所のみを採用する（厳しい判定）
            # ノイズに弱くなるため、閾値を少し下げる運用が推奨される
            result_min = cv2.min(result_b, cv2.min(result_g, result_r))
            
            # 最小値マップから最大スコアを探す
            _, max_val, _, max_loc = cv2.minMaxLoc(result_min)

        else:
            # --- 通常モード (BGR または グレースケール) ---
            
            # ★ 安全性修正: 型不一致 (UMat vs Numpy) によるエラーを防ぐ
            is_screen_umat = isinstance(screen_image, cv2.UMat)
            is_template_umat = isinstance(template_image_data, cv2.UMat)

            if is_screen_umat and not is_template_umat:
                # スクリーンがUMatでテンプレートがNumpyなら、スクリーンをNumpy化して合わせる
                screen_image = screen_image.get()
            elif not is_screen_umat and is_template_umat:
                # その逆ならテンプレートをNumpy化して合わせる
                template_image_data = template_image_data.get()

            # 通常のマッチング実行
            result = cv2.matchTemplate(screen_image, template_image_data, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

    except cv2.error as e:
        # cv2.split がグレースケール画像で呼ばれた場合や、
        # 型不一致などでエラーになった場合の安全策
        return None
    except Exception as e:
        return None

    # 一致度が閾値以上であれば、結果を辞書として返す
    if max_val >= threshold:
        return {
            'path': path,
            'settings': settings,
            'location': max_loc,
            'confidence': max_val,
            'scale': scale,
            'rect': (max_loc[0], max_loc[1], max_loc[0] + t_w, max_loc[1] + t_h)
        }
    
    # 閾値未満ならNoneを返す
    return None
