# timer_ui.py
# タイマー連動クリック機能の設定画面 (v3.4 UI修正版 + IME対策 + フリーズ対策)
# ★★★ 修正: 説明欄の入力時にもマウスフックを一時停止する ★★★

import sys
import time
from datetime import datetime, timedelta, date
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, 
    QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QMessageBox,
    QAbstractItemView, QLineEdit, QSizePolicy, QStyledItemDelegate,
    QTimeEdit, QStyle, QTabWidget, QTextEdit
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QTime, QEvent
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QMouseEvent, QFont, QBrush

from custom_widgets import ScaledPixmapLabel
from custom_input_dialog import ask_string_custom

# --- カスタムデリゲート: 時間 (+秒) 用 ---
class OffsetSpinBoxDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if index.row() == 0:
            return None
        editor = QDoubleSpinBox(parent)
        editor.setRange(0, 86400) 
        editor.setDecimals(1)
        editor.setSingleStep(1.0)
        return editor
    
    def setEditorData(self, editor, index):
        val = float(index.model().data(index, Qt.EditRole))
        editor.setValue(val)
        
    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)

# --- カスタムデリゲート: 実行時刻 (HH:MM:SS) 用 ---
class TimeEditDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QTimeEdit(parent)
        editor.setDisplayFormat("HH:mm:ss")
        return editor
    
    def setEditorData(self, editor, index):
        time_str = index.model().data(index, Qt.EditRole)
        qtime = QTime.fromString(time_str, "HH:mm:ss")
        editor.setTime(qtime)
        
    def setModelData(self, editor, model, index):
        time_str = editor.time().toString("HH:mm:ss")
        model.setData(index, time_str, Qt.EditRole)

# --- チェックボックス列用デリゲート (ハイライト無効化) ---
class NoHighlightDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option.state &= ~QStyle.State_Selected
        super().paint(painter, option, index)

class ClickPreviewLabel(ScaledPixmapLabel):
    positionClicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CrossCursor)
        self.markers = [] 
        self.selected_id = None

    def set_markers(self, markers, selected_id=None):
        self.markers = markers
        self.selected_id = selected_id
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if self._pixmap.isNull(): return
        label_size = self.size()
        if self._pixmap.width() == 0 or self._pixmap.height() == 0: return
        scaled_pixmap = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        offset_x = (label_size.width() - scaled_pixmap.width()) // 2
        offset_y = (label_size.height() - scaled_pixmap.height()) // 2
        click_x = event.pos().x() - offset_x
        click_y = event.pos().y() - offset_y
        
        if click_x < 0 or click_x >= scaled_pixmap.width() or click_y < 0 or click_y >= scaled_pixmap.height(): return
        
        scale_x = self._pixmap.width() / scaled_pixmap.width()
        scale_y = self._pixmap.height() / scaled_pixmap.height()
        final_x = int(click_x * scale_x)
        final_y = int(click_y * scale_y)
        self.positionClicked.emit(final_x, final_y)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.markers or self._pixmap.isNull(): return
        painter = QPainter(self)
        label_size = self.size()
        scaled_pixmap = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if scaled_pixmap.width() == 0 or scaled_pixmap.height() == 0: return
        offset_x = (label_size.width() - scaled_pixmap.width()) // 2
        offset_y = (label_size.height() - scaled_pixmap.height()) // 2
        scale_x = scaled_pixmap.width() / self._pixmap.width()
        scale_y = scaled_pixmap.height() / self._pixmap.height()
        
        font = painter.font(); font.setBold(True); font.setPointSize(10); painter.setFont(font)

        for m in self.markers:
            if not m['enabled'] and m['id'] != self.selected_id: continue 

            wx = int(m['x'] * scale_x) + offset_x
            wy = int(m['y'] * scale_y) + offset_y
            
            if m['id'] == self.selected_id:
                painter.setPen(QPen(QColor(255, 255, 0), 3)) # 黄色
                painter.setBrush(QColor(255, 255, 0, 150))
            elif m['enabled']:
                painter.setPen(QPen(QColor(255, 0, 0), 2)) # 赤
                painter.setBrush(QColor(255, 0, 0, 100))
            else:
                painter.setPen(QPen(QColor(150, 150, 150), 1)) # グレー
                painter.setBrush(QColor(100, 100, 100, 50))

            painter.drawEllipse(wx - 6, wy - 6, 12, 12)
            
            painter.setPen(QPen(QColor(255, 255, 255), 4))
            painter.drawText(wx + 10, wy + 5, str(m['id']))
            painter.setPen(QPen(QColor(0, 0, 0) if not m['enabled'] else QColor(255, 0, 0), 2))
            painter.drawText(wx + 10, wy + 5, str(m['id']))

class TimerSettingsDialog(QDialog):
    def __init__(self, item_path, item_name, current_settings, locale_manager, parent=None, core_engine=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.item_path = item_path
        self.item_name = item_name
        self.settings = current_settings.get('timer_mode', {})
        self.locale_manager = locale_manager
        self.core_engine = core_engine # ★追加: コアエンジンの保持
        
        title_fmt = self.locale_manager.tr("timer_settings_title", item_name)
        if title_fmt == "timer_settings_title": title_fmt = f"Timer Settings - {item_name}"
        self.setWindowTitle(title_fmt)
        
        self.resize(1150, 780)
        
        # --- データ初期化 ---
        loaded_actions = self.settings.get('actions', [])
        
        self.fixed_actions = []
        for i in range(10):
            found = next((a for a in loaded_actions if a.get('id') == i + 1), None)
            if found:
                action = found.copy()
                action['enabled'] = found.get('enabled', False)
                if 'display_time' not in action:
                    action['display_time'] = "20:00:00"
            else:
                action = {
                    'id': i + 1,
                    'offset_sec': 0.0,
                    'display_time': "20:00:00", 
                    'x': 0, 'y': 0,
                    'description': '',
                    'enabled': False
                }
            self.fixed_actions.append(action)

        self.fixed_actions[0]['offset_sec'] = 0.0
        self._recalc_all_times_from_anchor()

        self.current_selected_row = 0 
        
        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(1000)
        self.debounce_timer.timeout.connect(self.save_settings_internal)
        
        self.setup_ui()
        self.load_data_to_ui()
        
    def setup_ui(self):
        lm = self.locale_manager.tr
        layout = QHBoxLayout(self)
        
        # --- 左側 (タブ化) ---
        self.left_tabs = QTabWidget()
        
        self.left_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 2px solid #90a4ae;
                top: -1px;
                background-color: #eceff1;
            }
            QTabBar::tab {
                background: #cfd8dc;
                border: 1px solid #b0bec5;
                padding: 6px 12px;
                margin-right: 2px;
                color: #455a64;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background: #eceff1;
                border-bottom-color: #eceff1;
                color: #263238;
                font-weight: bold;
            }
            QTabBar::tab:hover {
                background: #dce775;
            }
        """)
        
        # タブ1: プレビュー & 設定
        self.tab_preview_widget = QWidget()
        preview_layout = QVBoxLayout(self.tab_preview_widget)
        preview_layout.setContentsMargins(10, 10, 10, 10) 
        
        self.preview_label = ClickPreviewLabel()
        self.preview_label.setStyleSheet("border: 2px solid #546e7a; background-color: #263238;") 
        self.preview_label.setMinimumSize(600, 450)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        pixmap = QPixmap(str(self.item_path))
        if not pixmap.isNull(): self.preview_label.set_pixmap(pixmap)
        else: self.preview_label.setText("Image Load Error")
        self.preview_label.positionClicked.connect(self.on_preview_clicked)
        
        lbl_preview = QLabel(lm("timer_preview_instruction"))
        lbl_preview.setStyleSheet("font-weight: bold; color: #37474f; font-size: 13px; margin-bottom: 5px;")
        
        desc_layout = QHBoxLayout()
        desc_label = QLabel(lm("timer_header_desc") + ":")
        desc_layout.addWidget(desc_label)
        
        # IME対策: 説明入力欄
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("(Click to edit)")
        # イベントフィルタをセットしてクリックを監視
        self.desc_input.installEventFilter(self)
        self.desc_input.textChanged.connect(self.on_desc_changed)
        
        desc_layout.addWidget(self.desc_input)
        
        preview_layout.addWidget(lbl_preview)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addLayout(desc_layout)
        
        # タブ2: 使い方ガイド
        self.tab_usage_widget = QWidget()
        usage_layout = QVBoxLayout(self.tab_usage_widget)
        
        self.usage_text_edit = QTextEdit()
        self.usage_text_edit.setReadOnly(True)
        self.usage_text_edit.setHtml(lm("timer_tab_usage_content"))
        
        usage_layout.addWidget(self.usage_text_edit)
        
        self.left_tabs.addTab(self.tab_preview_widget, lm("timer_tab_preview"))
        self.left_tabs.addTab(self.tab_usage_widget, lm("timer_tab_usage"))
        
        layout.addWidget(self.left_tabs, 2)
        
        # --- 右側 (設定) ---
        right_layout = QVBoxLayout()
        
        grp_basic = QGroupBox(lm("timer_basic_settings"))
        form_layout = QVBoxLayout(); form_layout.setSpacing(8)
        
        self.enable_cb = QCheckBox(lm("timer_enable"))
        self.enable_cb.setStyleSheet("font-weight: bold; color: #37474f;")
        self.enable_cb.stateChanged.connect(self.trigger_save)
        form_layout.addWidget(self.enable_cb)
        
        # アプローチ設定
        hbox_app = QHBoxLayout()
        hbox_app.addWidget(QLabel(lm("timer_approach_time")))
        self.approach_spin = QSpinBox(); self.approach_spin.setRange(1, 60); self.approach_spin.setSuffix(" min")
        self.approach_spin.setToolTip(lm("timer_approach_tooltip"))
        self.approach_spin.valueChanged.connect(self.update_approach_info_label)
        self.approach_spin.valueChanged.connect(self.trigger_save)
        hbox_app.addWidget(self.approach_spin)
        form_layout.addLayout(hbox_app)
        
        # インターバル設定
        hbox_int = QHBoxLayout()
        hbox_int.addWidget(QLabel(lm("timer_interval")))
        self.interval_spin = QDoubleSpinBox(); self.interval_spin.setRange(0.1, 60.0); self.interval_spin.setSingleStep(0.1); self.interval_spin.setSuffix(" sec")
        self.interval_spin.setToolTip(lm("timer_interval_tooltip"))
        self.interval_spin.valueChanged.connect(self.trigger_save)
        hbox_int.addWidget(self.interval_spin)
        form_layout.addLayout(hbox_int)
        
        self.approach_info_label = QLabel("Lock Start: --:--:--")
        self.approach_info_label.setStyleSheet("color: #0277bd; font-weight: bold; margin-left: 10px;")
        form_layout.addWidget(self.approach_info_label)
        
        grp_basic.setLayout(form_layout)
        right_layout.addWidget(grp_basic)
        
        grp_list = QGroupBox(lm("timer_actions_group"))
        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(4, 8, 4, 4)
        
        self.table = QTableWidget(); self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "On", "ID", lm("timer_header_time"), "Exec Time (HH:mm:ss)"
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents) 
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents) 
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents) 
        header.setSectionResizeMode(3, QHeaderView.Stretch)          
        
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        
        self.table.setItemDelegateForColumn(0, NoHighlightDelegate(self.table))
        self.table.setItemDelegateForColumn(2, OffsetSpinBoxDelegate(self.table))
        self.table.setItemDelegateForColumn(3, TimeEditDelegate(self.table))
        
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.table.cellChanged.connect(self.on_table_cell_changed)
        
        list_layout.addWidget(self.table)
        
        self.coord_label = QLabel("Coord: (--, --)")
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #424242; margin-top: 5px;")
        list_layout.addWidget(self.coord_label)
        
        grp_list.setLayout(list_layout)
        right_layout.addWidget(grp_list, 1)
        
        bbox = QHBoxLayout()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setMinimumHeight(40)
        
        bbox.addStretch(); bbox.addWidget(self.close_btn)
        right_layout.addLayout(bbox)
        
        layout.addLayout(right_layout, 1)

    def eventFilter(self, source, event):
        if source == self.desc_input and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                self._open_desc_input_dialog()
                return True
        return super().eventFilter(source, event)

    def _open_desc_input_dialog(self):
        """説明入力用のTkinterダイアログを開く"""
        current_text = self.desc_input.text()
        lm = self.locale_manager.tr
        
        # ★★★ フリーズ対策: リスナー停止 ★★★
        if self.core_engine:
            with self.core_engine.temporary_listener_pause():
                new_text, ok = ask_string_custom(
                    self, 
                    lm("timer_desc_dialog_title") if lm("timer_desc_dialog_title") != "timer_desc_dialog_title" else "Edit Description",
                    lm("timer_header_desc"),
                    current_text
                )
        else:
            # 万が一CoreEngineがない場合のフォールバック
            new_text, ok = ask_string_custom(
                self, 
                "Edit Description", 
                "Description", 
                current_text
            )
        
        if ok:
            self.desc_input.setText(new_text)

    def _add_seconds_to_time_str(self, time_str, seconds):
        try:
            t = datetime.strptime(time_str, "%H:%M:%S")
            new_t = t + timedelta(seconds=seconds)
            return new_t.strftime("%H:%M:%S")
        except ValueError:
            return time_str

    def _calc_offset_between_time_strs(self, base_time_str, target_time_str):
        try:
            t1 = datetime.strptime(base_time_str, "%H:%M:%S")
            t2 = datetime.strptime(target_time_str, "%H:%M:%S")
            diff = (t2 - t1).total_seconds()
            if diff < 0: diff += 86400 
            return diff
        except ValueError:
            return 0.0

    def _recalc_all_times_from_anchor(self):
        anchor_time = self.fixed_actions[0]['display_time']
        for action in self.fixed_actions:
            offset = action['offset_sec']
            action['display_time'] = self._add_seconds_to_time_str(anchor_time, offset)

    def load_data_to_ui(self):
        self.enable_cb.blockSignals(True)
        self.approach_spin.blockSignals(True)
        self.interval_spin.blockSignals(True)
        
        self.enable_cb.setChecked(self.settings.get('enabled', False))
        self.approach_spin.setValue(self.settings.get('approach_time', 3)) 
        self.interval_spin.setValue(self.settings.get('sequence_interval', 1.0))
        
        self.enable_cb.blockSignals(False)
        self.approach_spin.blockSignals(False)
        self.interval_spin.blockSignals(False)
        
        self.refresh_table()
        self.update_approach_info_label()

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(10)
        
        markers = []
        
        for i, action in enumerate(self.fixed_actions):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk_item.setCheckState(Qt.Checked if action['enabled'] else Qt.Unchecked)
            self.table.setItem(i, 0, chk_item)
            
            id_item = QTableWidgetItem(str(action['id']))
            id_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            id_item.setTextAlignment(Qt.AlignCenter)
            if i == 0: id_item.setBackground(QColor("#e3f2fd")) 
            self.table.setItem(i, 1, id_item)
            
            offset_item = QTableWidgetItem(str(action['offset_sec']))
            offset_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if i == 0: offset_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable) 
            self.table.setItem(i, 2, offset_item)
            
            time_item = QTableWidgetItem(action['display_time'])
            time_item.setTextAlignment(Qt.AlignCenter)
            if i == 0: time_item.setFont(QFont("Arial", 10, QFont.Bold)) 
            self.table.setItem(i, 3, time_item)
            
            markers.append({'id': action['id'], 'x': action['x'], 'y': action['y'], 'enabled': action['enabled']})
            
        self.table.blockSignals(False)
        
        selected_row = self.table.currentRow()
        selected_id = self.fixed_actions[selected_row]['id'] if selected_row >= 0 else None
        self.preview_label.set_markers(markers, selected_id)
        
        if selected_row >= 0:
            self.desc_input.blockSignals(True)
            self.desc_input.setText(self.fixed_actions[selected_row].get('description', ''))
            self.desc_input.blockSignals(False)
            self.update_coord_label(selected_row)

    def on_table_selection_changed(self):
        row = self.table.currentRow()
        if row >= 0:
            markers = []
            for action in self.fixed_actions:
                markers.append({'id': action['id'], 'x': action['x'], 'y': action['y'], 'enabled': action['enabled']})
            self.preview_label.set_markers(markers, self.fixed_actions[row]['id'])
            
            self.desc_input.blockSignals(True)
            self.desc_input.setText(self.fixed_actions[row].get('description', ''))
            self.desc_input.blockSignals(False)
            
            self.update_coord_label(row)

    def on_table_cell_changed(self, row, column):
        if row < 0 or row >= len(self.fixed_actions): return
        
        if column == 0: 
            state = self.table.item(row, 0).checkState()
            self.fixed_actions[row]['enabled'] = (state == Qt.Checked)
            self.refresh_table() 
            self.trigger_save()
            
        elif column == 2: 
            if row == 0: return 
            try:
                new_offset = float(self.table.item(row, 2).text())
                self.fixed_actions[row]['offset_sec'] = new_offset
                
                anchor_time = self.fixed_actions[0]['display_time']
                new_time = self._add_seconds_to_time_str(anchor_time, new_offset)
                self.fixed_actions[row]['display_time'] = new_time
                
                self.table.blockSignals(True)
                self.table.item(row, 3).setText(new_time)
                self.table.blockSignals(False)
                
                self.trigger_save()
            except ValueError: pass

        elif column == 3: 
            new_time_str = self.table.item(row, 3).text()
            
            if row == 0:
                self.fixed_actions[0]['display_time'] = new_time_str
                self._recalc_all_times_from_anchor()
                self.refresh_table()
                self.update_approach_info_label() 
            else:
                self.fixed_actions[row]['display_time'] = new_time_str
                anchor_time = self.fixed_actions[0]['display_time']
                new_offset = self._calc_offset_between_time_strs(anchor_time, new_time_str)
                self.fixed_actions[row]['offset_sec'] = new_offset
                
                self.table.blockSignals(True)
                self.table.item(row, 2).setText(str(new_offset))
                self.table.blockSignals(False)
            
            self.trigger_save()

    def on_desc_changed(self, text):
        row = self.table.currentRow()
        if row >= 0:
            self.fixed_actions[row]['description'] = text
            self.trigger_save()

    def on_preview_clicked(self, x, y):
        row = self.table.currentRow()
        if row >= 0:
            self.fixed_actions[row]['x'] = x
            self.fixed_actions[row]['y'] = y
            self.update_coord_label(row)
            
            markers = [{'id': a['id'], 'x': a['x'], 'y': a['y'], 'enabled': a['enabled']} for a in self.fixed_actions]
            self.preview_label.set_markers(markers, self.fixed_actions[row]['id'])
            self.trigger_save()
        else:
            self.table.selectRow(0)
            self.on_preview_clicked(x, y)

    def update_approach_info_label(self):
        anchor_time_str = self.fixed_actions[0]['display_time']
        approach_min = self.approach_spin.value()
        
        try:
            t_anchor = datetime.strptime(anchor_time_str, "%H:%M:%S")
            t_lock = t_anchor - timedelta(minutes=approach_min)
            lock_str = t_lock.strftime("%H:%M:%S")
            
            self.approach_info_label.setText(f"Start(ID1): {anchor_time_str} / Lock: {lock_str} (-{approach_min}m)")
        except ValueError:
            self.approach_info_label.setText("Time Format Error")

    def update_coord_label(self, row):
        if row < 0:
            self.coord_label.setText("Coord: (--, --)")
            return
        x = self.fixed_actions[row]['x']
        y = self.fixed_actions[row]['y']
        self.coord_label.setText(f"Coord: ({x}, {y})")

    def trigger_save(self):
        self.debounce_timer.start()

    def save_settings_internal(self):
        pass

    def get_settings(self):
        return {
            'enabled': self.enable_cb.isChecked(),
            'approach_time': self.approach_spin.value(),
            'sequence_interval': self.interval_spin.value(),
            'actions': self.fixed_actions
        }
