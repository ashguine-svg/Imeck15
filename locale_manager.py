# locale_manager.py

import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QObject, Signal

class LocaleManager(QObject):
    """
    JSONファイルから翻訳文字列を読み込み、管理するクラス。
    """

    # 言語が変更されたことをUIに通知するためのシグナル
    languageChanged = Signal()

    def __init__(self, default_lang='ja_JP', locales_dir='locales'):
        """
        LocaleManagerを初期化します。

        Args:
            default_lang (str): デフォルトで使用する言語コード ('ja_JP'など)。
            locales_dir (str): 翻訳ファイルが格納されているフォルダ名。
        """
        super().__init__()

        # アプリケーションのベースパスを取得 (実行ファイル or スクリプト)
        if getattr(sys, 'frozen', False):
            # .exe (PyInstaller) として実行されている場合
            base_path = Path(os.path.dirname(sys.executable))
        else:
            # .py スクリプトとして実行されている場合
            try:
                # __file__ が利用可能な場合 (通常のスクリプト実行)
                base_path = Path(os.path.dirname(os.path.abspath(__file__)))
            except NameError:
                # __file__ が定義されていない場合 (インタラクティブシェルなど)
                base_path = Path.cwd()

        self.locales_dir = base_path / locales_dir

        self.translations = {}
        self.current_lang = None
        # アプリケーション起動時にデフォルト言語を読み込む
        self.load_locale(default_lang)

    def load_locale(self, lang_code: str):
        """
        指定された言語コードに対応するJSONファイルを読み込み、
        翻訳データを更新します。

        Args:
            lang_code (str): 'ja_JP' や 'en_US' などの言語コード。
        """
        file_path = self.locales_dir / f"{lang_code}.json"

        if not file_path.exists():
            print(f"[WARN] 翻訳ファイルが見つかりません: {file_path}")
            # もしまだ何も言語が読み込めていない状態で、
            # かつデフォルト(ja_JP)ファイルが存在すればそれを読み込む試み
            if self.current_lang is None and lang_code != 'ja_JP':
                default_file_path = self.locales_dir / "ja_JP.json"
                if default_file_path.exists():
                    print("[INFO] デフォルト言語 'ja_JP' を読み込みます。")
                    self.load_locale('ja_JP') # 再帰呼び出しで日本語を読み込む
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.translations = json.load(f)
            self.current_lang = lang_code
            print(f"[INFO] 言語ファイルを読み込みました: {lang_code}")

            # UIに変更を通知するためのシグナルを発行
            self.languageChanged.emit()

        except json.JSONDecodeError as e:
            print(f"[ERROR] 翻訳ファイルの解析に失敗: {file_path}, {e}")
        except Exception as e:
            print(f"[ERROR] 翻訳ファイルの読み込みエラー: {e}")

    def tr(self, key: str, *args):
        """
        指定されたキーに対応する翻訳文字列を取得します。
        キーが見つからない場合はキー自体を返します。
        可変長引数 args があれば、取得した文字列をフォーマットします。

        Args:
            key (str): JSONファイル内の翻訳キー。
            *args: 文字列フォーマット (%s, %d など) のための引数。

        Returns:
            str: 翻訳された(またはフォーマットされた)文字列。
                 キーが見つからない場合やフォーマットに失敗した場合は、
                 エラーを出さずにキーまたは元の文字列を返します。
        """
        # 辞書からキーに対応する値を取得。見つからなければキー自体をデフォルト値とする
        value = self.translations.get(key, key)

        try:
            # 引数 args が提供されていれば、文字列フォーマットを試みる
            if args:
                return value % args
            # 引数がなければ、取得した文字列をそのまま返す
            return value
        except TypeError:
            # 文字列フォーマットに失敗した場合 (例: % の数が合わない)
            print(f"[WARN] 翻訳キー '{key}' のフォーマットに失敗しました。 Args: {args}")
            # エラーは出さずに、とりあえずフォーマット前の文字列を返す
            return value
