# Linux Input Method Support / Linuxでの入力メソッド対応

This application automatically detects your system's IME (Fcitx5 / IBus).
If you cannot type in your language, please install the Qt6 IME plugin for your distribution.

本アプリはシステムの入力メソッド (Fcitx5 / IBus) を自動認識します。
もし母国語での入力ができない場合は、以下の手順に従ってQt6用プラグインをインストールしてください。

---

## 🇺🇸 English (US) / General
**If you are unable to switch input methods or type special characters:**
Usually, no action is needed. However, if you use IBus or Fcitx for layout switching and it doesn't work, install the Qt6 plugin.

* **Ubuntu / Debian:**
    ```bash
    sudo apt install fcitx5-frontend-qt6
    # or for IBus:
    sudo apt install ibus-qt6
    ```
* **Arch Linux:**
    ```bash
    sudo pacman -S fcitx5-qt
    # or for IBus:
    sudo pacman -S ibus
    ```
* **Fedora:**
    ```bash
    sudo dnf install fcitx5-qt6
    # or for IBus:
    sudo dnf install ibus-qt6
    ```

---

## 🇯🇵 日本語 (Japanese)
**日本語入力ができない場合:**
Google Chrome等で入力できている環境であれば、通常は設定不要です。もし入力できない場合は、お使いの環境に合わせて以下のコマンドを実行してください。

* **Ubuntu / Debian / Linux Mint:**
    ```bash
    # Fcitx5の場合 (推奨)
    sudo apt install fcitx5-frontend-qt6
    # IBusの場合
    sudo apt install ibus-qt6
    ```
* **Arch Linux / Manjaro:**
    ```bash
    # Fcitx5の場合
    sudo pacman -S fcitx5-qt
    # IBusの場合
    sudo pacman -S ibus
    ```
* **Fedora:**
    ```bash
    # Fcitx5の場合
    sudo dnf install fcitx5-qt6
    # IBusの場合
    sudo dnf install ibus-qt6
    ```

---

## 🇨🇳 简体中文 (Simplified Chinese)
**无法输入中文时:**
如果您的 Google Chrome 已经可以输入中文，通常无需进行额外设置。如果本程序无法输入中文，请根据您的 Linux 发行版安装相应的 Qt6 插件。

* **Ubuntu / Debian / Deepin:**
    ```bash
    # 使用 Fcitx5 (推荐)
    sudo apt install fcitx5-frontend-qt6
    # 使用 IBus
    sudo apt install ibus-qt6
    ```
* **Arch Linux / Manjaro:**
    ```bash
    # 使用 Fcitx5
    sudo pacman -S fcitx5-qt
    # 使用 IBus
    sudo pacman -S ibus
    ```
* **Fedora:**
    ```bash
    # 使用 Fcitx5
    sudo dnf install fcitx5-qt6
    # 使用 IBus
    sudo dnf install ibus-qt6
    ```

---

## 🇰🇷 한국어 (Korean)
**한글 입력이 안 되는 경우:**
다른 응용 프로그램(예: Chrome)에서 한글 입력이 가능하다면, 일반적으로 설정이 필요하지 않습니다. 만약 본 프로그램에서 한글 입력이 되지 않는다면, 사용 중인 배포판에 맞춰 다음 명령어를 실행하여 Qt6 플러그인을 설치해 주세요.

* **Ubuntu / Debian:**
    ```bash
    # Fcitx5 (kime/nimf 등을 사용하는 경우 해당 문서를 참고하세요)
    sudo apt install fcitx5-frontend-qt6
    # IBus
    sudo apt install ibus-qt6
    ```
* **Arch Linux:**
    ```bash
    # Fcitx5
    sudo pacman -S fcitx5-qt
    # IBus
    sudo pacman -S ibus
    ```
* **Fedora:**
    ```bash
    # Fcitx5
    sudo dnf install fcitx5-qt6
    # IBus
    sudo dnf install ibus-qt6
    ```

---

## 🇮🇳 हिन्दी (Hindi)
**यदि आप हिंदी टाइप नहीं कर पा रहे हैं:**
यह एप्लिकेशन आपके सिस्टम के IME (Fcitx5 / IBus) का स्वचालित रूप से पता लगाता है। यदि इनपुट काम नहीं कर रहा है, तो कृपया अपने लिनक्स वितरण (distribution) के लिए Qt6 प्लगइन स्थापित करें।

* **Ubuntu / Debian:**
    ```bash
    sudo apt install ibus-qt6
    # या Fcitx5 के लिए:
    sudo apt install fcitx5-frontend-qt6
    ```
* **Arch Linux:**
    ```bash
    sudo pacman -S ibus
    # या Fcitx5 के लिए:
    sudo pacman -S fcitx5-qt
    ```

---

## 🇸🇦 العربية (Arabic)
**في حال عدم القدرة على الكتابة باللغة العربية:**
يكتشف هذا التطبيق تلقائيًا نظام الإدخال (Fcitx5 / IBus). إذا كنت تواجه مشكلة في الكتابة، يرجى تثبيت إضافة Qt6 المناسبة لتوزيعة لينكس الخاصة بك.

* **Ubuntu / Debian:**
    ```bash
    sudo apt install ibus-qt6
    # أو لـ Fcitx5:
    sudo apt install fcitx5-frontend-qt6
    ```
* **Arch Linux:**
    ```bash
    sudo pacman -S ibus
    # أو لـ Fcitx5:
    sudo pacman -S fcitx5-qt
    ```

---

## 🇷🇺 Русский (Russian)
**Если не переключается раскладка клавиатуры:**
Обычно ввод работает стандартно. Однако, если вы используете менеджеры ввода (IBus/Fcitx) для переключения раскладки и это не работает, установите соответствующий плагин Qt6.

* **Ubuntu / Debian:** `sudo apt install ibus-qt6`
* **Arch Linux:** `sudo pacman -S ibus`
* **Fedora:** `sudo dnf install ibus-qt6`
