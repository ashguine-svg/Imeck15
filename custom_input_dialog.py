import sys
import shutil
import subprocess
from PySide6.QtWidgets import QInputDialog, QLineEdit
from PySide6.QtCore import Qt

def ask_string_custom(parent, title, prompt, initial_text=""):
    # Linux かつ zenity がある場合
    if sys.platform == 'linux' and shutil.which("zenity"):
        try:
            # ★★★ 修正: Zenityにフォーカス処理を任せる（親ウィンドウの前面表示処理は不要） ★★★
            cmd = [
                "zenity", "--entry",
                f"--title={title}",
                f"--text={prompt}",
                f"--entry-text={initial_text}"
            ]
            # OS標準ツールなので同期実行(subprocess.run)でもフリーズしない
            # Zenityは自動的に前面に表示され、フォーカスも適切に処理される
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            
            if result.returncode == 0:
                return result.stdout.strip(), True
            else:
                return "", False
        except Exception:
            pass # 失敗したら下のQt標準へ

    # Windows/Mac または Zenityがない場合のフォールバック
    # ★★★ 修正: 親ウィンドウの子にしないことで、WindowStaysOnTopHintの影響を受けないようにする ★★★
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QDialogButtonBox, QApplication
    from PySide6.QtCore import Qt
    
    # ★★★ 修正: タイマー設定UIと同じ表示方法にする（parentを指定して通常のダイアログとして作成） ★★★
    dialog = QDialog(parent)  # parentを指定して通常のダイアログとして作成
    dialog.setWindowTitle(title)
    dialog.setWindowFlags(Qt.Dialog)
    dialog.setModal(True)
    
    layout = QVBoxLayout(dialog)
    label = QLabel(prompt)
    layout.addWidget(label)
    
    line_edit = QLineEdit(initial_text)
    layout.addWidget(line_edit)
    
    button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)
    
    # ダイアログのサイズを調整（レイアウトに基づいて自動調整）
    dialog.adjustSize()
    
    # 親ウィンドウの位置を取得してダイアログを中央に配置
    if parent:
        parent_pos = parent.pos()
        parent_size = parent.size()
        dialog_size = dialog.size()
        # 親ウィンドウの中央に配置
        dialog.move(
            parent_pos.x() + (parent_size.width() - dialog_size.width()) // 2,
            parent_pos.y() + (parent_size.height() - dialog_size.height()) // 2
        )
    
    # ダイアログを前面に表示
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    QApplication.processEvents()
    
    # フォーカスを入力フィールドに設定
    line_edit.setFocus()
    line_edit.selectAll()
    
    if dialog.exec() == QDialog.Accepted:
        return line_edit.text(), True
    else:
        return "", False
