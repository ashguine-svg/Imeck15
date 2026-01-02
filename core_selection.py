# core_selection.py
# 認識範囲選択、ウィンドウ検出、画像保存処理を担当
# ★★★ 修正: capture_image_for_registration 追加、cv2保存+シグナル通知 ★★★
# ★★★ (拡張) ライフサイクル管理機能のトリガー (コンテキストアタッチ) を追加 ★★★

import sys
import shutil
import subprocess
import cv2
import numpy as np
import os
import gc
from PySide6.QtCore import QTimer, QPoint, Qt
from PySide6.QtWidgets import QMessageBox, QApplication
from PySide6.QtGui import QCursor
from pynput import keyboard

if sys.platform == 'win32':
    try:
        import win32gui
    except ImportError:
        win32gui = None
else:
    win32gui = None

from selection import SelectionOverlay, WindowSelectionListener
from pathlib import Path

class SelectionHandler:
    def __init__(self, core):
        self.core = core
        self.logger = core.logger
        self._is_saving_image = False  # 重複呼び出し防止フラグ
        self.ui_manager = core.ui_manager
        self.locale_manager = core.locale_manager
        self.capture_manager = core.capture_manager
        self.config_manager = core.config_manager

        self.window_selection_listener = None
        self.keyboard_selection_listener = None
        self.selection_overlay = None

    def capture_image_for_registration(self):
        if not self.core.recognition_area:
            lm = self.locale_manager.tr
            QMessageBox.warning(
                self.ui_manager, 
                lm("warn_capture_no_rec_area_title"), 
                lm("warn_capture_no_rec_area_text")
            )
            self.ui_manager._update_capture_button_state()
            return
        
        self.core._is_capturing_for_registration = True
        self.ui_manager.setRecAreaDialog()

    def set_recognition_area(self, method: str):
        if self.core._is_reinitializing_display:
            try:
                self.logger.log("log_lazy_reinitialize_capture_backend")
                self.core._reinitialize_capture_backend()
            except Exception as e:
                self.logger.log("log_error_reinitialize_capture", str(e))
            finally:
                self.core._is_reinitializing_display = False

        # 監視中なら停止してからキャプチャフローへ
        if self.core.is_monitoring:
            self.logger.log("log_capture_while_monitoring")
            self.core.stop_monitoring()
            self.logger.log("log_capture_proceed_after_stop")

        # UIを隠し、マウスリスナーを一時停止（映り込み防止）
        self.core.selectionProcessStarted.emit()
        if self.ui_manager:
            if getattr(self.ui_manager, "is_minimal_mode", False) and getattr(self.ui_manager, "floating_window", None):
                self.ui_manager.floating_window.hide()
            else:
                self.ui_manager.hide()
        self.core._stop_global_mouse_listener()
        
        self.core.pre_captured_image_for_registration = None
        
        if method == "rectangle":
            if not self.core._is_capturing_for_registration: 
                self.core.target_hwnd = None; self.core.current_window_scale = None; self.core.windowScaleCalculated.emit(0.0); self.logger.log("log_rec_area_set_rect")
                self.core.environment_tracker.on_rec_area_set("rectangle")
                # コンテキストクリア
                self.core.clear_recognition_area()
            else: 
                self.logger.log("log_capture_area_set_rect")
                try:
                    self.core.pre_captured_image_for_registration = self.capture_manager.capture_frame()
                    if self.core.pre_captured_image_for_registration is None:
                         raise Exception("Failed to capture full screen for pre-capture.")
                    self.logger.log("log_pre_capture_success")
                except Exception as e:
                    self.logger.log("log_pre_capture_failed", str(e))
                    self._on_selection_cancelled()
                    return
            
            self.selection_overlay = SelectionOverlay()
            self.selection_overlay.selectionComplete.connect(self.core._areaSelectedForProcessing.emit)
            self.selection_overlay.selectionCancelled.connect(self._on_selection_cancelled)
            self.selection_overlay.showFullScreen()
        
        elif method == "window":
            if self.core._is_capturing_for_registration:
                try:
                    self.core.pre_captured_image_for_registration = self.capture_manager.capture_frame()
                    if self.core.pre_captured_image_for_registration is None:
                         raise Exception("Failed to capture full screen for pre-capture.")
                    self.logger.log("log_pre_capture_success")
                except Exception as e:
                    self.logger.log("log_pre_capture_failed", str(e))
                    self._on_selection_cancelled()
                    return

            if sys.platform == 'win32' and win32gui:
                if self.core._is_capturing_for_registration and self.core.recognition_area:
                    self.logger.log("log_capture_from_existing_rec_area")
                    (x1, y1, x2, y2) = self.core.recognition_area
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    self._handle_window_click_for_selection_windows(center_x, center_y)
                    return 
            
            if not self.core._is_capturing_for_registration:
                self.logger.log("log_rec_area_set_window")
            else:
                self.logger.log("log_capture_area_set_window")
                
            self.window_selection_listener = WindowSelectionListener(
                self._handle_window_click_for_selection,
                cancel_callback=self._on_window_selection_cancelled_by_mouse
            )
            self.window_selection_listener.start()
            # Linuxではグローバルキーボードフックが環境依存（Wayland等で不可/不安定）なため、
            # ウィンドウ選択のキャンセルは右クリックでも可能にしている。
            # WindowsではESCキャンセルも継続（ベストエフォート）。
            if sys.platform == 'win32':
                try:
                    self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection)
                    self.keyboard_selection_listener.start()
                except Exception as e:
                    self.logger.log(f"[WARN] Failed to start keyboard listener for window selection: {e}")

        elif method == "fullscreen":
            if self.core._is_capturing_for_registration:
                self.logger.log("log_capture_area_fullscreen_disabled") 
                self._on_selection_cancelled() 
                QMessageBox.warning(
                    self.ui_manager, 
                    self.locale_manager.tr("warn_capture_fullscreen_title"), 
                    self.locale_manager.tr("warn_capture_fullscreen_text")
                )
                return
            
            try:
                screen = QApplication.primaryScreen()
                if not screen:
                    raise Exception("QApplication.primaryScreen() returned None")
                
                geo = screen.geometry()
                fullscreen_rect = (geo.left(), geo.top(), geo.right() + 1, geo.bottom() + 1)
                
                self.logger.log("log_rec_area_set_fullscreen_internal")
                self.core.target_hwnd = None
                self.core.current_window_scale = None
                self.core.windowScaleCalculated.emit(0.0)
                self.core.environment_tracker.on_rec_area_set("fullscreen")
                self.core.appContextChanged.emit(None)
                # コンテキストクリア
                self.core.clear_recognition_area()
                
                self.core._areaSelectedForProcessing.emit(fullscreen_rect)
            
            except Exception as e:
                self.logger.log("log_error_get_primary_screen_geo", str(e))
                self._on_selection_cancelled()
                return

    def _on_selection_cancelled(self):
        self.logger.log("log_selection_cancelled"); self.core._is_capturing_for_registration = False
        
        self.core.pre_captured_image_for_registration = None 
        
        if self.window_selection_listener and self.window_selection_listener.is_alive(): self.window_selection_listener.stop(); self.window_selection_listener = None
        if self.keyboard_selection_listener and self.keyboard_selection_listener.is_alive(): self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        if hasattr(self, 'selection_overlay') and self.selection_overlay: self.selection_overlay.close(); self.selection_overlay = None
        self.core.selectionProcessFinished.emit(); self.core._show_ui_safe()
        self.logger.log("[DEBUG] Scheduling listener restart after cancellation (150ms delay)..."); QTimer.singleShot(150, self.core._start_global_mouse_listener)

    def _on_key_press_for_selection(self, key):
        if key == keyboard.Key.esc:
            self.logger.log("log_selection_cancelled_key")
            if self.keyboard_selection_listener and self.keyboard_selection_listener.is_alive(): self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
            if self.window_selection_listener and self.window_selection_listener.is_alive(): self.window_selection_listener.stop(); self.window_selection_listener = None
            self._on_selection_cancelled(); return False

    def _on_window_selection_cancelled_by_mouse(self):
        try:
            self.logger.log("[INFO] Window selection cancelled by mouse (right/middle click).")
        except Exception:
            pass
        self._on_selection_cancelled()

    def _handle_window_click_for_selection(self, x, y):
        if self.keyboard_selection_listener: self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        if sys.platform == 'win32' and win32gui: self._handle_window_click_for_selection_windows(x, y)
        elif sys.platform.startswith('linux'): self._handle_window_click_for_selection_linux(x, y)
        else: self.logger.log("Window selection not supported on this platform."); self._on_selection_cancelled(); return

    def _handle_window_click_for_selection_windows(self, x, y):
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd: self._on_selection_cancelled(); return
            if not self.core._is_capturing_for_registration:
                self.core.target_hwnd = hwnd
                if 'dxcam' in sys.modules and hasattr(self.capture_manager, 'dxcam_sct') and self.capture_manager.dxcam_sct:
                    try: self.capture_manager.dxcam_sct.target_hwnd = hwnd
                    except Exception as dxcam_err: self.logger.log(f"Error setting DXCam target HWND: {dxcam_err}")
            client_rect_win = win32gui.GetClientRect(hwnd); left, top = win32gui.ClientToScreen(hwnd, (0, 0)); right = left + client_rect_win[2]; bottom = top + client_rect_win[3]
            if right <= left or bottom <= top: self.logger.log("log_window_invalid_rect", left, top, right, bottom); self._on_selection_cancelled(); return
            try: import pyautogui; screen_width, screen_height = pyautogui.size(); rect = (max(0, left), max(0, top), min(screen_width, right), min(screen_height, bottom))
            except ImportError: rect = (max(0, left), max(0, top), right, bottom)
            
            if self.core._is_capturing_for_registration: 
                self.core._areaSelectedForProcessing.emit(rect)
                return
            
            title = win32gui.GetWindowText(hwnd); self.core._pending_window_info = {"title": title, "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.core.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
        except Exception as e:
            self.logger.log("log_window_get_rect_failed", str(e))
            if not self.core._is_capturing_for_registration: self.core.target_hwnd = None
            self.core._showUiSignal.emit(); self.core.selectionProcessFinished.emit()

    def _handle_window_click_for_selection_linux(self, x, y):
        if os.environ.get('WAYLAND_DISPLAY'):
            self.logger.log("log_linux_wayland_manual_attempt")
            
        missing_tools = [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]
        if missing_tools: 
            self.logger.log("log_linux_tool_not_found", ', '.join(missing_tools))
            self._on_selection_cancelled()
            return
        
        try:
            id_proc = subprocess.run(
                ['xdotool', 'getmouselocation'], 
                capture_output=True, text=True, timeout=2, check=False 
            )

            if id_proc.returncode != 0 or not id_proc.stdout:
                stderr_output = id_proc.stderr.strip() if id_proc.stderr else "No output"
                raise ValueError(f"xdotool getmouselocation failed. Exit code: {id_proc.returncode}, Stderr: {stderr_output}")

            window_id_line = next((line for line in id_proc.stdout.strip().split() if line.startswith('window:')), None)
            
            if not window_id_line:
                raise ValueError(f"Could not find 'window:' in xdotool output: {id_proc.stdout}")
                
            window_id = window_id_line.split(':')[1]
            
            if not window_id.isdigit():
                 raise ValueError(f"Invalid window ID received: '{window_id}'")
            
            info_proc = subprocess.run(
                ['xwininfo', '-id', window_id], 
                capture_output=True, text=True, timeout=2, check=False 
            )

            if info_proc.returncode != 0 or not info_proc.stdout:
                stderr_output = info_proc.stderr.strip() if info_proc.stderr else "No output"
                raise ValueError(f"xwininfo failed for ID {window_id}. Exit code: {info_proc.returncode}, Stderr: {stderr_output}")

            info = {}
            for line in info_proc.stdout.split('\n'):
                 if ':' in line: parts = line.split(':', 1); key = parts[0].strip(); value = parts[1].strip(); info[key] = value
            
            left = int(info['Absolute upper-left X']); top = int(info['Absolute upper-left Y']); width = int(info['Width']); height = int(info['Height']); title_part = info.get('xwininfo', ''); title = title_part.split('"')[1] if '"' in title_part else f"Window (ID: {window_id})"
            
            if width <= 0 or height <= 0: 
                self.logger.log("log_linux_window_invalid_rect")
                self._on_selection_cancelled()
                return
                
            try: import pyautogui; screen_width, screen_height = pyautogui.size(); rect = (max(0, left), max(0, top), min(screen_width, left + width), min(screen_height, top + height))
            except ImportError: rect = (max(0, left), max(0, top), left + width, top + height)
            
            if self.core._is_capturing_for_registration: 
                self.core._areaSelectedForProcessing.emit(rect)
                return

            # Linuxの場合もHWND（ウィンドウID）をセット
            self.core.target_hwnd = int(window_id)

            self.core._pending_window_info = {"title": title, "dims": {'width': width, 'height': height}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.core.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
            
        except (subprocess.TimeoutExpired, ValueError, KeyError, Exception) as e: 
            self.logger.log("log_linux_window_get_rect_failed", str(e))
            self._on_selection_cancelled()

    def process_base_size_prompt_response(self, save_as_base: bool):
        try:
            if not (info := self.core._pending_window_info): self.logger.log("Warning: process_base_size_prompt_response called with no pending info."); self.core._showUiSignal.emit(); self.core.selectionProcessFinished.emit(); return
            title, current_dims, rect = info['title'], info['dims'], info['rect']
            
            self.core.environment_tracker.on_rec_area_set("window", title)
            
            # --- ▼▼▼ 拡張: ライフサイクル管理コンテキストのアタッチ ▼▼▼ ---
            if self.core.target_hwnd:
                self.core._attach_session_context(self.core.target_hwnd, title)
            # --- ▲▲▲ 追加完了 ▲▲▲ ---

            if save_as_base:
                scales_data = self.core.config_manager.load_window_scales(); scales_data[title] = current_dims; self.core.config_manager.save_window_scales(scales_data); self.core.current_window_scale = 1.0; self.core.actual_window_scale = 1.0; self.logger.log("log_window_base_size_saved", title); self.core.windowScaleCalculated.emit(1.0); self.core._areaSelectedForProcessing.emit(rect)
            elif title and title in (scales_data := self.core.config_manager.load_window_scales()):
                base_dims = scales_data[title]; calc_scale = current_dims['width'] / base_dims['width'] if base_dims['width'] > 0 else 1.0
                # ★★★ 追加: 補正前の実際のウィンドウスケールを保存 ★★★
                self.core.actual_window_scale = calc_scale
                if 0.995 <= calc_scale <= 1.005: self.core.current_window_scale = 1.0; self.logger.log("log_window_scale_calc", title, f"{calc_scale:.3f}"); self.core.windowScaleCalculated.emit(1.0); self.core._areaSelectedForProcessing.emit(rect)
                else: self.core._pending_scale_prompt_info = {**info, 'calculated_scale': calc_scale}; self.core.askToApplyWindowScaleSignal.emit(calc_scale); return
            else: self.core.current_window_scale = None; self.core.actual_window_scale = None; self.core.windowScaleCalculated.emit(0.0); self.core._areaSelectedForProcessing.emit(rect)
            
            # 認識範囲設定後にappContextChangedを発行（ツリー更新が正しいタイミングで行われるようにするため）
            self.core.appContextChanged.emit(title)
        except Exception as e:
            self.logger.log("log_error_base_size_process", str(e))
            if not self.core._pending_scale_prompt_info: self.core._pending_window_info = None; self.core._showUiSignal.emit(); self.core.selectionProcessFinished.emit()
        finally:
            if not self.core._pending_scale_prompt_info: self.core._pending_window_info = None; self.core.selectionProcessFinished.emit()

    def process_apply_scale_prompt_response(self, apply_scale: bool):
        try:
            if not (info := self.core._pending_scale_prompt_info): self.logger.log("Warning: process_apply_scale_prompt_response called with no pending info."); self.core._pending_window_info = None; self.core._showUiSignal.emit(); self.core.selectionProcessFinished.emit(); return
            scale, rect = info['calculated_scale'], info['rect']
            title = info.get('title')
            if apply_scale:
                self.ui_manager.app_config['auto_scale']['use_window_scale'] = True; self.ui_manager.auto_scale_widgets['use_window_scale'].setChecked(True); self.ui_manager.on_app_settings_changed(); self.core.current_window_scale = scale; self.logger.log("log_window_scale_applied", f"{scale:.3f}")
            else: self.core.current_window_scale = None; self.logger.log("log_window_scale_not_applied", f"{scale:.3f}")
            self.core.windowScaleCalculated.emit(self.core.current_window_scale if self.core.current_window_scale is not None else 0.0); self.core._areaSelectedForProcessing.emit(rect)
            # 認識範囲設定後にappContextChangedを発行（ツリー更新が正しいタイミングで行われるようにするため）
            if title:
                self.core.appContextChanged.emit(title)
        except Exception as e: self.logger.log("log_error_apply_scale_process", str(e))
        finally: self.core._pending_scale_prompt_info = None; self.core._pending_window_info = None; self.core.selectionProcessFinished.emit()

    def handle_area_selection(self, coords):
        if self.core._is_capturing_for_registration: self.core._is_capturing_for_registration = False; QTimer.singleShot(100, lambda: self._save_captured_image(coords))
        else: 
            self.core.recognition_area = coords; 
            self.logger.log("log_rec_area_set", str(coords)); 
            self._update_rec_area_preview(); 
            # 認識範囲設定完了後にツリーを更新（OCR設定アイテムが消えないようにするため）
            self.ui_manager.update_image_tree()
            self.core.selectionProcessFinished.emit(); 
            self.core._show_ui_safe()
            self.ui_manager._update_capture_button_state()
            
        if self.selection_overlay: self.selection_overlay = None
        self.logger.log("[DEBUG] Scheduling listener restart after selection completion (150ms delay)..."); QTimer.singleShot(150, self.core._start_global_mouse_listener)

    def _save_captured_image(self, region_coords):
        try: 
            QTimer.singleShot(100, lambda: self._do_capture_and_emit(region_coords)) 
        except Exception as e: 
            self.logger.log("error_message_capture_prepare_failed", str(e)) 
            self.core.selectionProcessFinished.emit() 
            self.core.pre_captured_image_for_registration = None

    def _do_capture_and_emit(self, region_coords):
        try:
            captured_image = None
            
            if self.core.pre_captured_image_for_registration is not None:
                self.logger.log("log_cropping_from_pre_capture")
                try:
                    (x1, y1, x2, y2) = region_coords
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    captured_image = self.core.pre_captured_image_for_registration[y1:y2, x1:x2]
                    self.core.pre_captured_image_for_registration = None 
                except Exception as crop_e:
                    self.logger.log("log_crop_from_pre_capture_failed", str(crop_e))
                    captured_image = None
                    self.core.pre_captured_image_for_registration = None
            else:
                self.logger.log("log_capturing_new_frame")
                captured_image = self.capture_manager.capture_frame(region=region_coords)

            if captured_image is None or captured_image.size == 0: 
                self.logger.log("warn_message_capture_failed") 
                self.core.captureFailedSignal.emit()
                self.core.selectionProcessFinished.emit()
                return
                
            self.core.capturedImageReadyForPreview.emit(captured_image)
                
        except Exception as e: 
            self.logger.log("error_message_capture_save_failed", str(e))
            self.core.captureFailedSignal.emit() 
            self.core.selectionProcessFinished.emit()
        finally:
             self.core.pre_captured_image_for_registration = None
    
    def handle_save_captured_image(self, file_name: str, captured_image: np.ndarray):
        self.logger.log(f"[DEBUG] handle_save_captured_image called. File: {file_name}")
        
        # 重複呼び出し防止
        if self._is_saving_image:
            self.logger.log("[WARN] handle_save_captured_image called while already saving, ignoring duplicate call.")
            return
        
        self._is_saving_image = True
        
        try:
            if not file_name:
                self.logger.log("log_rename_error_empty")
                self._is_saving_image = False
                self.core.selectionProcessFinished.emit()
                return

            self.ui_manager.set_tree_enabled(False)
            save_path = self.core.config_manager.base_dir / f"{file_name}.png"
            
            if save_path.exists():
                lm = self.locale_manager.tr
                reply = QMessageBox.question(self.ui_manager, lm("confirm_overwrite_title"), 
                                             lm("confirm_overwrite_message", save_path.name), 
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No:
                    self.ui_manager.set_tree_enabled(True)
                    self._is_saving_image = False
                    self._reset_cursor_and_resume_listener()
                    return
            
            env_data = self.core.environment_tracker._collect_current_environment()
            
            if self.core.thread_pool: 
                self.logger.log("[DEBUG] Submitting save task to thread pool...")
                self.core.thread_pool.submit(self._save_image_task, captured_image, save_path, env_data).add_done_callback(self._on_save_image_done)
            else: 
                self._on_save_image_done(None, success=False, message=self.locale_manager.tr("Error: Thread pool unavailable for saving."))
                self.ui_manager.set_tree_enabled(True)
                self._is_saving_image = False
                self._reset_cursor_and_resume_listener() 
        
        except Exception as e:
            self.logger.log(f"[ERROR] Exception in handle_save_captured_image: {e}")
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e)))
            self._is_saving_image = False
            self._reset_cursor_and_resume_listener() 

    def _save_image_task(self, image, save_path, env_data: dict):
        try:
            is_success, buffer = cv2.imencode('.png', image)
            if not is_success: 
                raise IOError("cv2.imencode failed")
            
            buffer.tofile(str(save_path))

            settings = self.core.config_manager.load_item_setting(Path()); 
            settings['image_path'] = str(save_path); 
            settings['point_click'] = True
            settings['environment_info'] = [env_data] 

            self.core.config_manager.save_item_setting(save_path, settings); 
            self.core.config_manager.add_item(save_path)
            
            return True, self.locale_manager.tr("log_image_saved", str(save_path.name))
            
        except Exception as e:
            return False, self.locale_manager.tr("log_image_save_failed", str(e))
    
    def _on_save_image_done(self, future, success=None, message=None):
        """スレッドプールのコールバックから呼ばれる。メインスレッドで処理を実行する必要がある。"""
        try:
            if future: 
                success, message = future.result()
            
            # シグナルを使ってメインスレッドで処理を実行
            self.core._saveImageDoneProcessRequested.emit(bool(success), str(message) if message else "")
        
        except Exception as e: 
            # エラー時もメインスレッドで処理
            self.core._saveImageDoneProcessRequested.emit(False, f"Error processing save result: {e}")
        
    def _reset_cursor_and_resume_listener(self):
        # (省略)
        try:
            # ★ 1. ログの数値を書き換え（必須ではないですが分かりやすさのため）
            self.logger.log("[DEBUG] Scheduling listener restart after selection completion (600ms delay)...")
            
            # ★ 2. メモリ解放を追加
            gc.collect()
            
            # ★ 3. 待機時間を 100 → 600 に変更
            QTimer.singleShot(600, self.core._start_global_mouse_listener)
            
            self.core.selectionProcessFinished.emit()
        except Exception as e:
            self.logger.log(f"[WARN] Error in _reset_cursor_and_resume_listener: {e}")
            self.core.selectionProcessFinished.emit()

    def clear_recognition_area(self):
        self.core.recognition_area = None; self.core.current_window_scale = None; self.core.actual_window_scale = None; self.core.target_hwnd = None; self.core.windowScaleCalculated.emit(0.0)
        
        self.core.environment_tracker.on_rec_area_clear()
        self.core.appContextChanged.emit(None) 
        
        if 'dxcam' in sys.modules and hasattr(self.core.capture_manager, 'dxcam_sct') and self.core.capture_manager.dxcam_sct:
            try: self.core.capture_manager.dxcam_sct.target_hwnd = None
            except Exception as dxcam_err: self.logger.log(f"Error resetting DXCam target HWND: {dxcam_err}")
        self.logger.log("log_rec_area_cleared"); self.core.updateRecAreaPreview.emit(None)
        self.ui_manager._update_capture_button_state()

    def _update_rec_area_preview(self):
        img = None
        if self.core.recognition_area:
             try: img = self.core.capture_manager.capture_frame(region=self.core.recognition_area)
             except Exception as e: self.logger.log(f"Error capturing for rec area preview: {e}")
        self.core.updateRecAreaPreview.emit(img)
