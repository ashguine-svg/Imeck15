import sys
import shutil
import subprocess
from PySide6.QtWidgets import QInputDialog, QLineEdit
from PySide6.QtCore import Qt

def ask_string_custom(parent, title, prompt, initial_text=""):
    # Linux かつ zenity がある場合
    if sys.platform == 'linux' and shutil.which("zenity"):
        try:
            cmd = [
                "zenity", "--entry",
                f"--title={title}",
                f"--text={prompt}",
                f"--entry-text={initial_text}"
            ]
            # OS標準ツールなので同期実行(subprocess.run)でもフリーズしない
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            
            if result.returncode == 0:
                return result.stdout.strip(), True
            else:
                return "", False
        except Exception:
            pass # 失敗したら下のQt標準へ

    # Windows/Mac または Zenityがない場合のフォールバック
    # 親ウィンドウを前面に表示してからダイアログを表示
    if parent:
        parent.showNormal()
        parent.raise_()
        parent.activateWindow()
        # イベントを処理して前面表示を確実にする
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
    
    # QInputDialog はモーダルなので、親を前面に表示すればダイアログも前面に表示される
    result = QInputDialog.getText(parent, title, prompt, QLineEdit.Normal, initial_text)
    
    # ダイアログが閉じられた後も親ウィンドウを前面に保つ
    if parent:
        parent.raise_()
        parent.activateWindow()
    
    return result
