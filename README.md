The multi-language version of Imeck15_v1.5.5.6 (exe/Bin) for your OS can be downloaded from the bottom of the [releases page](https://github.com/ashguine-svg/Imeck15/releases).  
适用于您操作系统的 Imeck15_v1.5.5.6 多语言版本 (exe/Bin) 可从 [Releases 页面](https://github.com/ashguine-svg/Imeck15/releases) 底部下载。  
사용하시는 OS에 맞는 Imeck15_v1.5.5.6 다국어 버전 (exe/Bin)은 [Releases 페이지](https://github.com/ashguine-svg/Imeck15/releases) 하단에서 다운로드하실 수 있습니다。  
La versión multiidioma de Imeck15_v1.5.5.6 (exe/Bin) para su sistema operativo puede descargarse desde la parte inferior de la [página de Releases](https://github.com/ashguine-svg/Imeck15/releases).  
आपके ऑपरेटिंग सिस्टम के लिए Imeck15_v1.5.5.6 मल्टी-लैंग्वेज संस्करण (exe/Bin) [Releases पृष्ठ](https://github.com/ashguine-svg/Imeck15/releases) के निचले भाग से डाउनलोड किया जा सकता है।  
يمكنكم تحميل النسخة متعددة اللغات من Imeck15_v1.5.5.5 (exe/Bin) الخاصة بنظام التشغيل لديكم من أسفل [صفحة الإصدارات (Releases)](https://github.com/ashguine-svg/Imeck15/releases).  
Die mehrsprachige Version Imeck15_v1.5.5.6 (exe/Bin) für Ihr Betriebssystem kann unten auf der [Releases-Seite](https://github.com/ashguine-svg/Imeck15/releases) heruntergeladen werden.  
Мультиязыковую версию Imeck15_v1.5.5.6 (exe/Bin) для вашей операционной системы можно скачать в нижней части [страницы релизов](https://github.com/ashguine-svg/Imeck15/releases).  
La version multi-langues de Imeck15_v1.5.5.6 (exe/Bin) pour votre OS peut être téléchargée en bas de la [page des versions](https://github.com/ashguine-svg/Imeck15/releases).  
La versione multilingue di Imeck15_v1.5.5.6 (exe/Bin) per il tuo OS può essere scaricata dal fondo della [pagina delle release](https://github.com/ashguine-svg/Imeck15/releases).  
Multisproget version af Imeck15_v1.5.5.6 (exe/Bin) til dit OS kan downloades fra bunden af [udgivelser siden](https://github.com/ashguine-svg/Imeck15/releases).  
De meertalige versie van Imeck15_v1.5.5.6 (exe/Bin) voor uw OS kan worden gedownload onderaan de [releases pagina](https://github.com/ashguine-svg/Imeck15/releases).  
Fler språklig versjon av Imeck15_v1.5.5.6 (exe/Bin) for ditt OS kan lastes ned fra bunnen av [utgivelsessiden](https://github.com/ashguine-svg/Imeck15/releases).  
Wielojęzyczną wersję Imeck15_v1.5.5.6 (exe/Bin) dla Twojego systemu operacyjnego można pobrać z dołu [strony wydań](https://github.com/ashguine-svg/Imeck15/releases).  
A versão multilíngue do Imeck15_v1.5.5.6 (exe/Bin) para o seu SO pode ser baixada na parte inferior da [página de lançamentos](https://github.com/ashguine-svg/Imeck15/releases).  
Imeck15_v1.5.5.6 (exe/Bin) monikielinen versio käyttöjärjestelmällesi on ladattavissa [julkaisusivun](https://github.com/ashguine-svg/Imeck15/releases) alaosasta. 
Den flerspråkiga versionen av Imeck15_v1.5.5.6 (exe/Bin) för ditt OS kan laddas ner från botten av [releases-sidan](https://github.com/ashguine-svg/Imeck15/releases).  
İşletim sisteminiz için Imeck15_v1.5.5.6 (exe/Bin) çok dilli sürümü, [sürümler sayfasının](https://github.com/ashguine-svg/Imeck15/releases) alt kısmından indirilebilir.  

# Imeck15 - 導入と使い方ガイド

このガイドでは、Imeck15をダウンロードし、お使いのPCで実行するまでの手順を初心者の方にも分かりやすく解説します。

*Pythonではなくexe/binが必要な方は[Releases](https://github.com/ashguine-svg/Imeck15/releases)より、あなたのOS用のものをDownloadしてください。
*ページ一番下にPythonからネイティブ言語にコンパイルしものがお使いになれます。

  - **Windows**
  - **Linux (MX Linux)**

お使いのOSに合わせて、それぞれの手順に従ってください。

-----

## 共通の準備：Pythonの導入

Imeck15はプログラミング言語「Python」で動作します。まずはお使いのPCにPythonをインストールしましょう。

  - **推奨バージョン:** Python 3.10 または 3.11

すでにインストール済みの方は、お使いのOSのセクションに進んでください。

-----

## 💻 Windows環境での導入手順

Windowsユーザー向けの手順です。

### ステップ1：Imeck15のダウンロード

プロジェクトファイルをPCにダウンロードします。方法は2つありますが、**Gitを使う方法が今後のアップデートに便利でおすすめです。**

>方法A：Gitを使ってダウンロードする（推奨）

1.  **Git for Windowsをインストール**
    もしPCにGitがなければ、[こちらのサイト](https://git-scm.com/download/win)からインストーラーをダウンロードしてインストールしてください。設定はすべてデフォルトのままで問題ありません。

2.  **コマンドプロンプトを開く**
    スタートメニューで「`cmd`」と検索し、「コマンドプロンプト」を起動します。

3.  **プロジェクトをダウンロード**
    以下のコマンドを実行すると、デスクトップに`Imeck15`フォルダが作成されます。

    ```powershell
    # デスクトップに移動
    cd %USERPROFILE%\Desktop

    # Imeck15をダウンロード
    git clone https://github.com/ashguine-svg/Imeck15
    ```

>方法B：ZIPファイルでダウンロードする

1.  GitHubプロジェクトページ右上の緑色の「**\<\> Code**」ボタンをクリックします。
2.  「**Download ZIP**」を選択します。
3.  ダウンロードしたZIPファイルを、デスクトップなどの分かりやすい場所で\*\*解凍（展開）\*\*してください。


### ステップ2：Pythonのインストール

1.  **Python公式サイトにアクセス**
    [python.org](https://www.python.org/downloads/windows/)にアクセスし、推奨バージョン（3.10や3.11）のインストーラーをダウンロードします。

2.  **インストール実行**
    インストーラーを起動し、**必ず一番下の「Add python.exe to PATH」にチェックを入れてから**、「Install Now」をクリックしてください。

    > **❗️重要:** このチェックを入れないと、コマンドプロンプトで`python`コマンドが使えず、次の手順に進めません。

### ステップ3：Imeck15の実行環境を準備する

コマンドプロンプトを使って、Imeck15を動かすための準備をします。

1.  **Imeck15フォルダに移動**
    コマンドプロンプトで、先ほどダウンロードしたImeck15のフォルダに移動します。

    ```powershell
    # デスクトップにダウンロードした場合
    cd %USERPROFILE%\Desktop\Imeck15
    ```

2.  **仮想環境の作成**
    プロジェクト専用の独立した環境を作ります。これにより、PC全体の環境を汚さずに済みます。

    ```powershell
    python -m venv venv
    ```

    フォルダ内に`venv`という新しいフォルダが作成されれば成功です。

3.  **仮想環境のアクティベート**
    作成した専用環境に入ります。

    ```powershell
    venv\Scripts\activate
    ```

    実行後、行の先頭に`(venv)`と表示されれば成功です。

4.  **必要なライブラリのインストール**
    Imeck15の動作に必要なツールをまとめてインストールします。

    ```powershell
    pip install -r requirements.txt
    ```

    > 💡 `requirements.txt`ファイルがない場合は、開発者に必要なライブラリの一覧を確認してください。（例: `pip install opencv-python Pillow PySide6`など）

### ステップ4：Imeck15の起動！ 🎉

これで準備は完了です！以下のコマンドでImeck15を起動します。

```powershell
python main.py
```

-----

## 🐧 Linux (MX Linux) 環境での導入手順

MX Linuxユーザー向けの手順です。

### ステップ1：必要なツールのインストール

まず、ターミナル（端末）を開き、Imeck15の導入に必要なツールをインストールします。

```bash
# パッケージリストを更新
sudo apt update

# 必要なツールをまとめてインストール
sudo apt install git python3 python3-pip python3-venv -y
```

### ステップ2：Imeck15のダウンロード

ターミナルで以下のコマンドを実行し、ホームディレクトリにImeck15をダウンロードします。

```bash
# ホームディレクトリに移動
cd ~

# Imeck15をダウンロード
git clone https://github.com/ashguine-svg/Imeck15
```

※ `ashguine-svg` の部分は、実際のプロジェクトのURLに合わせて変更してください。

### ステップ3：Imeck15の実行環境を準備する

1.  **Imeck15フォルダに移動**
    ターミナルで、ダウンロードしたImeck15フォルダに移動します。

    ```bash
    cd ~/Imeck15
    ```

2.  **仮想環境の作成**
    プロジェクト専用の独立した環境を作ります。

    ```bash
    python3 -m venv venv
    ```

3.  **仮想環境のアクティベート**
    作成した専用環境に入ります。

    ```bash
    source venv/bin/activate
    ```

    実行後、行の先頭に`(venv)`と表示されれば成功です。

4.  **必要なライブラリのインストール**
    Imeck15の動作に必要なツールをまとめてインストールします。

    ```bash
    pip install -r requirements.txt
    ```

    > 💡 `requirements.txt`ファイルがない場合は、開発者に必要なライブラリの一覧を確認してください。（例: `pip install opencv-python Pillow PySide6`など）

### ステップ4：Imeck15の起動！ 🎉

これで準備は完了です！以下のコマンドでImeck15を起動します。

```bash
python3 main.py
```

-----

## 🚀 次回からの起動方法

一度準備が完了すれば、次回からは簡単な手順で起動できます。

1.  **コマンドプロンプト（またはターミナル）を開く**
2.  **Imeck15フォルダに移動する**
      * Windows: `cd %USERPROFILE%\Desktop\Imeck15`
      * Linux: `cd ~/Imeck15`
3.  **仮想環境をアクティベートする**
      * Windows: `venv\Scripts\activate`
      * Linux: `source venv/bin/activate`
4.  **Imeck15を起動する**
      * Windows: `python main.py`
      * Linux: `python3 main.py`

## 💡 基本的な操作

  - **監視開始**: `右ダブルクリック` または `監視開始`ボタン
  - **監視停止**: `右クリック` または `監視停止`ボタン

詳しい使い方はアプリケーション内のUIをご覧ください。

## 🔧 トラブルシューティング

  - **`python`や`git`コマンドが見つからないと言われる (Windows)**

      * **原因:** インストール時に「PATHを通す」設定がされていない可能性があります。
      * **対策:** PythonやGit for Windowsを再インストールし、必ず「Add to PATH」のチェックを入れてください。

  - **`ModuleNotFoundError: No module named '...'` と表示される**

      * **原因:** 仮想環境がアクティベートされていないか、ライブラリのインストールが正常に完了していません。
      * **対策:** コマンドプロンプト（ターミナル）で行の先頭に`(venv)`と表示されているか確認してください。表示されていない場合は、アクティベートのコマンドを再実行してから`pip install -r requirements.txt`をもう一度実行してみてください。

-----

## ⚙️ モジュール構成図

このアプリケーションのモジュール構成は、Google の AI、**Gemini** とのペアプログラミングを通じて洗練されました。
各ファイルがどのような役割を持ち、どのように連携しているかを示した図です。

```mermaid
graph TD
    subgraph UILayer
        A[main.py] -- "起動" --> B[ui.py]
        B -- "使用" --> B1[image_tree_widget.py]
        B -- "使用" --> B2[preview_mode_manager.py]
        B -- "使用" --> B3[floating_window.py]
        B -- "使用" --> B4[dialogs.py]
    end

    subgraph CoreLogicLayer
        C(core.py) --- C1(monitoring_states.py)
        C --- D(template_manager.py)
        C --- E(matcher.py)
        C --- F(action.py)
    end

    subgraph HardwareSystemInteraction
        G[capture.py]
        F --- H[Mouse/Keyboard]
        G --- I[Screen]
    end

    subgraph DataConfiguration
        J[config.py]
        L[locale_manager.py]
        K[画像ファイル & 設定JSON]

        D --- J
        C --- J
        B -- "設定読み込み" --> J
        B -- "多言語" --> L
        C -- "多言語" --> L
        J --- K
    end

    %% Connections
    B -- "操作イベント (開始/停止/設定変更)" --> C
    C -- "UI更新指示 (ログ/プレビュー/ステータス)" --> B

    C -- "画面キャプチャ要求" --> G
    G -- "キャプチャ画像" --> C

    C -- "テンプレートマッチング実行" --> E
    E -- "マッチング結果" --> C

    C -- "クリック/操作実行" --> F
    
    C -- "キャッシュ構築要求" --> D

    C1 -.-> C
```
### 各ファイルの機能説明

| レイヤー | ファイル名 | 担当する処理 |
| :--- | :--- | :--- |
| **UI Layer** | **`main.py`** | **起動ファイル。** アプリケーションを起動し、`UIManager` や `CoreEngine` などの主要コンポーネントを初期化して接続します。|
| | **`ui.py` (UIManager)** | **メインウィンドウ（器）。** アプリ全体のレイアウト、タブ、ボタンなどを管理します。各モジュール（`image_tree_widget` や `preview_mode_manager`）を組み込み、`CoreEngine` との信号を中継します。|
| | **`image_tree_widget.py`** | **画像ツリー専門家。** `ui.py` から分離されました。画像のリスト表示と、フォルダへのD&D（ドラッグ＆ドロップ）操作や並べ替えといった複雑なロジックを専門に担当します。|
| | **`preview_mode_manager.py`** | **画像設定プレビュー専門家。** `ui.py` から分離されました。画像プレビューの表示、クリック位置（1点/範囲）やROIの描画・設定ロジックをすべて管理します。|
| | `floating_window.py` / `dialogs.py` | 最小化UIや各種ポップアップダイアログなど、サブのUIコンポーネントです。 |
| **Core Logic** | **`core.py`** | **アプリケーションの頭脳・司令塔。** 全体の動作を制御します。UIからの指示を受けて監視を開始/停止し、各専門モジュール（`capture`, `matcher`など）に必要な処理を命令し、結果をUIに伝達します。 |
| | **`monitoring_states.py`** | **監視モードの専門家。** `core.py`の監視ループの複雑なロジックを担当。「通常監視」「優先モード」「バックアップカウントダウン」といった各状態の振る舞いを個別のクラスとして定義し、状態遷移を管理します。 |
| | **`template_manager.py`** | **テンプレート画像の準備係。** 登録された画像ファイルを読み込み、設定（拡大率、ROIなど）に基づいて、画像認識に使用するためのデータ（キャッシュ）をメモリ上に準備します。 |
| | **`matcher.py`** | **画像認識の実行役。** `core.py`から渡された画面のキャプチャ画像と、`template_manager`が準備したテンプレート画像を比較し、一致する箇所と信頼度を計算して結果を返します。 |
| | **`action.py`** | **PC操作の実行役。** `core.py`からの指示に基づき、実際にマウスカーソルを動かしてクリックしたり、ウィンドウをアクティブにしたりといったPC操作を実行します。 |
| **Hardware** | **`capture.py`** | **画面のカメラマン。** PCのスクリーンや特定のウィンドウの画像を撮影（キャプチャ）する処理を担当します。`core.py`の要求に応じて、リアルタイムの画面イメージを提供します。 |
| **Data** | **`config.py`** | **設定情報の管理人。** 登録された画像へのパス、各画像の詳細設定（クリック座標、閾値など）、アプリ全体の動作設定などをファイル（JSONなど）から読み書きする役割を担います。 |
| | **`locale_manager.py`** | **多言語の専門家。** アプリケーション内のテキスト（UI、ログ）を管理し、`locales` フォルダ内の `ja_JP.json` などのファイルから適切な言語に翻訳します。 |
