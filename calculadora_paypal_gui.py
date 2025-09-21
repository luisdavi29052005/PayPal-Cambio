# -*- coding: utf-8 -*-
"""
Calculadora de Câmbio PayPal Pro
Versão: 3.1
Autor: Gemini & Davi Ferrer

Aplicação de desktop com um design limpo e moderno, inspirado em layouts
de conversão de moeda, para calcular taxas de câmbio do PayPal em tempo real.
"""
import sys
import requests
import threading
import locale
import json
import os
from datetime import datetime
from itertools import cycle

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QFrame, QGraphicsDropShadowEffect,
    QSizePolicy
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QSize
)
from PySide6.QtGui import (
    QFont, QColor, QFontDatabase
)

# =============================================================================
# 2. LÓGICA DE NEGÓCIO (MODELO E SERVIÇOS)
# =============================================================================
PAYPAL_FEES = {
    "USD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
    "EUR": {"fee_percent": 6.40, "fixed_fee": 0.35, "spread_percent": 3.50},
    "GBP": {"fee_percent": 6.40, "fixed_fee": 0.20, "spread_percent": 3.50},
    "JPY": {"fee_percent": 6.40, "fixed_fee": 40.00, "spread_percent": 3.50},
    "CAD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
    "AUD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
}
DEFAULT_FEES = {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 4.50}

class SettingsManager:
    """Gerencia o carregamento e salvamento de configurações em um arquivo JSON."""
    def __init__(self, filename="calculator_pro_settings.json"):
        self.filepath = os.path.join(os.path.expanduser("~"), filename)
        self.defaults = {"last_currency": "USD"}
        self.settings = self._load()

    def _load(self):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self.defaults.copy()

    def _save(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
        except IOError:
            pass

    def get(self, key):
        return self.settings.get(key, self.defaults.get(key))

    def set(self, key, value):
        self.settings[key] = value
        self._save()

class ApiService(QObject):
    """Serviço para buscar taxas de câmbio da API em uma thread separada."""
    result_ready = Signal(dict)
    BASE_URL = "https://economia.awesomeapi.com.br/json/last/{}-BRL"
    
    def __init__(self):
        super().__init__()
        self.rate_cache = {}

    def get_exchange_rate(self, currency):
        if currency == "BRL":
            self.result_ready.emit({'status': 'success', 'rate': 1.0, 'currency': currency})
            return
        
        if currency in self.rate_cache:
            self.result_ready.emit({'status': 'success', 'rate': self.rate_cache[currency], 'currency': currency})
            return
        
        self.result_ready.emit({'status': 'loading', 'currency': currency})
        threading.Thread(target=self._fetch_rate, args=(currency,), daemon=True).start()

    def _fetch_rate(self, currency):
        try:
            url = self.BASE_URL.format(currency)
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            rate = float(data[f"{currency}BRL"]['bid'])
            self.rate_cache[currency] = rate
            self.result_ready.emit({'status': 'success', 'rate': rate, 'currency': currency})
        except requests.RequestException:
            self.result_ready.emit({'status': 'error', 'message': "Erro de conexão."})
        except (KeyError, ValueError):
            self.result_ready.emit({'status': 'error', 'message': f"Moeda '{currency}' inválida."})

class CalculatorModel(QObject):
    """Mantém o estado da calculadora e executa a lógica de cálculo."""
    updated = Signal()

    def __init__(self, settings_manager):
        super().__init__()
        self.settings = settings_manager
        self.input_str = "1000.00"
        
        self.currency_list = list(PAYPAL_FEES.keys())
        self.currency_cycle = cycle(self.currency_list)
        self.currency = self.settings.get('last_currency')
        
        # Sincroniza o cycle com a moeda salva
        while next(self.currency_cycle) != self.currency:
            pass
            
        self.current_rate = 0
        self.calculation_result = {}

    def next_currency(self):
        self.currency = next(self.currency_cycle)
        self.settings.set('last_currency', self.currency)
        self.updated.emit()

    def perform_calculation(self, exchange_rate):
        self.current_rate = exchange_rate
        try:
            value = float(self.input_str)
        except ValueError:
            self.calculation_result = {'error': "Entrada inválida."}
            self.updated.emit()
            return

        fees = PAYPAL_FEES.get(self.currency, DEFAULT_FEES)
        fee_percent = fees["fee_percent"] / 100
        fixed_fee = fees["fixed_fee"]
        spread_percent = fees["spread_percent"] / 100
        
        # Cálculos
        rate_with_spread = exchange_rate * (1 - spread_percent)
        paypal_fee_foreign = (value * fee_percent) + fixed_fee
        net_after_fees = max(0, value - paypal_fee_foreign)
        final_brl = net_after_fees * rate_with_spread
        
        # Novo: Cálculo da perda total
        value_at_raw_rate_brl = value * exchange_rate
        total_loss_brl = value_at_raw_rate_brl - final_brl

        self.calculation_result = {
            'final_value_brl': final_brl,
            'exchange_rate': exchange_rate,
            'rate_with_spread': rate_with_spread,
            'paypal_fee_foreign': paypal_fee_foreign,
            'base_value': value,
            'total_loss_brl': total_loss_brl,
        }
        
        self.updated.emit()

# =============================================================================
# 4. VIEW PRINCIPAL (JANELA DA APLICAÇÃO)
# =============================================================================
class CalculatorView(QMainWindow):
    """A janela principal da aplicação, com o novo design."""
    def __init__(self, model, controller_callback):
        super().__init__()
        self.model = model
        self.controller_callback = controller_callback

        self.setWindowTitle("Calculadora de Câmbio PayPal")
        self.setMinimumSize(620, 480)
        self.resize(640, 500)
        
        QFontDatabase.addApplicationFont(":/fonts/Inter-Regular.ttf")
        self.setFont(QFont("Inter", 10))

        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(30, 20, 30, 30)
        main_layout.setSpacing(20)

        self._create_amount_area()
        
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(25)
        
        self._create_keypad_area()
        self._create_result_area()

        bottom_layout.addWidget(self.keypad_frame, 1)
        bottom_layout.addWidget(self.result_frame, 1)

        main_layout.addWidget(self.amount_frame)
        main_layout.addLayout(bottom_layout)

        self._apply_styles()
        self.model.updated.connect(self.update_view)
        self.update_view()

    def _create_amount_area(self):
        self.amount_frame = QFrame()
        self.amount_frame.setObjectName("AmountFrame")
        self.amount_frame.setFixedHeight(80)
        
        layout = QHBoxLayout(self.amount_frame)
        layout.setContentsMargins(25, 0, 25, 0)
        
        amount_label = QLabel("Amount")
        amount_label.setObjectName("SectionTitleLabel")
        
        self.input_line = QLabel(self.model.input_str)
        self.input_line.setObjectName("InputLine")
        self.input_line.setAlignment(Qt.AlignRight)

        self.currency_button = QPushButton(self.model.currency)
        self.currency_button.setObjectName("CurrencyButton")
        self.currency_button.setFixedWidth(80)
        self.currency_button.clicked.connect(lambda: self.controller_callback('currency_change'))

        input_v_layout = QVBoxLayout()
        input_v_layout.setSpacing(0)
        input_v_layout.addWidget(amount_label)
        input_v_layout.addWidget(self.input_line)
        
        layout.addLayout(input_v_layout)
        layout.addStretch()
        layout.addWidget(self.currency_button)

    def _create_keypad_area(self):
        self.keypad_frame = QFrame()
        self.keypad_frame.setObjectName("KeypadFrame")
        layout = QGridLayout(self.keypad_frame)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        keys = [
            ('1', 0, 0), ('2', 0, 1), ('3', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('7', 2, 0), ('8', 2, 1), ('9', 2, 2),
            ('.', 3, 0), ('0', 3, 1), ('DEL', 3, 2)
        ]

        for text, r, c in keys:
            button = QPushButton(text)
            button.setObjectName("KeypadButton")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.clicked.connect(lambda _, k=text: self.controller_callback('key_press', k))
            layout.addWidget(button, r, c)

    def _create_result_area(self):
        self.result_frame = QFrame()
        self.result_frame.setObjectName("ResultCard")
        
        shadow = QGraphicsDropShadowEffect(blurRadius=30, xOffset=0, yOffset=4)
        shadow.setColor(QColor(0, 0, 0, 40))
        self.result_frame.setGraphicsEffect(shadow)
        
        layout = QVBoxLayout(self.result_frame)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(8)

        result_title = QLabel("Result")
        result_title.setObjectName("SectionTitleLabel")
        
        result_line_layout = QHBoxLayout()
        self.result_value_label = QLabel("0.00")
        self.result_value_label.setObjectName("ResultValueLabel")
        self.result_currency_label = QLabel("BRL")
        self.result_currency_label.setObjectName("ResultCurrencyLabel")
        
        result_line_layout.addWidget(self.result_value_label)
        result_line_layout.addStretch()
        result_line_layout.addWidget(self.result_currency_label)
        
        self.rate_label = QLabel("Câmbio: 1 USD = 0,0000 BRL")
        self.rate_label.setObjectName("DetailsLabel")
        
        self.fee_label = QLabel("Taxa PayPal: 0,00 USD")
        self.fee_label.setObjectName("DetailsLabel")

        # Novo: Label para mostrar a perda
        self.loss_label = QLabel("Você perde: R$ 0,00")
        self.loss_label.setObjectName("LossLabel")

        self.save_button = QPushButton("Salvar e Limpar")
        self.save_button.setObjectName("ActionButton")
        self.save_button.setFixedHeight(50)
        self.save_button.clicked.connect(lambda: self.controller_callback('key_press', '='))

        layout.addWidget(result_title)
        layout.addLayout(result_line_layout)
        layout.addStretch()
        layout.addWidget(self.rate_label)
        layout.addWidget(self.fee_label)
        layout.addWidget(self.loss_label)
        layout.addSpacing(10)
        layout.addWidget(self.save_button)

    def update_view(self):
        """Atualiza a UI com base no estado do modelo."""
        try:
            locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
        except locale.Error:
            locale.setlocale(locale.LC_ALL, '')
        
        try:
            input_val = float(self.model.input_str)
            # Formata o input para exibição, mas o modelo mantém o string puro
            self.input_line.setText(locale.format_string("%.2f", input_val, grouping=True))
        except ValueError:
            # Se o input não for um float válido (ex: "100."), exibe como está
            self.input_line.setText(self.model.input_str)

        self.currency_button.setText(self.model.currency)

        res = self.model.calculation_result
        if 'error' in res or not res:
            self.result_value_label.setText("0,00")
            self.loss_label.setText("Você perde: R$ 0,00")
            return

        final_brl = res.get('final_value_brl', 0)
        self.result_value_label.setText(locale.format_string("%.2f", final_brl, grouping=True))
        
        rate = res.get('exchange_rate', 0)
        self.rate_label.setText(f"Câmbio: 1 {self.model.currency} = {locale.format_string('%.4f', rate, grouping=True)} BRL")
        
        fee = res.get('paypal_fee_foreign', 0)
        self.fee_label.setText(f"Taxa PayPal: {locale.format_string('%.2f', fee, grouping=True)} {self.model.currency}")

        total_loss = res.get('total_loss_brl', 0)
        self.loss_label.setText(f"Você perde: {locale.currency(total_loss, grouping=True)}")
    
    def _apply_styles(self):
        self.setStyleSheet("""
            #CentralWidget {
                background-color: #f7f8fa;
            }
            #SectionTitleLabel {
                color: #6c727f;
                font-size: 11pt;
                font-weight: 500;
            }
            #AmountFrame {
                background-color: #ffffff;
                border-radius: 12px;
                border: 1px solid #e0e5eb;
            }
            #InputLine {
                font-size: 24pt;
                font-weight: 600;
                color: #212529;
                border: none;
            }
            #CurrencyButton {
                font-size: 12pt;
                font-weight: 600;
                color: #212529;
                background-color: #f7f8fa;
                border: 1px solid #e0e5eb;
                border-radius: 8px;
            }
            #KeypadFrame {
                background-color: #e8ecf1;
                border-radius: 16px;
            }
            #KeypadButton {
                font-size: 15pt;
                font-weight: 600;
                color: #2d343c;
                background-color: #ffffff;
                border-radius: 30px; /* Circular */
                border: 1px solid #e0e5eb;
                min-height: 60px;
            }
            #KeypadButton:pressed {
                background-color: #f0f0f0;
            }
            #ResultCard {
                background-color: #ffffff;
                border-radius: 16px;
            }
            #ResultValueLabel {
                font-size: 32pt;
                font-weight: 700;
                color: #2d343c;
            }
            #ResultCurrencyLabel {
                font-size: 20pt;
                font-weight: 600;
                color: #007bff;
                padding-top: 8px; /* Alinhamento visual */
            }
            #DetailsLabel {
                color: #6c727f;
                font-size: 10pt;
            }
            #LossLabel {
                color: #d93025; /* Vermelho para destaque */
                font-size: 10pt;
                font-weight: 500;
            }
            #ActionButton {
                font-size: 12pt;
                font-weight: 600;
                color: #ffffff;
                background-color: #007bff;
                border: none;
                border-radius: 12px;
            }
            #ActionButton:hover {
                background-color: #0069d9;
            }
            #ActionButton:pressed {
                background-color: #0056b3;
            }
        """)

# =============================================================================
# 5. CONTROLADOR
# =============================================================================
class CalculatorController:
    """Manipula a lógica de entrada do usuário e coordena Modelo, View e Serviços."""
    def __init__(self, model, view, api_service):
        self.model = model
        self.view = view
        self.api = api_service
        self.api.result_ready.connect(self._on_api_result)
        self.needs_reset = True

    def handle_action(self, action_type, value=None):
        actions = {
            'key_press': self._handle_key_press,
            'currency_change': self._handle_currency_change,
        }
        if action_type in actions:
            actions[action_type](value)

    def _on_api_result(self, result):
        if result['status'] == 'success':
            self.model.perform_calculation(result['rate'])
        elif result['status'] == 'error':
            print(result['message']) 

    def _handle_key_press(self, key):
        if key == '=':
            self.api.get_exchange_rate(self.model.currency)
            self.needs_reset = True
            return
            
        current_value = self.model.input_str
        if self.needs_reset:
            current_value = "0"
            self.needs_reset = False

        if key.isdigit():
            if len(current_value.replace('.', '')) > 9: return
            new_value = key if current_value == "0" else current_value + key
            self.model.input_str = new_value
        elif key == '.' and '.' not in current_value:
            self.model.input_str += '.'
        elif key == 'DEL':
            self.model.input_str = current_value[:-1] or "0"
        
        self.model.updated.emit()
        # Dispara o cálculo em tempo real após cada alteração
        if self.model.input_str:
            self.api.get_exchange_rate(self.model.currency)
    
    def _handle_currency_change(self, value=None):
        self.model.next_currency()
        self.api.get_exchange_rate(self.model.currency)
        
# =============================================================================
# 6. PONTO DE ENTRADA DA APLICAÇÃO
# =============================================================================
class App(QApplication):
    def __init__(self, sys_argv):
        super(App, self).__init__(sys_argv)
        self.settings = SettingsManager()
        self.model = CalculatorModel(self.settings)
        self.api_service = ApiService()
        self.view = CalculatorView(self.model, self.dispatch_action)
        self.controller = CalculatorController(self.model, self.view, self.api_service)

        self.view.show()
        self.controller.handle_action('key_press', '=')

    def dispatch_action(self, action_type, value=None):
        self.controller.handle_action(action_type, value)

if __name__ == "__main__":
    app = App(sys.argv)
    sys.exit(app.exec())

