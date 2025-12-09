import sys
import shutil
import subprocess
from PySide6.QtWidgets import QInputDialog, QLineEdit

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
    return QInputDialog.getText(parent, title, prompt, QLineEdit.Normal, initial_text)
