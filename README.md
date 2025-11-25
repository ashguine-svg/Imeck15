-----

# Imeck15

**Automated Image Recognition Clicker for PC Games & Apps**

Imeck15 is a powerful automation tool that recognizes specific images on your screen and automatically clicks them. It is designed to automate routine tasks with high precision and low CPU usage.

-----

### 🌍 Multilingual Support & Download

**Imeck15 supports 19 languages:**
English, 日本語 (Japanese), 简体中文 (Chinese), 한국어 (Korean), Español (Spanish), हिन्दी (Hindi), العربية (Arabic), Deutsch (German), Русский (Russian), Français (French), Italiano (Italian), Dansk (Danish), Nederlands (Dutch), Norsk (Norwegian), Polski (Polish), Português (Portuguese), Suomi (Finnish), Svenska (Swedish), Türkçe (Turkish).

> **📥 Download Executable (.exe)**
>
> If you do not wish to run from Python source code, please download the standalone executable for your OS from the **[Releases Page](https://github.com/ashguine-svg/Imeck15/releases)**.
>
> **The User Manual (in all supported languages) is included in the `locales` folder of the application.**

-----

## ✨ Key Features

Imeck15 goes beyond simple auto-clicking with advanced logic capabilities:

  * **📷 Image Recognition & Auto-Clicking:** Detects registered template images and clicks specific coordinates or random ranges.
  * **📂 Smart Folder Modes:** Create complex scenarios without coding by assigning modes to folders:
      * **Sequence Priority (Cyan):** Clicks images in a strict step-by-step order. Great for login bonuses or tutorials.
      * **Cooldown (Purple):** Pauses the entire folder for a set time after any image is clicked. Perfect for "Close Ad" buttons.
      * **Image Recognition Priority (Blue):** Exclusive mode that activates only when a specific screen (e.g., "Combat Mode") is detected.
      * **Timer Priority (Green):** Interrupts routine tasks periodically (e.g., every 30 minutes).
  * **🪟 App Context & Auto-Scale:**
      * Automatically filters the image list to show only items relevant to the active window.
      * Automatically recalculates image scaling if the game window is resized.
  * **🚀 Performance Optimized:**
      * **Lightweight Mode:** Drastically reduces CPU load by lowering capture resolution.
      * **Eco Mode:** Reduces scan frequency when idle.
      * **DXCam Support:** High-speed screen capture for Windows.

-----

## 🛠️ Installation & Usage Guide (Python Source)

This guide explains how to download the source code, install dependencies, and run Imeck15 on your computer.

### Prerequisites

  * **Python:** Version 3.10 or 3.11 is recommended.
  * **Git:** Recommended for downloading and updating the project.

Please follow the instructions for your specific Operating System below.

### 💻 Windows Installation

**Step 1: Download Imeck15**

We recommend using Git to download the project for easier updates.

1.  **Install Git:** Download and install from [git-scm.com](https://git-scm.com/download/win). Default settings are fine.
2.  **Open Command Prompt:** Press `Win + R`, type `cmd`, and press Enter.
3.  **Clone the repository:**
    ```powershell
    cd %USERPROFILE%\Desktop
    git clone https://github.com/ashguine-svg/Imeck15
    ```
    *(Alternatively, click the green **\<\> Code** button -\> **Download ZIP** and extract it.)*

**Step 2: Install Python**

1.  Go to [python.org](https://www.python.org/downloads/windows/) and download the installer for Python 3.10 or 3.11.
2.  **Important:** Check the box **"Add python.exe to PATH"** at the bottom of the installer before clicking "Install Now".

**Step 3: Setup Environment & Install Libraries**

1.  **Navigate to the folder:**
    ```powershell
    cd %USERPROFILE%\Desktop\Imeck15
    ```
2.  **Create a Virtual Environment:**
    ```powershell
    python -m venv venv
    ```
3.  **Activate the Virtual Environment:**
    ```powershell
    venv\Scripts\activate
    ```
    *(You should see `(venv)` appear at the start of your command line).*
4.  **Install Dependencies:**
    ```powershell
    pip install -r requirements_windows.txt
    ```

**Step 4: Run Imeck15 🚀**

```powershell
python main.py
```

-----

### 🐧 Linux (MX Linux / Ubuntu / Debian) Installation

**⚠️ Important: System Packages**
You **must** install `xdotool` and `xwininfo` for window detection.

**Step 1: Install System Tools**

```bash
sudo apt update
sudo apt install git python3 python3-pip python3-venv xdotool xwininfo -y
```

**Step 2: Download Imeck15**

```bash
cd ~
git clone https://github.com/ashguine-svg/Imeck15
```

**Step 3: Setup Environment & Install Libraries**

1.  **Navigate to the folder:**
    ```bash
    cd ~/Imeck15
    ```
2.  **Create a Virtual Environment:**
    ```bash
    python3 -m venv venv
    ```
3.  **Activate the Virtual Environment:**
    ```bash
    source venv/bin/activate
    ```
4.  **Install Dependencies:**
    ```bash
    pip install -r requirements_linux.txt
    ```

**Step 4: Run Imeck15 🚀**

```bash
python3 main.py
```

-----

## 💡 Basic Operations

  * **Start Monitoring:** Right-click (Triple Click) OR press the "Start" button.
  * **Stop Monitoring:** Right-click (Double Click) OR press the "Stop" button.

Refer to the **User Manual** (included in the app) for detailed usage instructions.

-----

## ⚙️ Architecture Diagram

The module structure of this application was refined through pair programming with Google's AI, **Gemini**.

```mermaid
graph TD
    subgraph UILayer
        A[main.py] -- "Launch" --> B[ui.py]
        B -- "Uses" --> B1[image_tree_widget.py]
        B -- "Uses" --> B2[preview_mode_manager.py]
        B -- "Uses" --> B3[floating_window.py]
        B -- "Uses" --> B4[dialogs.py]
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
        K[Images & Config JSON]

        D --- J
        C --- J
        B -- "Load Config" --> J
        B -- "Localization" --> L
        C -- "Localization" --> L
        J --- K
    end

    %% Connections
    B -- "Events (Start/Stop/Settings)" --> C
    C -- "UI Updates (Log/Preview/Status)" --> B

    C -- "Capture Request" --> G
    G -- "Captured Image" --> C

    C -- "Template Matching" --> E
    E -- "Match Result" --> C

    C -- "Execute Action" --> F
    
    C -- "Build Cache Request" --> D

    C1 -.-> C
```

### Module Descriptions

| Layer | File | Description |
| :--- | :--- | :--- |
| **UI Layer** | **`main.py`** | **Launcher.** Starts the app and initializes major components (`UIManager`, `CoreEngine`). |
| | **`ui.py` (UIManager)** | **Main Window.** Manages layout, tabs, and buttons. Integrates sub-modules (`image_tree_widget`, `preview_mode_manager`) and relays signals to `CoreEngine`. |
| | **`image_tree_widget.py`** | **Tree View Logic.** Handles the list display of images, Drag & Drop operations, and item reordering. |
| | **`preview_mode_manager.py`** | **Preview Logic.** Manages the image preview display, click point/range settings, and ROI drawing logic. |
| | `floating_window.py` / `dialogs.py` | Minimal mode UI and various popup dialogs. |
| **Core Logic** | **`core.py`** | **The Brain.** Controls the main loop. Receives UI commands, coordinates specialized modules (`capture`, `matcher`), and reports results. |
| | **`monitoring_states.py`** | **State Machine.** Handles complex monitoring logic (Normal Scan, Priority Mode, Backup Countdown) as distinct classes. |
| | **`template_manager.py`** | **Image Prep.** Loads image files and prepares cached data (scaling, ROI cropping) for the matcher. |
| | **`matcher.py`** | **Vision System.** Compares screen captures against template images to find matches and calculate confidence. |
| | **`action.py`** | **Executor.** Performs physical mouse clicks and window activation based on `core.py` instructions. |
| **Hardware** | **`capture.py`** | **Camera.** Captures the screen or specific windows. Uses `dxcam` (Win) or `mss` (Linux). |
| **Data** | **`config.py`** | **Storage.** Reads/Writes image paths, settings, and application configurations to JSON files. |
| | **`locale_manager.py`** | **Translator.** Manages app text and translates it based on JSON files in the `locales` folder. |
