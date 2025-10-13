# matcher.py

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

def _match_template_task(screen_image, template_data, screen_shape, template_shape):
    """
    画面イメージとテンプレートイメージのマッチングを行うタスク。
    ThreadPoolExecutorのワーカースレッドで実行されることを想定しています。

    Args:
        screen_image: 検索対象の画面イメージ。
        template_data: テンプレート画像と設定情報を含む辞書。
        screen_shape: 画面イメージの高さと幅 (h, w)。
        template_shape: テンプレートイメージの高さと幅 (h, w)。

    Returns:
        一致度が閾値を超えた場合にマッチ情報を含む辞書、それ以外は None。
    """
    path = template_data['path']
    settings = template_data['settings']
    template_image = template_data['template']
    scale = template_data['scale'] 
    
    threshold = settings.get('threshold', 0.8)

    s_h, s_w = screen_shape
    t_h, t_w = template_shape

    # テンプレートがスクリーンより大きい場合はマッチング不可能
    if t_h > s_h or t_w > s_w:
        return None

    # テンプレートマッチング実行
    result = cv2.matchTemplate(screen_image, template_image, cv2.TM_CCOEFF_NORMED)
    
    # 結果から最も一致度の高い場所とその値を取得
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

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
