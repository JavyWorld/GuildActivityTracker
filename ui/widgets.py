import tkinter as tk
from tkinter import ttk
from .theme import UITheme

class StatCard(ttk.Frame):
    def __init__(self, master, title: str, value: str, theme: UITheme):
        super().__init__(master)
        style_name = f"Card.TFrame"
        self.configure(style=style_name, padding=10)
        self.value_var = tk.StringVar(value=value)
        ttk.Label(self, text=title, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.value_var, style="Value.TLabel").pack(anchor="w")

    def set(self, value: str):
        self.value_var.set(value)
