"""Native Tk dialog for staging and validating a three-file input selection."""

import tkinter as tk
from tkinter import filedialog, ttk

from mill_yard.gui.configuration import COMPONENT_LABELS, ConfigurationDraft


class ConfigurationDialog:
    def __init__(self, root, files, on_apply, on_close):
        self.draft = ConfigurationDraft.from_files(files)
        self.on_apply, self.on_close = on_apply, on_close
        self.window = tk.Toplevel(root)
        self.window.title("Simulation inputs")
        self.window.transient(root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        self.variables = {}
        for row, (key, label) in enumerate(COMPONENT_LABELS.items()):
            variable = tk.StringVar(value=getattr(self.draft, key))
            self.variables[key] = variable
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=variable, width=64).grid(row=row, column=1, padx=8)
            ttk.Button(frame, text="Browse...", command=lambda kind=key: self.browse(kind)).grid(
                row=row, column=2
            )
        self.horizon = tk.StringVar(value=self.draft.horizon)
        ttk.Label(frame, text="Horizon override (min)").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.horizon, width=15).grid(
            row=3, column=1, sticky="w", padx=8
        )
        ttk.Label(frame, text="Blank uses the truck schedule's horizon.").grid(
            row=4, column=1, sticky="w", padx=8
        )
        self.message = tk.StringVar()
        ttk.Label(frame, textvariable=self.message, wraplength=700).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=14
        )
        self.apply_button = ttk.Button(frame, text="Apply & restart", command=self.apply)
        self.apply_button.grid(row=6, column=1, sticky="e", padx=8)
        ttk.Button(frame, text="Cancel", command=self.cancel).grid(row=6, column=2)
        self.closed = False
        for variable in (*self.variables.values(), self.horizon):
            variable.trace_add("write", lambda *_: self.validate())
        self.validate()

    def validate(self):
        for key, variable in self.variables.items():
            setattr(self.draft, key, variable.get())
        self.draft.horizon = self.horizon.get()
        valid, message = self.draft.validate()
        self.message.set(message)
        self.apply_button.state(["!disabled"] if valid else ["disabled"])
        return valid

    def choose(self, kind, path):
        try:
            self.draft.choose(kind, path)
        except (OSError, ValueError) as exc:
            self.message.set(str(exc))
            return
        self.variables[kind].set(getattr(self.draft, kind))

    def browse(self, kind):
        path = filedialog.askopenfilename(
            parent=self.window,
            title=f"Select {COMPONENT_LABELS[kind].lower()}",
            initialdir=self.draft.directory(kind),
            filetypes=[("YAML files", "*.yaml *.yml")],
        )
        if path:
            self.choose(kind, path)

    def apply(self):
        if not self.validate():
            return
        try:
            self.on_apply(self.draft.files())
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set(str(exc))
            self.apply_button.state(["disabled"])
            return
        self.close(True)

    def cancel(self):
        self.close(False)

    def close(self, accepted):
        if self.closed:
            return
        self.closed = True
        self.window.grab_release()
        self.window.destroy()
        self.on_close(accepted)
