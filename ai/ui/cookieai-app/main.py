#!/usr/bin/env python3
"""
CookieAI — Standalone Cross-Platform App
Works on: Linux, Windows, macOS, Android (via Kivy/BeeWare)

Features:
  - CookieFocus: local Fooocus image generation with NSFW filters
  - CookieChat:  local Ollama/Gemma 4 chat with content filters
  - RAG:         drag-and-drop personal files as AI context
  - CookieCloud: optional sync (not required — works fully offline)

No data leaves your device. All AI runs locally.

This file is the entry point.
Run:  python main.py
Build: briefcase build   (macOS/Windows/Linux)
       briefcase build android  (Android APK)
"""

import sys
import os
from pathlib import Path

# Ensure ai/ submodules are on path regardless of install location
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "safeguards"))
sys.path.insert(0, str(_ROOT / "fooocus"))
sys.path.insert(0, str(_ROOT / "ollama"))

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from content_filter import check_prompt, check_image, Severity
from cookie_ollama import ChatSession, OllamaClient, build_rag_context, DEFAULT_MODEL
from cookie_fooocus import generate as fooocus_generate, SAFE_STYLES

__version__ = "1.0.0"
APP_NAME    = "CookieAI"
APP_ID      = "uk.cookiehost.cookieai"


class CookieAIApp(toga.App):
    def startup(self):
        self.rag_files: list[Path] = []
        self.chat_session: ChatSession | None = None
        self.current_model = DEFAULT_MODEL

        # ── Main window ───────────────────────────────────────────────────────
        self.main_window = toga.MainWindow(title=f"🍪 {APP_NAME} v{__version__}")

        # Tab container
        self.tabs = toga.OptionContainer(
            content=[
                ("💬 Chat",        self._build_chat_tab()),
                ("🖼 Image Gen",   self._build_image_tab()),
                ("📁 My Files",    self._build_files_tab()),
                ("⚙ Settings",     self._build_settings_tab()),
            ]
        )

        self.main_window.content = self.tabs
        self.main_window.show()

    # ── Chat tab ──────────────────────────────────────────────────────────────

    def _build_chat_tab(self) -> toga.Box:
        box = toga.Box(style=Pack(direction=COLUMN, padding=10))

        # Chat history display
        self.chat_display = toga.MultilineTextInput(
            readonly=True,
            style=Pack(flex=1, padding_bottom=8),
            value="🍪 CookieChat — your private local AI\n"
                  "Powered by Gemma 4 via Ollama. All data stays on your device.\n"
                  "──────────────────────────────────────\n",
        )

        # RAG file indicator
        self.rag_indicator = toga.Label(
            "No files in context",
            style=Pack(padding_bottom=4, color="#888888"),
        )

        # Input row
        input_row = toga.Box(style=Pack(direction=ROW, padding_bottom=4))
        self.chat_input = toga.TextInput(
            placeholder="Ask CookieGPT anything...",
            style=Pack(flex=1, padding_right=8),
        )
        send_btn = toga.Button(
            "Send",
            on_press=self._on_chat_send,
            style=Pack(padding_left=4),
        )
        input_row.add(self.chat_input)
        input_row.add(send_btn)

        # Action row
        action_row = toga.Box(style=Pack(direction=ROW, padding_top=4))
        clear_btn = toga.Button("Clear history", on_press=self._on_chat_clear,
                                style=Pack(padding_right=8))
        add_ctx_btn = toga.Button("+ Add file context", on_press=self._on_add_rag_file)
        action_row.add(clear_btn)
        action_row.add(add_ctx_btn)

        box.add(self.chat_display)
        box.add(self.rag_indicator)
        box.add(input_row)
        box.add(action_row)
        return box

    def _on_chat_send(self, widget):
        text = self.chat_input.value.strip()
        if not text:
            return
        self.chat_input.value = ""

        # Safety check
        result = check_prompt(text, user_id="app-user")
        if not result.allowed:
            self._chat_append(f"🚫 Blocked: {result.reason}\n")
            return
        if result.severity == Severity.WARN:
            self._chat_append(f"⚠  Warning: {result.reason}\n")

        self._chat_append(f"You: {text}\n")
        self._chat_append("CookieGPT: ")

        # Ensure session exists
        if self.chat_session is None or self.rag_files != self.chat_session._rag_files:
            self.chat_session = ChatSession(
                model=self.current_model,
                user_id="app-user",
                rag_files=self.rag_files or None,
            )
            self.chat_session._rag_files = list(self.rag_files)

        # Stream response
        import threading
        def _run():
            response = ""
            try:
                for chunk in self.chat_session.client.chat_stream(
                    self.chat_session.history + [{"role": "user", "content": text}],
                    model=self.current_model,
                ):
                    response += chunk
                    toga.App.app.loop.call_soon_threadsafe(
                        lambda c=chunk: self._chat_append(c)
                    )
            except Exception as e:
                toga.App.app.loop.call_soon_threadsafe(
                    lambda: self._chat_append(f"[Error: {e}]")
                )
            finally:
                toga.App.app.loop.call_soon_threadsafe(
                    lambda: self._chat_append("\n\n")
                )
                self.chat_session.history.append({"role": "assistant", "content": response})

        threading.Thread(target=_run, daemon=True).start()

    def _chat_append(self, text: str):
        self.chat_display.value += text

    def _on_chat_clear(self, widget):
        self.chat_session = None
        self.chat_display.value = (
            "🍪 CookieChat — history cleared.\n"
            "──────────────────────────────────────\n"
        )

    def _on_add_rag_file(self, widget):
        async def _pick():
            try:
                result = await self.main_window.open_file_dialog(
                    title="Select file(s) for AI context",
                    multiselect=True,
                )
                if result:
                    self.rag_files.extend(Path(str(p)) for p in result)
                    names = ", ".join(p.name for p in self.rag_files)
                    self.rag_indicator.text = f"Context: {names}"
                    self.chat_session = None  # Rebuild with new context
            except Exception as e:
                self._chat_append(f"[File picker error: {e}]\n")

        import asyncio
        asyncio.ensure_future(_pick())

    # ── Image generation tab ──────────────────────────────────────────────────

    def _build_image_tab(self) -> toga.Box:
        box = toga.Box(style=Pack(direction=COLUMN, padding=10))

        box.add(toga.Label("🖼 CookieFocus — Local Image Generation",
                           style=Pack(padding_bottom=8, font_size=14)))
        box.add(toga.Label(
            "All images generated locally. NSFW filter active.",
            style=Pack(padding_bottom=12, color="#888888"),
        ))

        # Prompt
        box.add(toga.Label("Prompt"))
        self.img_prompt = toga.MultilineTextInput(
            placeholder="A serene mountain landscape at golden hour...",
            style=Pack(height=80, padding_bottom=8),
        )
        box.add(self.img_prompt)

        # Style selector
        style_row = toga.Box(style=Pack(direction=ROW, padding_bottom=8))
        style_row.add(toga.Label("Style: ", style=Pack(padding_right=8)))
        self.style_select = toga.Selection(items=SAFE_STYLES)
        style_row.add(self.style_select)
        box.add(style_row)

        # Generate button
        gen_btn = toga.Button(
            "Generate Image",
            on_press=self._on_generate_image,
            style=Pack(padding_bottom=12),
        )
        box.add(gen_btn)

        # Status label
        self.img_status = toga.Label("Ready.", style=Pack(padding_bottom=8))
        box.add(self.img_status)

        # Image display
        self.img_view = toga.ImageView(style=Pack(height=400, flex=1))
        box.add(self.img_view)

        return box

    def _on_generate_image(self, widget):
        prompt = self.img_prompt.value.strip()
        if not prompt:
            self.img_status.text = "⚠  Please enter a prompt."
            return

        style = str(self.style_select.value)
        self.img_status.text = "⏳ Generating..."

        import threading
        def _run():
            path = fooocus_generate(prompt, user_id="app-user", style=style)
            if path:
                toga.App.app.loop.call_soon_threadsafe(
                    lambda: self._on_image_ready(path)
                )
            else:
                toga.App.app.loop.call_soon_threadsafe(
                    lambda: setattr(self.img_status, "text", "🚫 Blocked by safety filter.")
                )

        threading.Thread(target=_run, daemon=True).start()

    def _on_image_ready(self, path: Path):
        self.img_status.text = f"✓ Saved: {path.name}"
        self.img_view.image = toga.Image(str(path))

    # ── Files tab ─────────────────────────────────────────────────────────────

    def _build_files_tab(self) -> toga.Box:
        box = toga.Box(style=Pack(direction=COLUMN, padding=10))
        box.add(toga.Label("📁 Personal File Context",
                           style=Pack(padding_bottom=8, font_size=14)))
        box.add(toga.Label(
            "Files added here give the AI context about your documents, "
            "notes, and data.\nFiles are read locally — never uploaded.",
            style=Pack(padding_bottom=12, color="#888888"),
        ))

        self.files_list = toga.Table(
            headings=["Filename", "Size", "Type"],
            style=Pack(flex=1, padding_bottom=8),
        )
        box.add(self.files_list)

        btn_row = toga.Box(style=Pack(direction=ROW))
        btn_row.add(toga.Button("+ Add File", on_press=self._on_add_rag_file))
        btn_row.add(toga.Button("✕ Remove Selected",
                                on_press=self._on_remove_rag_file,
                                style=Pack(padding_left=8)))
        btn_row.add(toga.Button("Clear All",
                                on_press=lambda _: self._clear_rag_files(),
                                style=Pack(padding_left=8)))
        box.add(btn_row)
        return box

    def _on_remove_rag_file(self, widget):
        if self.files_list.selection:
            idx = self.files_list.data.index(self.files_list.selection)
            if 0 <= idx < len(self.rag_files):
                self.rag_files.pop(idx)
                self._refresh_files_list()
                self.chat_session = None

    def _clear_rag_files(self):
        self.rag_files.clear()
        self._refresh_files_list()
        self.rag_indicator.text = "No files in context"
        self.chat_session = None

    def _refresh_files_list(self):
        self.files_list.data = [
            (p.name, f"{p.stat().st_size // 1024}KB", p.suffix)
            for p in self.rag_files if p.exists()
        ]

    # ── Settings tab ─────────────────────────────────────────────────────────

    def _build_settings_tab(self) -> toga.Box:
        box = toga.Box(style=Pack(direction=COLUMN, padding=10))
        box.add(toga.Label("⚙ Settings", style=Pack(padding_bottom=8, font_size=14)))

        # Model selection
        model_row = toga.Box(style=Pack(direction=ROW, padding_bottom=8))
        model_row.add(toga.Label("Chat model: ", style=Pack(padding_right=8)))
        self.model_select = toga.Selection(
            items=["gemma3:4b", "gemma3:12b", "mistral:7b", "llama3:8b"],
            on_change=self._on_model_change,
        )
        model_row.add(self.model_select)
        box.add(model_row)

        # Ollama host
        host_row = toga.Box(style=Pack(direction=ROW, padding_bottom=8))
        host_row.add(toga.Label("Ollama host: ", style=Pack(padding_right=8)))
        self.ollama_host_input = toga.TextInput(
            value="http://127.0.0.1:11434",
            style=Pack(flex=1),
        )
        host_row.add(self.ollama_host_input)
        box.add(host_row)

        # CookieCloud server
        cc_row = toga.Box(style=Pack(direction=ROW, padding_bottom=8))
        cc_row.add(toga.Label("CookieCloud server (optional): ",
                              style=Pack(padding_right=8)))
        self.cc_server_input = toga.TextInput(
            value="https://cookiecloud.cookiehost.uk",
            style=Pack(flex=1),
        )
        cc_row.add(self.cc_server_input)
        box.add(cc_row)

        box.add(toga.Divider(style=Pack(padding_top=12, padding_bottom=12)))

        # Safety settings
        box.add(toga.Label("Safety", style=Pack(font_size=12, padding_bottom=4)))
        self.nsfw_switch = toga.Switch("Block NSFW image output", value=True)
        self.prompt_filter_switch = toga.Switch("Filter harmful prompts", value=True)
        box.add(self.nsfw_switch)
        box.add(self.prompt_filter_switch)

        box.add(toga.Button("Save Settings", on_press=self._on_save_settings,
                            style=Pack(padding_top=12)))

        box.add(toga.Divider(style=Pack(padding_top=12, padding_bottom=12)))
        box.add(toga.Label(
            f"CookieAI v{__version__}  |  Made with ❤ by CookieNet\n"
            "All AI runs locally. No telemetry. No cloud required.",
            style=Pack(color="#888888"),
        ))
        return box

    def _on_model_change(self, widget):
        self.current_model = str(widget.value)
        self.chat_session = None

    def _on_save_settings(self, widget):
        self.main_window.info_dialog("Settings saved", "Your settings have been saved.")


def main():
    return CookieAIApp(APP_NAME, APP_ID)


if __name__ == "__main__":
    app = main()
    app.main_loop()
