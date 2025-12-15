# translation_updater.py
#
# 翻訳JSONファイル一括更新ツール
# 
# 使い方:
# 1. このスクリプトを実行します。
# 2. 「指示書CSVを選択」で、ステップ1で作成したCSVファイルを選びます。
# 3. 「localesフォルダを選択」で、en_US.json などが格納されているフォルダを選びます。
# 4. 「実行」ボタンを押すと、処理が開始されます。

import sys
import csv
import json
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QTextEdit, QLabel, QLineEdit
)
from PySide6.QtCore import Qt

class TranslationUpdater(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻訳ファイル一括更新ツール")
        self.setMinimumSize(600, 400)

        # --- ファイルパス入力欄 ---
        self.csv_path_edit = QLineEdit()
        self.csv_path_edit.setPlaceholderText("指示書CSVファイル (.csv)")
        self.csv_path_edit.setReadOnly(True)
        
        self.locales_dir_edit = QLineEdit()
        self.locales_dir_edit.setPlaceholderText("翻訳フォルダ (locales)")
        self.locales_dir_edit.setReadOnly(True)

        self.csv_button = QPushButton("指示書CSVを選択")
        self.locales_button = QPushButton("localesフォルダを選択")
        
        self.run_button = QPushButton("実行")
        self.run_button.setStyleSheet("font-weight: bold; padding: 5px;")
        
        # --- ログ表示 ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("ここに処理結果が表示されます...")

        # --- シグナル接続 ---
        self.csv_button.clicked.connect(self.select_csv_file)
        self.locales_button.clicked.connect(self.select_locales_dir)
        self.run_button.clicked.connect(self.process_files)

        # --- レイアウト ---
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        path_layout = QVBoxLayout()
        csv_layout = QHBoxLayout()
        csv_layout.addWidget(QLabel("1. 指示書:"))
        csv_layout.addWidget(self.csv_path_edit)
        csv_layout.addWidget(self.csv_button)
        
        locales_layout = QHBoxLayout()
        locales_layout.addWidget(QLabel("2. フォルダ:"))
        locales_layout.addWidget(self.locales_dir_edit)
        locales_layout.addWidget(self.locales_button)
        
        path_layout.addLayout(csv_layout)
        path_layout.addLayout(locales_layout)
        
        layout.addLayout(path_layout)
        layout.addWidget(self.run_button)
        layout.addWidget(QLabel("処理ログ:"))
        layout.addWidget(self.log_area, 1) # 伸縮するログエリア

    def select_csv_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "指示書CSVファイルを選択", "", "CSV Files (*.csv)"
        )
        if file_path:
            self.csv_path_edit.setText(file_path)
            self.log_to_area(f"指示書ファイルを設定: {file_path}")

    def select_locales_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "翻訳フォルダ (locales) を選択"
        )
        if dir_path:
            self.locales_dir_edit.setText(dir_path)
            self.log_to_area(f"翻訳フォルダを設定: {dir_path}")

    def log_to_area(self, message):
        self.log_area.append(message)
        print(message)

    def process_files(self):
        csv_path_str = self.csv_path_edit.text()
        locales_dir_str = self.locales_dir_edit.text()

        if not csv_path_str or not locales_dir_str:
            self.log_to_area("[エラー] 指示書CSVとlocalesフォルダの両方を選択してください。")
            return

        csv_path = Path(csv_path_str)
        locales_dir = Path(locales_dir_str)

        if not csv_path.exists():
            self.log_to_area(f"[エラー] 指示書ファイルが見つかりません: {csv_path}")
            return
        if not locales_dir.is_dir():
            self.log_to_area(f"[エラー] 翻訳フォルダが見つかりません: {locales_dir}")
            return

        self.log_area.clear()
        self.log_to_area("--- 処理開始 ---")

        try:
            # 1. 指示書(CSV)を読み込む
            with open(csv_path, mode='r', encoding='utf-8-sig') as f:
                # 'utf-8-sig' は BOM 付きCSVに対応するため
                reader = csv.DictReader(f)
                tasks = list(reader) # 全タスクをリストとして読み込む

            # 2. タスクをファイル名ごとにグループ化する
            #    これにより、1つのjsonファイルに対する読み書きを1回にまとめる
            grouped_tasks = {}
            for task in tasks:
                filename = task.get('filename')
                if not filename:
                    continue
                
                if filename not in grouped_tasks:
                    grouped_tasks[filename] = []
                grouped_tasks[filename].append(task)

            # 3. ファイルごとに処理を実行
            for filename, file_tasks in grouped_tasks.items():
                json_path = locales_dir / filename
                
                if not json_path.exists():
                    self.log_to_area(f"[警告] スキップ: {filename} がフォルダ内に見つかりません。")
                    continue

                self.log_to_area(f"--- {filename} を処理中 ---")
                
                try:
                    # 3-1. JSONファイルを読み込む (順序を保持)
                    with open(json_path, 'r', encoding='utf-8') as f:
                        # json.load は Python 3.7+ で挿入順序を保持する dict を返します
                        data = json.load(f)

                    modified = False
                    
                    # 3-2. ファイルに対するタスクを実行
                    for task in file_tasks:
                        operation = task.get('operation', '').upper()
                        key = task.get('key')
                        value = task.get('value')

                        if not key:
                            self.log_to_area(f" [失敗] キーが指定されていません。{task}")
                            continue

                        if operation == 'UPDATE':
                            if key in data:
                                if data[key] != value:
                                    data[key] = value
                                    self.log_to_area(f" [更新] {key} = {value[:30]}...")
                                    modified = True
                                else:
                                    self.log_to_area(f" [情報] スキップ: {key} は既に同じ値です。")
                            else:
                                self.log_to_area(f" [失敗] UPDATE失敗: {key} が見つかりません。")
                        
                        elif operation == 'ADD':
                            if key not in data:
                                data[key] = value
                                self.log_to_area(f" [追加] {key} = {value[:30]}...")
                                modified = True
                            else:
                                self.log_to_area(f" [失敗] ADD失敗: {key} は既に存在します。")
                        
                        else:
                             self.log_to_area(f" [失敗] 不明な操作 '{operation}' です。")

                    # 3-3. 変更があれば書き込む
                    if modified:
                        with open(json_path, 'w', encoding='utf-8') as f:
                            # ensure_ascii=False で日本語などをそのまま書き込む
                            # indent=2 で元のファイルと同じフォーマットを維持
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        self.log_to_area(f"--- {filename} を保存しました ---")
                    else:
                        self.log_to_area(f"--- {filename} に変更はありませんでした ---")

                except json.JSONDecodeError:
                    self.log_to_area(f"[エラー] {filename} のJSONパースに失敗しました。")
                except Exception as e:
                    self.log_to_area(f"[エラー] {filename} の処理中に予期せぬエラー: {e}")

            self.log_to_area("\n--- すべての処理が完了しました ---")

        except Exception as e:
            self.log_to_area(f"[致命的エラー] 処理が中断されました: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TranslationUpdater()
    window.show()
    sys.exit(app.exec())
