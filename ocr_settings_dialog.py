# ocr_settings_dialog.py
# ★★★ 修正: OCRRuntimeEvaluator.evaluate の戻り値増加に対応 ★★★

import time
import cv2
import numpy as np
import re
import sys
import webbrowser
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QSlider, QComboBox, QCheckBox, QGroupBox, QTextEdit, 
    QMessageBox, QWidget, QSizePolicy, QLineEdit, QFormLayout,
    QScrollArea, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QRectF, QPoint, QPointF, QEvent
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QMouseEvent

from ocr_manager import OCRConfig, OCRManager, TESS_CODE_DISPLAY_MAP
from ocr_runtime import OCRRuntimeEvaluator

class OCRPreviewLabel(QLabel):
    rect_changed = Signal(tuple)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self._pixmap = QPixmap()
        self.ocr_roi_rect = None
        self.parent_settings = {}
        self.is_drawing = False
        self.start_pos = QPoint()
        self.current_pos = QPoint()
        self.display_rect = QRectF()
        self.scale_x = 1.0
        self.scale_y = 1.0

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self._update_geometry()
        self.update()

    def set_roi(self, rect):
        self.ocr_roi_rect = rect
        self.update()

    def set_parent_settings(self, settings):
        self.parent_settings = settings if settings else {}
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_geometry()

    def _update_geometry(self):
        if self._pixmap.isNull() or self.width() == 0 or self.height() == 0:
            self.display_rect = QRectF()
            return
        w_r = self.width() / self._pixmap.width()
        h_r = self.height() / self._pixmap.height()
        scale = min(w_r, h_r)
        disp_w = self._pixmap.width() * scale
        disp_h = self._pixmap.height() * scale
        disp_x = (self.width() - disp_w) / 2
        disp_y = (self.height() - disp_h) / 2
        self.display_rect = QRectF(disp_x, disp_y, disp_w, disp_h)
        self.scale_x = scale
        self.scale_y = scale

    def _map_widget_to_image(self, pos):
        if self._pixmap.isNull() or not self.display_rect.contains(pos): return None
        rel_x = (pos.x() - self.display_rect.x()) / self.scale_x
        rel_y = (pos.y() - self.display_rect.y()) / self.scale_y
        return QPoint(int(rel_x), int(rel_y))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            img_pos = self._map_widget_to_image(event.pos())
            if img_pos:
                self.is_drawing = True
                self.start_pos = img_pos
                self.current_pos = img_pos
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_drawing:
            img_pos = self._map_widget_to_image(event.pos())
            if img_pos:
                self.current_pos = img_pos
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.is_drawing:
            self.is_drawing = False
            x = min(self.start_pos.x(), self.current_pos.x())
            y = min(self.start_pos.y(), self.current_pos.y())
            w = abs(self.current_pos.x() - self.start_pos.x())
            h = abs(self.current_pos.y() - self.start_pos.y())
            if w > 5 and h > 5:
                img_w, img_h = self._pixmap.width(), self._pixmap.height()
                x, y = max(0, min(x, img_w)), max(0, min(y, img_h))
                w, h = min(w, img_w - x), min(h, img_h - y)
                self.ocr_roi_rect = (x, y, w, h)
                self.rect_changed.emit(self.ocr_roi_rect)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._pixmap.isNull(): return
        painter.drawPixmap(self.display_rect.toRect(), self._pixmap)
        
        def to_screen(x, y):
            sx = self.display_rect.x() + x * self.scale_x
            sy = self.display_rect.y() + y * self.scale_y
            return QPointF(sx, sy)

        if self.parent_settings.get('roi_enabled'):
            roi_mode = self.parent_settings.get('roi_mode', 'fixed')
            p_roi = self.parent_settings.get('roi_rect_variable') if roi_mode == 'variable' else self.parent_settings.get('roi_rect')
            if p_roi:
                tl = to_screen(p_roi[0], p_roi[1])
                br = to_screen(p_roi[2], p_roi[3])
                painter.setPen(QPen(QColor(0, 255, 0, 200), 2))
                painter.setBrush(QColor(0, 255, 0, 50))
                painter.drawRect(QRectF(tl, br))
        
        if self.parent_settings.get('point_click') and self.parent_settings.get('click_position'):
            cx, cy = self.parent_settings['click_position']
            pt = to_screen(cx, cy)
            painter.setPen(QPen(QColor(255, 0, 0, 200), 2))
            painter.setBrush(QColor(255, 0, 0, 150))
            painter.drawEllipse(pt, 4, 4) 
        elif self.parent_settings.get('range_click') and self.parent_settings.get('click_rect'):
            c_rect = self.parent_settings['click_rect']
            tl = to_screen(c_rect[0], c_rect[1])
            br = to_screen(c_rect[2], c_rect[3])
            painter.setPen(QPen(QColor(50, 100, 255, 150), 1, Qt.DashLine))
            painter.setBrush(QColor(50, 100, 255, 30))
            painter.drawRect(QRectF(tl, br))

        if self.ocr_roi_rect:
            x, y, w, h = self.ocr_roi_rect
            tl = to_screen(x, y)
            sw = w * self.scale_x
            sh = h * self.scale_y
            painter.setPen(QPen(QColor("#9c27b0"), 2))
            painter.setBrush(QColor(156, 39, 176, 50))
            painter.drawRect(QRectF(tl.x(), tl.y(), sw, sh))

        if self.is_drawing:
            x = min(self.start_pos.x(), self.current_pos.x())
            y = min(self.start_pos.y(), self.current_pos.y())
            w = abs(self.current_pos.x() - self.start_pos.x())
            h = abs(self.current_pos.y() - self.start_pos.y())
            tl = to_screen(x, y)
            sw = w * self.scale_x
            sh = h * self.scale_y
            painter.setPen(QPen(QColor("#9c27b0"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(tl.x(), tl.y(), sw, sh))


class OCRSettingsDialog(QDialog):
    def __init__(self, parent_image, current_config: OCRConfig, current_roi=None, current_condition=None, enabled=True, parent=None):
        super().__init__(parent)
        
        self._last_input_click_time = 0
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        
        self.locale_manager = None
        if parent and hasattr(parent, 'locale_manager'):
            self.locale_manager = parent.locale_manager
        
        self.original_image = parent_image
        self.preview_image = parent_image.copy()
        self.config = current_config
        self.roi = current_roi
        self.condition = current_condition if current_condition else {"operator": ">=", "value": 0}
        self.enabled = enabled
        
        self.ocr_manager = OCRManager()
        self.parent_item_settings = {}
        self.previous_lang_idx = -1

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(100) 
        self.debounce_timer.timeout.connect(self.update_preview_image)

        self.setup_ui()
        self.load_initial_preview()

    def tr(self, key, *args):
        if self.locale_manager:
            return self.locale_manager.tr(key, *args)
        return key

    def set_parent_settings(self, settings):
        self.parent_item_settings = settings
        if self.image_label:
            self.image_label.set_parent_settings(settings)

    def setup_ui(self):
        self.setWindowTitle(self.tr("ocr_dialog_title"))
        self.resize(1000, 680)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        preview_container = QWidget()
        preview_container.setStyleSheet("background-color: #263238; border-radius: 4px;")
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_label = OCRPreviewLabel()
        self.image_label.rect_changed.connect(self.on_roi_changed)
        
        lbl_hint = QLabel(self.tr("ocr_hint_drag"))
        lbl_hint.setStyleSheet("color: #b0bec5; font-weight: bold; padding: 5px;")
        lbl_hint.setAlignment(Qt.AlignCenter)
        
        preview_layout.addWidget(lbl_hint)
        preview_layout.addWidget(self.image_label, 1)
        
        main_layout.addWidget(preview_container, 1) 

        settings_panel = QWidget()
        settings_panel.setStyleSheet("""
            QWidget#SettingsPanel {
                background-color: #f5f5f5; 
                border: 1px solid #cfd8dc; 
                border-radius: 6px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px;
            }
        """)
        settings_panel.setObjectName("SettingsPanel")
        
        panel_layout = QVBoxLayout(settings_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(10)

        groups_layout = QHBoxLayout()
        groups_layout.setSpacing(10)

        img_group = QGroupBox(self.tr("ocr_grp_preprocess"))
        img_layout = QGridLayout()
        img_layout.setContentsMargins(8, 8, 8, 8)
        img_layout.setVerticalSpacing(4)
        img_layout.setHorizontalSpacing(8)

        img_layout.addWidget(QLabel(self.tr("ocr_lbl_scale")), 0, 0)
        self.combo_scale = QComboBox()
        self.combo_scale.addItems(["1.0x", "2.0x", "3.0x"])
        scale_index = int(self.config.scale - 1)
        self.combo_scale.setCurrentIndex(max(0, min(scale_index, 2)))
        self.combo_scale.currentIndexChanged.connect(self.trigger_preview_update)
        img_layout.addWidget(self.combo_scale, 0, 1)

        img_layout.addWidget(QLabel(self.tr("ocr_lbl_threshold")), 1, 0)
        thresh_hbox = QHBoxLayout()
        self.slider_thresh = QSlider(Qt.Horizontal)
        self.slider_thresh.setRange(0, 255)
        self.slider_thresh.setValue(self.config.threshold)
        self.slider_thresh.valueChanged.connect(self.trigger_preview_update)
        thresh_hbox.addWidget(self.slider_thresh)
        self.label_thresh_val = QLabel(f"{self.config.threshold}")
        self.label_thresh_val.setFixedWidth(30)
        thresh_hbox.addWidget(self.label_thresh_val)
        img_layout.addLayout(thresh_hbox, 1, 1)

        self.chk_invert = QCheckBox(self.tr("ocr_chk_invert"))
        self.chk_invert.setChecked(self.config.invert)
        self.chk_invert.stateChanged.connect(self.trigger_preview_update)
        img_layout.addWidget(self.chk_invert, 2, 0, 1, 2)

        img_group.setLayout(img_layout)
        groups_layout.addWidget(img_group, 1)

        logic_group = QGroupBox(self.tr("ocr_grp_detection") + " / " + self.tr("ocr_grp_condition"))
        logic_layout = QGridLayout()
        logic_layout.setContentsMargins(8, 8, 8, 8)
        logic_layout.setVerticalSpacing(4)
        logic_layout.setHorizontalSpacing(8)
        
        logic_layout.addWidget(QLabel(self.tr("ocr_lbl_lang")), 0, 0)
        self.combo_lang = QComboBox()
        other_langs = sorted([code for code in TESS_CODE_DISPLAY_MAP.keys() if code not in ["eng", "jpn"]])
        def add_item(code, display): self.combo_lang.addItem(display, code)
        add_item("eng", TESS_CODE_DISPLAY_MAP["eng"])
        add_item("jpn", TESS_CODE_DISPLAY_MAP["jpn"])
        add_item("jpn+eng", "Japanese + English")
        for code in other_langs: add_item(code, TESS_CODE_DISPLAY_MAP[code])
        
        current_lang_code = self.config.lang
        idx = self.combo_lang.findData(current_lang_code)
        if idx >= 0: self.combo_lang.setCurrentIndex(idx)
        else:
            self.combo_lang.addItem(current_lang_code, current_lang_code)
            self.combo_lang.setCurrentIndex(self.combo_lang.count() - 1)
        self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        logic_layout.addWidget(self.combo_lang, 0, 1)

        self.chk_numeric = QCheckBox(self.tr("ocr_chk_numeric"))
        self.chk_numeric.setChecked(self.config.numeric_mode)
        self.chk_numeric.setToolTip(self.tr("ocr_tooltip_numeric"))
        self.chk_numeric.stateChanged.connect(self.on_numeric_mode_changed)
        logic_layout.addWidget(self.chk_numeric, 0, 2)

        logic_layout.addWidget(QLabel(self.tr("ocr_lbl_operator")), 1, 0)
        self.combo_operator = QComboBox()
        logic_layout.addWidget(self.combo_operator, 1, 1)
        
        self.input_target_value = QLineEdit()
        self.input_target_value.setPlaceholderText(self.tr("ocr_placeholder_target"))
        
        self.input_target_value.setReadOnly(False) 
        self.input_target_value.setStyleSheet("background-color: #ffffff; color: #333;")
        self.input_target_value.installEventFilter(self)
        
        logic_layout.addWidget(self.input_target_value, 1, 2)

        current_op_key = self.condition.get("operator", ">=")
        self.update_operator_list(current_op_key)
        self.input_target_value.setText(str(self.condition.get("value", "")))

        logic_group.setLayout(logic_layout)
        groups_layout.addWidget(logic_group, 2)

        panel_layout.addLayout(groups_layout)

        bottom_bar_layout = QHBoxLayout()
        bottom_bar_layout.setSpacing(15)

        self.chk_enable = QCheckBox(self.tr("ocr_chk_enable"))
        self.chk_enable.setStyleSheet("font-weight: bold; color: #9c27b0;")
        self.chk_enable.setChecked(self.enabled)
        bottom_bar_layout.addWidget(self.chk_enable)

        self.btn_help = QPushButton("?")
        self.btn_help.setFixedWidth(30)
        self.btn_help.setToolTip(self.tr("ocr_help_tooltip"))
        self.btn_help.setStyleSheet("""
            QPushButton {
                background-color: #e3f2fd; 
                border: 1px solid #2196f3; 
                color: #1565c0; 
                font-weight: bold; 
                border-radius: 15px;
            }
            QPushButton:hover { background-color: #bbdefb; }
        """)
        self.btn_help.clicked.connect(self.show_tesseract_guide)
        bottom_bar_layout.addWidget(self.btn_help)

        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        bottom_bar_layout.addWidget(line)

        self.btn_test = QPushButton(self.tr("ocr_btn_test"))
        self.btn_test.setIcon(QPixmap()) 
        self.btn_test.clicked.connect(self.run_ocr_test)
        self.btn_test.setStyleSheet("background-color: #e0f7fa; border: 1px solid #00acc1; padding: 4px 10px; border-radius: 4px;")
        bottom_bar_layout.addWidget(self.btn_test)

        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFixedHeight(45) 
        self.text_log.setStyleSheet("background-color: #fafafa; border: 1px solid #cfd8dc; font-size: 11px;")
        bottom_bar_layout.addWidget(self.text_log, 1)

        btn_box = QHBoxLayout()
        self.btn_ok = QPushButton("OK") 
        self.btn_ok.setFixedWidth(80)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedWidth(80)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_box.addWidget(self.btn_ok)
        btn_box.addWidget(self.btn_cancel)
        bottom_bar_layout.addLayout(btn_box)

        panel_layout.addLayout(bottom_bar_layout)

        main_layout.addWidget(settings_panel)
        
        self.on_numeric_mode_changed()
        self.on_enable_toggled(self.enabled)

    def eventFilter(self, source, event):
        if source == self.input_target_value and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                if sys.platform != 'win32':
                    current_time = time.time()
                    if current_time - self._last_input_click_time < 1.0:
                        return True 

                    self._last_input_click_time = current_time
                    self.open_custom_input_dialog()
                    return True 
        return super().eventFilter(source, event)

    def open_custom_input_dialog(self):
        try:
            from custom_input_dialog import ask_string_custom
        except ImportError:
            QMessageBox.critical(self, "Error", "custom_input_dialog.py not found.")
            return

        current_val = self.input_target_value.text()
        title = self.tr("ocr_grp_condition")
        prompt = self.tr("ocr_placeholder_target")
        if title == "ocr_grp_condition": title = "Condition"
        if prompt == "ocr_placeholder_target": prompt = "Target Value"

        new_val, ok = ask_string_custom(self, title, prompt, current_val)
        if ok:
            self.input_target_value.setText(new_val)

    def on_enable_toggled(self, checked):
        pass

    def load_initial_preview(self):
        self.update_preview_image()
        if self.roi:
            self.image_label.set_roi(self.roi)

    def on_numeric_mode_changed(self):
        self.config.numeric_mode = self.chk_numeric.isChecked()
        
        if self.config.numeric_mode:
            if self.combo_lang.isEnabled():
                self.previous_lang_idx = self.combo_lang.currentIndex()
            
            idx = self.combo_lang.findData("eng")
            if idx >= 0:
                self.combo_lang.blockSignals(True)
                self.combo_lang.setCurrentIndex(idx)
                self.combo_lang.blockSignals(False)
            self.combo_lang.setEnabled(False)
        else:
            self.combo_lang.setEnabled(True)
            if self.previous_lang_idx >= 0:
                self.combo_lang.blockSignals(True)
                self.combo_lang.setCurrentIndex(self.previous_lang_idx)
                self.combo_lang.blockSignals(False)

        current_op = self.combo_operator.currentData()
        self.update_operator_list(current_op)
        self.trigger_preview_update()

    def update_operator_list(self, current_op_key=None):
        if current_op_key is None:
            current_op_key = self.combo_operator.currentData()

        self.combo_operator.clear()
        
        if self.chk_numeric.isChecked():
            ops = [
                (">=", ">="), ("<=", "<="), ("==", "=="), 
                ("!=", "!="), (">", ">"), ("<", "<")
            ]
        else:
            ops = [
                (self.tr("op_contains"), "Contains"),
                (self.tr("op_equals"), "Equals"),
                (self.tr("op_regex"), "Regex")
            ]
            
        for display, key in ops:
            self.combo_operator.addItem(display, key)
        
        idx = self.combo_operator.findData(current_op_key)
        if idx >= 0:
            self.combo_operator.setCurrentIndex(idx)
        else:
            self.combo_operator.setCurrentIndex(0)

    @Slot()
    def trigger_preview_update(self):
        self.label_thresh_val.setText(f"{self.slider_thresh.value()}")
        self.debounce_timer.start()

    def update_preview_image(self):
        self.config.scale = float(self.combo_scale.currentText().replace('x', ''))
        self.config.threshold = self.slider_thresh.value()
        self.config.invert = self.chk_invert.isChecked()
        self.config.lang = self.combo_lang.currentData() 
        self.config.numeric_mode = self.chk_numeric.isChecked()

        try:
            temp_img = self.original_image.copy()
            if len(temp_img.shape) == 3:
                gray = cv2.cvtColor(temp_img, cv2.COLOR_BGR2GRAY)
            else:
                gray = temp_img
                
            if self.config.invert:
                gray = cv2.bitwise_not(gray)
                
            _, binary = cv2.threshold(gray, self.config.threshold, 255, cv2.THRESH_BINARY)
            disp_img = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
            
            h, w, ch = disp_img.shape
            bytes_per_line = ch * w
            qt_image = QImage(disp_img.data, w, h, bytes_per_line, QImage.Format_RGB888)
            self.image_label.set_image(QPixmap.fromImage(qt_image))
            
        except Exception as e:
            self.text_log.setText(f"Preview Error: {str(e)}")

    @Slot(tuple)
    def on_roi_changed(self, rect):
        self.roi = rect

    @Slot()
    def on_language_changed(self):
        self.trigger_preview_update()
        selected_code = self.combo_lang.currentData()
        if not selected_code: return

        if not self.ocr_manager.is_language_ready(selected_code):
            reply = QMessageBox.question(
                self, 
                self.tr("ocr_msg_download_title"), 
                self.tr("ocr_msg_download_text", selected_code), 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.start_download(selected_code)

    def start_download(self, lang_code):
        targets = lang_code.split('+')
        self.btn_test.setEnabled(False)
        self.btn_ok.setEnabled(False)
        
        msg = self.tr("ocr_msg_downloading", lang_code)
        self.text_log.setText(msg)
        
        self.ocr_manager.download_progress.connect(self.on_download_progress)
        self.ocr_manager.download_finished.connect(self.on_download_finished)
        self.ocr_manager.download_languages(targets)

    @Slot(str, int)
    def on_download_progress(self, filename, percent):
        pass

    @Slot(bool, str)
    def on_download_finished(self, success, msg):
        try:
            self.ocr_manager.download_progress.disconnect(self.on_download_progress)
            self.ocr_manager.download_finished.disconnect(self.on_download_finished)
        except: pass

        self.btn_test.setEnabled(True)
        self.btn_ok.setEnabled(True)
        
        if success:
            comp_msg = self.tr("ocr_msg_download_complete")
            self.text_log.setHtml(f"<font color='green'>{comp_msg}</font>")
            self.update_preview_image()
        else:
            fail_msg = self.tr("ocr_msg_download_failed", msg)
            self.text_log.setHtml(f"<font color='red'>{fail_msg}</font>")
            
            err_title = self.tr("error_title_download_failed")
            if err_title == "error_title_download_failed": err_title = "Download Failed"
            QMessageBox.warning(self, err_title, fail_msg)

    @Slot()
    def run_ocr_test(self):
        if not self.roi:
            QMessageBox.warning(self, "Warning", self.tr("ocr_warn_select_area"))
            return

        self.update_preview_image()
        self.text_log.clear()
        self.text_log.setText(self.tr("ocr_log_processing"))
        self.btn_test.setEnabled(False)

        self.worker = self.ocr_manager.create_worker(
            self.original_image, 
            self.config, 
            self.roi
        )
        self.worker.finished.connect(self.on_ocr_finished)
        self.worker.error.connect(self.on_ocr_error)
        self.worker.start()

    @Slot(str, object)
    def on_ocr_finished(self, raw_text, numeric_value):
        self.btn_test.setEnabled(True)
        log_str = f"<b>RAW:</b> '{raw_text}'"
        if self.config.numeric_mode:
            result_str = str(numeric_value) if numeric_value is not None else "NaN"
            log_str += f" | <b>Num:</b> {result_str}"

        target_val = self.input_target_value.text()
        operator = self.combo_operator.currentData()
        
        temp_settings = {
            "enabled": True,
            "roi": self.roi,
            "config": {
                "scale": self.config.scale,
                "threshold": self.config.threshold,
                "invert": self.config.invert,
                "numeric_mode": self.config.numeric_mode,
                "lang": self.config.lang
            },
            "condition": {
                "operator": operator,
                "value": target_val
            }
        }
        
        # ★★★ 修正: 戻り値が4つになったのでアンパックを修正 ★★★
        success, log_msg, _, _ = OCRRuntimeEvaluator.evaluate(
            self.original_image, 
            (0, 0), 
            temp_settings,
            item_settings={}, 
            current_scale=1.0 
        )
        
        color = "green" if success else "orange"
        status_text = "PASS" if success else "FAIL"
        log_str += f" | <font color='{color}'><b>JUDGE:</b> {status_text}</font>"
        self.text_log.setHtml(log_str)

    @Slot(str)
    def on_ocr_error(self, error_msg):
        self.btn_test.setEnabled(True)
        self.text_log.setHtml(f"<font color='red'>Error: {error_msg}</font>")

    def get_result(self):
        self.config.scale = float(self.combo_scale.currentText().replace('x', ''))
        self.config.threshold = self.slider_thresh.value()
        self.config.invert = self.chk_invert.isChecked()
        self.config.lang = self.combo_lang.currentData()
        self.config.numeric_mode = self.chk_numeric.isChecked()
        
        target_val = self.input_target_value.text()
        if self.config.numeric_mode:
            try:
                target_val = float(target_val)
            except:
                pass 
                
        condition_data = {
            "operator": self.combo_operator.currentData(),
            "value": target_val
        }
        
        return self.config, self.roi, condition_data, self.chk_enable.isChecked()

    @Slot()
    def show_tesseract_guide(self):
        title = self.tr("ocr_guide_title")
        if title == "ocr_guide_title": title = "Tesseract Installation Guide"
        
        info_text = ""
        is_windows = sys.platform == 'win32'
        
        if is_windows:
            info_text = self.tr("ocr_guide_windows_content")
            if info_text == "ocr_guide_windows_content":
                info_text = (
                    "<h3>Windows - Tesseract OCR Installation</h3>"
                    "<p>Please install Tesseract OCR engine.</p>"
                    "<ol>"
                    "<li>Download the installer (<b>tesseract-ocr-w64-setup-x.x.x.exe</b>) from the link below.</li>"
                    "<li>Run the installer.</li>"
                    "<li><b>Important:</b> Check [Script Data] -> [Japanese] components during installation.</li>"
                    "<li>Restart this application.</li>"
                    "</ol>"
                )
        else:
            info_text = self.tr("ocr_guide_linux_content")
            if info_text == "ocr_guide_linux_content":
                info_text = (
                    "<h3>Linux - Tesseract OCR Installation</h3>"
                    "<p>Please install Tesseract OCR using your package manager.</p>"
                    "<pre style='background-color:#eee; padding:5px;'>sudo apt install tesseract-ocr</pre>"
                )

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(600, 450)
        
        layout = QVBoxLayout(dialog)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setHtml(info_text)
        layout.addWidget(text_edit)
        
        if is_windows:
            btn_text = self.tr("ocr_guide_btn_download")
            if btn_text == "ocr_guide_btn_download": btn_text = "Open Download Page"
            
            btn_open_url = QPushButton(btn_text)
            btn_open_url.clicked.connect(lambda: webbrowser.open("https://github.com/UB-Mannheim/tesseract/wiki"))
            layout.addWidget(btn_open_url)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.exec()
