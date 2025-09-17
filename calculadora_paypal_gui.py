import tkinter as tk
from tkinter import ttk, messagebox, TclError
import requests
import math
import threading
import locale
from queue import Queue, Empty
import json
import os

# =============================================================================
# 0. CONFIGURAÇÃO DE TAXAS (Fee Configuration)
# =============================================================================
# Fonte da verdade para as taxas do PayPal baseadas na documentação.
# Tarifa % = 4,79% (base) + 1,61% (internacional) = 6,40%
# Spread = 3,50% para recebimento de pagamentos.
# =============================================================================

PAYPAL_FEES = {
    "USD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
    "EUR": {"fee_percent": 6.40, "fixed_fee": 0.35, "spread_percent": 3.50},
    "GBP": {"fee_percent": 6.40, "fixed_fee": 0.20, "spread_percent": 3.50},
    "JPY": {"fee_percent": 6.40, "fixed_fee": 40.00, "spread_percent": 3.50},
    "CAD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
    "AUD": {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 3.50},
}
# Valores padrão para moedas não listadas explicitamente
DEFAULT_FEES = {"fee_percent": 6.40, "fixed_fee": 0.30, "spread_percent": 4.50}


# =============================================================================
# 1. SERVIÇOS E GERENCIADORES (Services & Managers)
# =============================================================================
class SettingsManager:
    """Gerencia o carregamento e salvamento das configurações do usuário em um arquivo JSON."""
    def __init__(self, filename="calculator_settings.json"):
        self.filepath = os.path.join(os.path.expanduser("~"), filename)
        self.defaults = {
            "theme": "light",
            "last_currency": "USD",
            "history": []
        }
        self.settings = self.load_settings()

    def load_settings(self):
        """Carrega as configurações do arquivo JSON."""
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                for key, value in self.defaults.items():
                    settings.setdefault(key, value)
                return settings
        except (FileNotFoundError, json.JSONDecodeError):
            return self.defaults.copy()

    def save_settings(self):
        """Salva as configurações atuais no arquivo JSON."""
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
        except IOError as e:
            print(f"Erro ao salvar configurações: {e}")

    def get(self, key):
        """Obtém um valor de configuração."""
        return self.settings.get(key, self.defaults.get(key))

    def set(self, key, value):
        """Define um valor de configuração e salva."""
        self.settings[key] = value
        self.save_settings()

class ApiService:
    """Lida com todas as requisições à API de cotações de forma assíncrona."""
    BASE_URL = "https://economia.awesomeapi.com.br/json/last/{}-BRL"
    
    def __init__(self, queue):
        self.api_queue = queue
        self.rate_cache = {}

    def get_exchange_rate(self, currency):
        """Busca a cotação da moeda em uma thread separada para não bloquear a UI."""
        if currency == "BRL":
            self.api_queue.put({'status': 'success', 'rate': 1.0})
            return

        # Para forçar a atualização, poderíamos invalidar o cache periodicamente
        if currency in self.rate_cache:
            self.api_queue.put({'status': 'success', 'rate': self.rate_cache[currency]})
            return

        self.api_queue.put({'status': 'loading', 'currency': currency})
        
        threading.Thread(target=self._fetch_rate, args=(currency,), daemon=True).start()

    def _fetch_rate(self, currency):
        """Lógica da requisição que executa em segundo plano."""
        try:
            url = self.BASE_URL.format(currency)
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            rate = float(data[f"{currency}BRL"]['bid'])
            self.rate_cache[currency] = rate
            self.api_queue.put({'status': 'success', 'rate': rate})
        except requests.RequestException:
            self.api_queue.put({'status': 'error', 'message': "Erro de conexão."})
        except (KeyError, ValueError):
            self.api_queue.put({'status': 'error', 'message': f"Moeda '{currency}' inválida."})

# =============================================================================
# 2. MODELO (Model)
# =============================================================================
class CalculatorModel:
    """Mantém o estado e a lógica de negócio da calculadora."""
    def __init__(self, settings_manager):
        self.settings = settings_manager
        self.valor_str = tk.StringVar(value="100.00")
        self.moeda_var = tk.StringVar(value=self.settings.get('last_currency'))
        
        # Estas StringVars agora são apenas para exibição na UI
        self.tarifa_display_var = tk.StringVar()
        self.taxa_fixa_display_var = tk.StringVar()
        self.spread_display_var = tk.StringVar()
        
        self.resultado_final_var = tk.StringVar(value="R$ 0,00")
        self.detalhes_resultado_var = tk.StringVar(value="Preencha os valores para calcular.")
        
        self.memory = 0.0
        self.history = self.settings.get('history')

    def perform_calculation(self, exchange_rate):
        """Executa a lógica principal de cálculo com base no estado atual."""
        try:
            valor = float(self.valor_str.get().replace(',', '.'))
            moeda = self.moeda_var.get()
            
            # ATUALIZAÇÃO: Busca as taxas do dicionário de configuração
            fees = PAYPAL_FEES.get(moeda, DEFAULT_FEES)
            tarifa_percentual = fees["fee_percent"] / 100
            taxa_fixa = fees["fixed_fee"]
            spread = fees["spread_percent"] / 100

        except (ValueError, TclError):
            self.detalhes_resultado_var.set("Entrada inválida.")
            return

        valor_apos_taxas = valor - (valor * tarifa_percentual) - taxa_fixa
        valor_liquido = max(0, valor_apos_taxas)
        cambio_final = exchange_rate * (1 - spread)
        valor_final_brl = valor_liquido * cambio_final

        self.update_display_vars(valor_final_brl, valor_liquido, moeda, cambio_final)
        self._add_to_history(valor, moeda, valor_final_brl)
        
        self.settings.set('last_currency', moeda)

    def update_display_vars(self, brl, liquido, moeda, cambio):
        """Formata e atualiza as StringVars do display com os resultados."""
        try:
            brl_fmt = locale.currency(brl, grouping=True)
            liq_fmt = locale.format_string("%.2f", liquido, grouping=True)
            cambio_fmt = locale.format_string("%.4f", cambio, grouping=True)
        except (ValueError, TypeError): # Fallback
            brl_fmt = f"R$ {brl:,.2f}"
            liq_fmt = f"{liquido:,.2f}"
            cambio_fmt = f"{cambio:,.4f}"
        
        self.resultado_final_var.set(brl_fmt)
        self.detalhes_resultado_var.set(f"Líquido {liq_fmt} {moeda} @ {brl_fmt.split()[0]} {cambio_fmt}")

    def _add_to_history(self, valor_origem, moeda, valor_final_brl):
        """Adiciona um novo registro ao histórico de cálculos."""
        entry = {
            "from": f"{locale.format_string('%.2f', valor_origem, grouping=True)} {moeda}",
            "to": locale.currency(valor_final_brl, grouping=True)
        }
        self.history.insert(0, entry)
        self.history = self.history[:20] 
        self.settings.set('history', self.history)

# =============================================================================
# 3. WIDGETS CUSTOMIZADOS (Custom Widgets)
# =============================================================================
class AnimatedFrame(ttk.Frame):
    """Um Frame que pode animar sua posição (slide in/out)."""
    # ... (código sem alterações)
    def __init__(self, parent, start_relx, end_relx, **kwargs):
        super().__init__(parent, **kwargs)
        self.start_relx = start_relx
        self.end_relx = end_relx
        self.pos = start_relx
        self.is_shown = False
        self.animation_in_progress = False

    def animate(self):
        if self.animation_in_progress:
            return
        self.animation_in_progress = True
        
        if not self.is_shown:
            self.place(relx=self.pos, rely=0, relwidth=0.4, relheight=1)
            self._animate_forward()
        else:
            self._animate_backward()

    def _animate_forward(self):
        if self.pos < self.end_relx:
            self.pos += 0.02
            self.place(relx=self.pos, rely=0, relwidth=0.4, relheight=1)
            self.after(5, self._animate_forward)
        else:
            self.pos = self.end_relx
            self.place(relx=self.pos, rely=0, relwidth=0.4, relheight=1)
            self.is_shown = True
            self.animation_in_progress = False

    def _animate_backward(self):
        if self.pos > self.start_relx:
            self.pos -= 0.02
            self.place(relx=self.pos, rely=0, relwidth=0.4, relheight=1)
            self.after(5, self._animate_backward)
        else:
            self.place_forget()
            self.pos = self.start_relx
            self.is_shown = False
            self.animation_in_progress = False
# =============================================================================
# 4. VISÃO (View)
# =============================================================================
class CalculatorView(ttk.Frame):
    """Constrói e gerencia todos os widgets da interface gráfica."""
    def __init__(self, parent, model, controller_callback):
        super().__init__(parent)
        self.parent = parent
        self.model = model
        self.controller_callback = controller_callback
        self.active_entry = None
        self.highlight_widgets = {}
        self.widget_to_var_map = {}
        
        self.model.moeda_var.trace_add("write", self._on_currency_change)

        self._setup_styles()
        self._create_widgets()
        
    def _setup_styles(self):
        """Configura os estilos visuais para os widgets ttk."""
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        
        self.light_theme = {
            "BG": "#f0f2f5", "SURFACE": "#ffffff", "TEXT": "#202124", 
            "SECONDARY_TEXT": "#5f6368", "ACCENT": "#1a73e8"
        }
        self.dark_theme = {
            "BG": "#202124", "SURFACE": "#3c4043", "TEXT": "#e8eaed",
            "SECONDARY_TEXT": "#9aa0a6", "ACCENT": "#8ab4f8"
        }
        self.apply_theme(self.model.settings.get('theme'))

    def apply_theme(self, theme_name):
        """Aplica um esquema de cores (tema) a todos os widgets."""
        theme = self.dark_theme if theme_name == "dark" else self.light_theme
        self.parent.configure(bg=theme["BG"])
        
        self.style.configure('.', background=theme["BG"], foreground=theme["TEXT"], borderwidth=0, focusthickness=0)
        self.style.configure('TFrame', background=theme["BG"])
        self.style.configure('Card.TFrame', background=theme["SURFACE"])
        self.style.configure('Card.TLabel', background=theme["SURFACE"], foreground=theme["TEXT"])
        self.style.configure('Card.InputLabel.TLabel', background=theme["SURFACE"], foreground=theme["SECONDARY_TEXT"], font=('Segoe UI Semibold', 10))
        # ATUALIZAÇÃO: Estilo para os valores das taxas exibidas
        self.style.configure('Card.Value.TLabel', background=theme["SURFACE"], foreground=theme["TEXT"], font=('Segoe UI', 12))
        
        self.style.configure('TLabel', foreground=theme["TEXT"])
        self.style.configure('Header.TLabel', font=('Segoe UI', 18, 'bold'))
        self.style.configure('ResultValue.TLabel', font=('Segoe UI Semibold', 40), foreground=theme["ACCENT"])
        self.style.configure('ResultDetails.TLabel', foreground=theme["SECONDARY_TEXT"])
        self.style.configure('InputLabel.TLabel', foreground=theme["SECONDARY_TEXT"])

        self.style.configure('TEntry', fieldbackground=theme["SURFACE"], foreground=theme["TEXT"], insertbackground=theme["TEXT"])
        self.style.map('TEntry', fieldbackground=[('focus', theme["SURFACE"])])
        
        self.parent.option_add('*TCombobox*Listbox*Background', theme["SURFACE"])
        self.parent.option_add('*TCombobox*Listbox*Foreground', theme["TEXT"])
        self.parent.option_add('*TCombobox*Listbox*selectBackground', theme["ACCENT"])
        self.parent.option_add('*TCombobox*Listbox*selectForeground', theme["SURFACE"])
        self.style.configure('TCombobox', fieldbackground=theme["SURFACE"], foreground=theme["TEXT"], arrowcolor=theme["SECONDARY_TEXT"])
        
        self.style.configure('Keypad.TButton', background=theme["SURFACE"], foreground=theme["TEXT"], font=('Segoe UI Semibold', 16), padding=(20, 18))
        self.style.configure('Func.Keypad.TButton', background=theme["BG"], foreground=theme["SECONDARY_TEXT"], font=('Segoe UI', 14))
        self.style.map('Keypad.TButton', background=[('active', theme["BG"])])
        self.style.map('Func.Keypad.TButton', background=[('active', theme["SURFACE"])])
        
        self.style.configure('Menu.TButton', font=('Segoe UI', 18), padding=5, relief='flat')
        self.style.map('Menu.TButton', background=[('active', theme["SURFACE"])])
        
        self.style.configure('Switch.TCheckbutton', indicatorbackground=theme["SURFACE"], background=theme["BG"])
        self.style.map('Switch.TCheckbutton', indicatorbackground=[('selected', theme["ACCENT"])])

    def _create_widgets(self):
        """Cria e organiza a estrutura principal da UI."""
        main_frame = ttk.Frame(self, padding=(25, 25))
        main_frame.pack(fill=tk.BOTH, expand=True)
        calculator_frame = ttk.Frame(main_frame)
        calculator_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.sidebar = self._create_sidebar(main_frame)
        self._create_calculator_content(calculator_frame)
    
    def _on_currency_change(self, *args):
        """ATUALIZAÇÃO: Atualiza os displays de taxa e recalcula."""
        moeda = self.model.moeda_var.get()
        fees = PAYPAL_FEES.get(moeda, DEFAULT_FEES)
        
        self.model.tarifa_display_var.set(f'{fees["fee_percent"]:.2f} %')
        self.model.taxa_fixa_display_var.set(f'{fees["fixed_fee"]:.2f} {moeda}')
        self.model.spread_display_var.set(f'{fees["spread_percent"]:.2f} %')
        
        self.controller_callback('calculate')
        
    def _create_sidebar(self, parent):
        sidebar = AnimatedFrame(parent, start_relx=1.0, end_relx=0.6, style='Card.TFrame')
        
        header = ttk.Frame(sidebar, style='Card.TFrame')
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, text="Configurações", font=('Segoe UI', 16, 'bold'), style='Card.TLabel').pack(side='left')
        
        close_button = ttk.Button(header, text="X", style="Menu.TButton", cursor="hand2", command=sidebar.animate)
        close_button.pack(side='right')

        notebook = ttk.Notebook(sidebar)
        notebook.pack(fill='both', expand=True, pady=10)

        settings_tab = ttk.Frame(notebook, style='Card.TFrame', padding=5)
        self._populate_settings_tab(settings_tab)
        notebook.add(settings_tab, text='Ajustes')

        history_tab = ttk.Frame(notebook, style='Card.TFrame', padding=5)
        self._populate_history_tab(history_tab)
        notebook.add(history_tab, text='Histórico')
        
        return sidebar

    def _populate_settings_tab(self, parent):
        """ATUALIZAÇÃO: Preenche a aba de configurações com displays não-editáveis."""
        s_style, l_style = 'Card.TFrame', 'Card.InputLabel.TLabel'
        self._create_currency_row(parent, "Moeda", self.model.moeda_var, s_style, l_style)
        
        self._create_display_row(parent, "Tarifa Percentual", self.model.tarifa_display_var, s_style, l_style)
        self._create_display_row(parent, "Taxa Fixa", self.model.taxa_fixa_display_var, s_style, l_style)
        self._create_display_row(parent, "Spread de Conversão", self.model.spread_display_var, s_style, l_style)

        theme_frame = ttk.Frame(parent, style=s_style, padding=(10,15))
        theme_frame.pack(fill='x', pady=10)
        ttk.Label(theme_frame, text="Tema Escuro", style='Card.TLabel').pack(side='left')
        self.theme_var = tk.BooleanVar(value=self.model.settings.get('theme') == 'dark')
        theme_switch = ttk.Checkbutton(theme_frame, style='Switch.TCheckbutton', variable=self.theme_var,
                                       command=lambda: self.controller_callback('toggle_theme', self.theme_var.get()))
        theme_switch.pack(side='right')

    def _create_display_row(self, parent, label_text, display_var, style, label_style):
        """ATUALIZAÇÃO: Novo método para criar uma linha de exibição (não-editável)."""
        row = ttk.Frame(parent, style=style, padding=(10, 8))
        row.pack(fill='x')
        ttk.Label(row, text=label_text, style=label_style).pack(side='left')
        ttk.Label(row, textvariable=display_var, style='Card.Value.TLabel').pack(side='right')

    def _populate_history_tab(self, parent):
        self.history_list = tk.Listbox(parent, background=self.style.lookup('Card.TFrame', 'background'),
                                      fg=self.style.lookup('Card.TLabel', 'foreground'), 
                                      selectbackground=self.style.lookup('TLabel', 'foreground'),
                                      selectforeground=self.style.lookup('TLabel', 'background'),
                                      borderwidth=0, highlightthickness=0, font=('Segoe UI', 10))
        self.history_list.pack(fill='both', expand=True)
        self.update_history_display()

    def update_history_display(self):
        self.history_list.delete(0, tk.END)
        if not self.model.history:
            self.history_list.insert(tk.END, "  Nenhum cálculo recente.")
        else:
            for item in self.model.history:
                self.history_list.insert(tk.END, f"  {item['from']} → {item['to']}")

    def _create_calculator_content(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=0); parent.rowconfigure(1, weight=1); parent.rowconfigure(2, weight=0); parent.rowconfigure(3, weight=0); parent.rowconfigure(4, weight=5)

        header_frame = ttk.Frame(parent)
        header_frame.grid(row=0, column=0, sticky='ew', pady=(0, 15))
        ttk.Label(header_frame, text="Calculadora de Câmbio", style='Header.TLabel').pack(side=tk.LEFT, padx=(5,0))
        
        menu_button = ttk.Button(header_frame, text="☰", style="Menu.TButton", cursor="hand2", command=self.sidebar.animate)
        menu_button.pack(side=tk.RIGHT)
        
        result_frame = ttk.Frame(parent)
        result_frame.grid(row=1, column=0, sticky='ew', pady=(0, 20))
        ttk.Label(result_frame, textvariable=self.model.resultado_final_var, style='ResultValue.TLabel', anchor='e').pack(fill='x')
        ttk.Label(result_frame, textvariable=self.model.detalhes_resultado_var, style='ResultDetails.TLabel', anchor='e').pack(fill='x')

        self.valor_entry_frame = self._create_input_row(parent, "Valor a converter", self.model.valor_str)
        self.valor_entry_frame.grid(row=2, column=0, sticky='ew')
        
        ttk.Separator(parent).grid(row=3, column=0, sticky='ew', pady=(20, 15))
        
        self._create_keypad(parent).grid(row=4, column=0, sticky='nsew')
        
    def _create_input_row(self, parent, label, var, style='TFrame', label_style='InputLabel.TLabel'):
        row = ttk.Frame(parent, padding=(0, 10), style=style)
        ttk.Label(row, text=label, style=label_style).pack(anchor='w', padx=10, pady=(0, 2))
        
        container = tk.Frame(row, bg=self.style.lookup('TEntry', 'fieldbackground'), relief='solid', borderwidth=1, highlightthickness=0)
        container.pack(fill='x', padx=5); container.config(borderwidth=0)
        
        entry = ttk.Entry(container, textvariable=var, justify='right', state='readonly', font=('Segoe UI', 20))
        entry.pack(fill='x')
        
        focus_highlight = tk.Frame(container, bg=self.style.lookup('TEntry', 'fieldbackground'), height=2)
        focus_highlight.pack(fill='x')
        
        entry.bind("<FocusIn>", lambda e, en=entry: self.set_active_input(en))
        
        self.highlight_widgets[entry] = focus_highlight
        self.widget_to_var_map[entry] = var 
        row.entry_widget = entry
        return row

    def _create_currency_row(self, parent, label, var, style, label_style):
        row = ttk.Frame(parent, padding=(0, 10), style=style)
        row.pack(fill='x')
        ttk.Label(row, text=label, style=label_style).pack(anchor='w', padx=10, pady=(0,2))
        container = tk.Frame(row, background=self.style.lookup('TEntry', 'fieldbackground'))
        container.pack(fill='x', padx=5)
        combo = ttk.Combobox(container, textvariable=var, values=list(PAYPAL_FEES.keys()), state="readonly")
        combo.pack(fill='x', expand=True)
        combo.bind("<FocusIn>", lambda e: self.set_active_input(None))

    def _create_keypad(self, parent):
        keypad_frame = ttk.Frame(parent, padding=(0, 10, 0, 0))
        keys = [['MC', 'MR', 'M+', 'M-'], ['%', '1/x', 'x²', '√x'], ['7', '8', '9', '⌫'], ['4', '5', '6', 'C'], ['1', '2', '3', '+/-'], ['0', '.', ]]
        for i in range(4): keypad_frame.columnconfigure(i, weight=1)
        for r in range(len(keys)): keypad_frame.rowconfigure(r, weight=1)
        for r, row_keys in enumerate(keys):
            for c, key in enumerate(row_keys):
                style = 'Func.Keypad.TButton' if key not in '0123456789.' else 'Keypad.TButton'
                cmd = lambda k=key: self.controller_callback('key_press', k)
                if key == '0':
                    btn = ttk.Button(keypad_frame, text=key, command=cmd, style=style).grid(row=r, column=c, columnspan=2, sticky='nsew', padx=4, pady=4)
                elif key == '.':
                    btn = ttk.Button(keypad_frame, text=key, command=cmd, style=style).grid(row=r, column=c + 1, sticky='nsew', padx=4, pady=4)
                else:
                    btn = ttk.Button(keypad_frame, text=key, command=cmd, style=style).grid(row=r, column=c, sticky='nsew', padx=4, pady=4)
        return keypad_frame

    def set_active_input(self, entry_widget):
        self.active_entry = entry_widget
        self.update_focus_highlight()
        active_var = self.widget_to_var_map.get(entry_widget)
        self.controller_callback('set_active_var', active_var)
        
    def update_focus_highlight(self):
        accent_color = self.style.lookup('ResultValue.TLabel', 'foreground')
        surface_color = self.style.lookup('TEntry', 'fieldbackground')
        for entry_obj, highlight_bar in self.highlight_widgets.items():
            is_active = (entry_obj == self.active_entry)
            highlight_bar.config(bg=accent_color if is_active else surface_color)

# =============================================================================
# 5. CONTROLADOR (Controller)
# =============================================================================
class CalculatorController:
    """Processa a entrada do usuário e orquestra as atualizações."""
    def __init__(self, model, view, api_service):
        self.model = model
        self.view = view
        self.api = api_service
        self.active_var = None

    def handle_action(self, action_type, value=None):
        if action_type == 'key_press':
            self._handle_key_press(value)
        elif action_type == 'calculate':
            self.api.get_exchange_rate(self.model.moeda_var.get())
        elif action_type == 'toggle_theme':
            theme = "dark" if value else "light"
            self.model.settings.set('theme', theme)
            self.view.apply_theme(theme)
        elif action_type == 'set_active_var':
            self.active_var = value

    def _handle_key_press(self, key):
        if not self.active_var: return
        
        current_value = self.active_var.get()

        if key in '0123456789':
            if len(current_value) > 15: return
            new_value = key if current_value == "0" else current_value + key
            self.active_var.set(new_value)
        elif key == '.' and '.' not in current_value:
            self.active_var.set(current_value + '.')
        elif key == 'C':
            self.active_var.set("0")
        elif key == '⌫':
            self.active_var.set(current_value[:-1] or "0")
        else:
            self._handle_math_functions(key, current_value)
        
        # Recalcula a cada alteração de valor
        self.api.get_exchange_rate(self.model.moeda_var.get())

    def _handle_math_functions(self, key, current_value_str):
        try:
            value = float(current_value_str.replace(',', '.') or 0)
        except (ValueError, TclError): return
        
        result = None
        if key == 'MC': self.model.memory = 0.0
        elif key == 'MR': result = self.model.memory
        elif key == 'M+': self.model.memory += value
        elif key == 'M-': self.model.memory -= value
        elif key == '%': result = value / 100
        elif key == '+/-': result = -value
        elif key == '1/x':
            if value == 0: messagebox.showwarning("Erro", "Divisão por zero."); return
            result = 1 / value
        elif key == 'x²': result = value ** 2
        elif key == '√x':
            if value < 0: messagebox.showwarning("Erro", "Entrada inválida."); return
            result = math.sqrt(value)
        
        if result is not None:
            formatted_result = f"{result:.10g}".rstrip('0').rstrip('.')
            self.active_var.set(formatted_result)

# =============================================================================
# 6. APLICAÇÃO PRINCIPAL (Main Application)
# =============================================================================
class App(tk.Tk):
    """Classe principal que inicializa e gerencia a aplicação."""
    def __init__(self):
        super().__init__()
        
        self.withdraw()
        self._setup_dpi_awareness()
        self.title("Calculadora de Câmbio PayPal")
        self.minsize(500, 750)
        
        try: locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
        except locale.Error:
            try: locale.setlocale(locale.LC_ALL, 'Portuguese_Brazil.1252')
            except locale.Error: locale.setlocale(locale.LC_ALL, '')

        self.api_queue = Queue()
        self.settings = SettingsManager()
        self.api_service = ApiService(self.api_queue)
        self.model = CalculatorModel(self.settings)
        self.view = CalculatorView(self, self.model, self.dispatch_action)
        self.controller = CalculatorController(self.model, self.view, self.api_service)

        self.view.pack(fill="both", expand=True)
        self.process_api_queue()
        
        self.bind("<Key>", self.handle_key_press)
        
        self.after(100, self.initial_setup)
        self.deiconify()

    def handle_key_press(self, event):
        """Lida com a entrada do teclado físico."""
        key_map = {
            "BackSpace": "⌫", "Delete": "C", "Escape": "C",
            "period": ".", "comma": ".", "KP_Decimal": ".",
            "Return": "calculate", "KP_Enter": "calculate",
            "plus": "+", "KP_Add": "+", "minus": "-", "KP_Subtract": "-",
        }
        key = event.keysym
        
        if key in "0123456789":
            self.controller.handle_action('key_press', key)
        elif key in key_map:
            mapped_key = key_map[key]
            if mapped_key == 'calculate':
                self.controller.handle_action('calculate')
            else:
                self.controller.handle_action('key_press', mapped_key)

    def _setup_dpi_awareness(self):
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except (ImportError, AttributeError):
            pass

    def initial_setup(self):
        """Configuração inicial da UI."""
        active_widget = self.view.valor_entry_frame.entry_widget
        active_widget.focus_set()
        self.view.set_active_input(active_widget)
        self.view._on_currency_change() # Garante que as taxas iniciais sejam carregadas

    def dispatch_action(self, action_type, value=None):
        """Recebe ações da View e as envia para o Controller."""
        self.controller.handle_action(action_type, value)

    def process_api_queue(self):
        """Verifica a fila da API e atualiza o Model/View."""
        try:
            message = self.api_queue.get_nowait()
            if message['status'] == 'success':
                exchange_rate = message['rate']
                self.model.perform_calculation(exchange_rate)
                self.view.update_history_display()
            elif message['status'] == 'error':
                self.model.detalhes_resultado_var.set(message['message'])
            elif message['status'] == 'loading':
                currency = message.get('currency', '')
                self.model.detalhes_resultado_var.set(f"Buscando cotação para {currency}...")
        except Empty:
            pass
        finally:
            self.after(100, self.process_api_queue)

if __name__ == "__main__":
    app = App()
    app.mainloop()

