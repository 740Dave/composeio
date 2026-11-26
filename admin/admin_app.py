"""ComposeIO Admin Console — persistent storage browser + service monitor.

Tabbed CTk interface for inspecting the deployed ComposeIO system:
  Tab 1: Persistent Storage Browser (configs, assets, workloads)
  Tab 2: Services Monitor (bus storage, MQTT broker, service hub, connected modules)
  Tab 3: Namespace Inspector (live view of all registered namespaces)

Connects to bus storage at mqtt-broker.local:8420.

Run: python3 -m admin.admin_app
     (from ${COMPOSEIO_HOME}/)
"""

from __future__ import annotations
import os
import sys
import json
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import customtkinter as ctk

from composeio.storage import BusStorageClient
from composeio.docstore import DocStore

BUS_STORAGE_URL = os.environ.get("COMPOSEIO_STORAGE_URL", "http://mqtt-broker.local:8420")
DOCSTORE_HOST = os.environ.get("COMPOSEIO_DOCSTORE_HOST", "mqtt-broker.local")
MQTT_BROKER = os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")
MQTT_PORT = 1884


class StorageBrowserTab(ctk.CTkFrame):
    """Tab 1: Persistent Storage Browser."""

    def __init__(self, master, client: BusStorageClient, **kwargs):
        super().__init__(master, **kwargs)
        self.client = client
        self.configure(fg_color="transparent")

        # Split: left list, right detail
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))

        ctk.CTkButton(toolbar, text="Refresh", width=80, command=self.refresh).pack(side="left", padx=4)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter())
        ctk.CTkEntry(toolbar, textvariable=self.search_var, placeholder_text="Search...", width=200).pack(side="left", padx=4)

        self.count_label = ctk.CTkLabel(toolbar, text="", font=("Courier", 10), text_color="#666666")
        self.count_label.pack(side="right", padx=4)

        # Left panel: item list
        left = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8)
        left.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))

        self.item_list = ctk.CTkTextbox(left, font=("Courier", 11), fg_color="#0a0a0a",
                                         state="disabled", wrap="none", width=300)
        self.item_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.item_list.bind("<Button-1>", self._on_click)

        # Right panel: detail view
        right = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8)
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 8))

        self.detail_title = ctk.CTkLabel(right, text="Select an item", font=("Helvetica", 14, "bold"))
        self.detail_title.pack(pady=(8, 4))

        self.detail_text = ctk.CTkTextbox(right, font=("Courier", 11), fg_color="#0a0a0a",
                                           state="disabled", wrap="word")
        self.detail_text.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        self._items = []
        self._filtered_items = []

    def refresh(self):
        """Fetch all items from bus storage."""
        self._items = []

        # Configs
        status = self.client.status()
        for mod in status.get("modules", []):
            checksum = self.client.get_config_checksum(mod)
            self._items.append({
                "type": "config",
                "id": mod,
                "label": f"[CONFIG] {mod}",
                "checksum": checksum,
            })

        # Namespaces
        namespaces = self.client.list_namespaces()
        for ns in namespaces:
            name = ns.get("name", "?")
            self._items.append({
                "type": "namespace",
                "id": name,
                "label": f"[NS] {name}",
                "data": ns,
            })

        # Workloads
        workloads = self.client.list_workloads()
        for wl in workloads:
            wl_id = wl.get("id", wl.get("workload", "?"))
            self._items.append({
                "type": "workload",
                "id": wl_id,
                "label": f"[WORKLOAD] {wl_id}",
                "data": wl,
            })

        self._filter()

    def _filter(self):
        """Filter items by search term."""
        query = self.search_var.get().lower()
        if query:
            self._filtered_items = [i for i in self._items if query in i["label"].lower()]
        else:
            self._filtered_items = list(self._items)

        self.item_list.configure(state="normal")
        self.item_list.delete("1.0", "end")
        for item in self._filtered_items:
            self.item_list.insert("end", item["label"] + "\n")
        self.item_list.configure(state="disabled")
        self.count_label.configure(text=f"{len(self._filtered_items)} items")

    def _on_click(self, event):
        """Handle click on item list."""
        try:
            index = self.item_list.index(f"@{event.x},{event.y}")
            line = int(index.split(".")[0]) - 1
            if 0 <= line < len(self._filtered_items):
                self._show_detail(self._filtered_items[line])
        except Exception:
            pass

    def _show_detail(self, item):
        """Show detail view for selected item."""
        self.detail_title.configure(text=f"{item['type'].upper()}: {item['id']}")
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")

        if item["type"] == "config":
            checksum = item.get("checksum", "?")
            config_data = self.client.get_config(item["id"])
            size = len(config_data) if config_data else 0
            text = f"Module: {item['id']}\nChecksum: {checksum}\nBinary size: {size} bytes\n"
            if config_data:
                text += f"\nRaw hex:\n{config_data.hex()}\n"
                # Parse header
                if len(config_data) >= 10:
                    magic = config_data[:2].hex()
                    version = config_data[2]
                    role_id = config_data[3]
                    roles = {1: "button-matrix", 2: "relay-controller", 3: "stepper-controller",
                             4: "pwm-bank", 5: "fingerprint-gate", 6: "composite-device", 7: "sensor-hub"}
                    text += f"\nHeader:\n  Magic: {magic}\n  Version: {version}\n  Role: {roles.get(role_id, f'0x{role_id:02x}')}\n"
            self.detail_text.insert("end", text)

        elif item["type"] == "namespace":
            self.detail_text.insert("end", json.dumps(item["data"], indent=2))

        elif item["type"] == "workload":
            self.detail_text.insert("end", json.dumps(item["data"], indent=2))

        self.detail_text.configure(state="disabled")


class ServicesMonitorTab(ctk.CTkFrame):
    """Tab 2: Services Monitor."""

    def __init__(self, master, client: BusStorageClient, **kwargs):
        super().__init__(master, **kwargs)
        self.client = client
        self.configure(fg_color="transparent")
        self._auto_refresh = True

        # Services grid
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.text = ctk.CTkTextbox(self, font=("Courier", 12), fg_color="#0a0a0a",
                                    state="disabled", wrap="word")
        self.text.pack(fill="both", expand=True, padx=8, pady=8)

        # Auto-refresh
        self._refresh_tick()

    def _refresh_tick(self):
        if not self._auto_refresh:
            return
        self._update_status()
        self.after(5000, self._refresh_tick)

    def _update_status(self):
        lines = []
        lines.append("=" * 60)
        lines.append("  COMPOSEIO SERVICES MONITOR")
        lines.append("=" * 60)
        lines.append("")

        # Bus Storage
        lines.append("--- Bus Storage (mqtt-broker.local:8420) ---")
        try:
            status = self.client.status()
            lines.append(f"  Status:     ONLINE")
            lines.append(f"  Uptime:     {status.get('uptime_seconds', '?')}s")
            lines.append(f"  Configs:    {status.get('configs', '?')}")
            lines.append(f"  Namespaces: {status.get('namespaces', '?')}")
            lines.append(f"  Workloads:  {status.get('workloads', '?')}")
            storage = status.get('storage_bytes', 0)
            lines.append(f"  Storage:    {storage:,} bytes")
            disk_free = status.get('disk_free_bytes', 0)
            lines.append(f"  Disk free:  {disk_free / (1024**3):.1f} GB")
            lines.append(f"  Modules:    {', '.join(status.get('modules', []))}")
        except Exception as e:
            lines.append(f"  Status:     OFFLINE ({e})")
        lines.append("")

        # MQTT Broker
        lines.append(f"--- MQTT Broker (mqtt-broker.local:{MQTT_PORT}) ---")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((MQTT_BROKER, MQTT_PORT))
            sock.close()
            if result == 0:
                lines.append(f"  Status:     ONLINE (port {MQTT_PORT} open)")
            else:
                lines.append(f"  Status:     OFFLINE (port {MQTT_PORT} closed)")
        except Exception as e:
            lines.append(f"  Status:     UNKNOWN ({e})")
        lines.append("")

        # Registered namespaces
        lines.append("--- Registered Namespaces ---")
        try:
            namespaces = self.client.list_namespaces()
            for ns in namespaces:
                name = ns.get("name", "?")
                engine = ns.get("engine", "?")
                label = ns.get("label", "")
                label_str = f" ({label})" if label else ""
                lines.append(f"  {name}{label_str}: {engine}")
            if not namespaces:
                lines.append("  (none)")
        except Exception:
            lines.append("  (unavailable)")
        lines.append("")

        # Workloads
        lines.append("--- Workloads ---")
        try:
            workloads = self.client.list_workloads()
            for wl in workloads:
                wl_id = wl.get("id", "?")
                runtime = wl.get("runtime", "?")
                lines.append(f"  {wl_id}: runtime={runtime}")
            if not workloads:
                lines.append("  (none)")
        except Exception:
            lines.append("  (unavailable)")
        lines.append("")

        lines.append(f"Last updated: {time.strftime('%H:%M:%S')}")

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(lines))
        self.text.configure(state="disabled")

    def stop(self):
        self._auto_refresh = False


class NamespaceInspectorTab(ctk.CTkFrame):
    """Tab 3: Namespace Inspector."""

    def __init__(self, master, client: BusStorageClient, **kwargs):
        super().__init__(master, **kwargs)
        self.client = client
        self.configure(fg_color="transparent")
        self._auto_refresh = True

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkButton(toolbar, text="Refresh", width=80, command=self._update).pack(side="left", padx=4)
        self.ns_count = ctk.CTkLabel(toolbar, text="", font=("Courier", 10), text_color="#666666")
        self.ns_count.pack(side="right")

        # Namespace table
        self.text = ctk.CTkTextbox(self, font=("Courier", 11), fg_color="#0a0a0a",
                                    state="disabled", wrap="none")
        self.text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._refresh_tick()

    def _refresh_tick(self):
        if not self._auto_refresh:
            return
        self._update()
        self.after(5000, self._refresh_tick)

    def _update(self):
        try:
            namespaces = self.client.list_namespaces()
        except Exception:
            namespaces = []

        lines = []
        header = f"{'NAMESPACE':<30} {'ENGINE':<18} {'VALUE':<15} {'SCOPE':<8} {'LABEL'}"
        lines.append(header)
        lines.append("-" * len(header))

        for ns in namespaces:
            name = ns.get("name", "?")[:29]
            engine = ns.get("engine", "?")[:17]
            value = str(ns.get("value", ""))[:14]
            scope = ns.get("scope", "?")[:7]
            label = ns.get("label", "")
            lines.append(f"{name:<30} {engine:<18} {value:<15} {scope:<8} {label}")

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(lines))
        self.text.configure(state="disabled")
        self.ns_count.configure(text=f"{len(namespaces)} namespaces")

    def stop(self):
        self._auto_refresh = False


class DocumentBrowserTab(ctk.CTkFrame):
    """Tab 4: Document Store Browser."""

    def __init__(self, master, ds_client: DocStore, **kwargs):
        super().__init__(master, **kwargs)
        self.ds = ds_client
        self.configure(fg_color="transparent")
        self._current_collection: str | None = None

        # Split: left (collections), right (documents)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(1, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))
        ctk.CTkButton(toolbar, text="Refresh", width=80, command=self._refresh_collections).pack(side="left", padx=4)

        # Query bar
        self.query_var = ctk.StringVar()
        ctk.CTkEntry(toolbar, textvariable=self.query_var, placeholder_text="Query: field>value,field=value",
                     width=350).pack(side="left", padx=4)
        ctk.CTkButton(toolbar, text="Query", width=60, command=self._run_query).pack(side="left", padx=4)

        self.status_label = ctk.CTkLabel(toolbar, text="", font=("Courier", 10), text_color="#666666")
        self.status_label.pack(side="right")

        # Left: collection list
        left = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8)
        left.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))

        ctk.CTkLabel(left, text="Collections", font=("Courier", 11, "bold")).pack(pady=(8, 4))
        self.coll_list = ctk.CTkScrollableFrame(left, fg_color="#0a0a0a")
        self.coll_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Right: document viewer
        right = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=8)
        right.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 8))

        self.doc_title = ctk.CTkLabel(right, text="Select a collection", font=("Helvetica", 13, "bold"))
        self.doc_title.pack(pady=(8, 4))

        self.doc_text = ctk.CTkTextbox(right, font=("Courier", 11), fg_color="#0a0a0a",
                                        state="disabled", wrap="word")
        self.doc_text.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        self._refresh_collections()

    def _refresh_collections(self):
        for w in self.coll_list.winfo_children():
            w.destroy()
        try:
            collections = self.ds.list_collections()
        except Exception:
            collections = []

        for coll in collections:
            name = coll.get("name", "?")
            count = coll.get("count", 0)
            btn = ctk.CTkButton(
                self.coll_list, text=f"{name} ({count})",
                fg_color="transparent", hover_color="#333333",
                anchor="w", font=("Courier", 11),
                command=lambda n=name: self._select_collection(n),
            )
            btn.pack(fill="x", pady=1)

        self.status_label.configure(text=f"{len(collections)} collections")

    def _select_collection(self, name: str):
        self._current_collection = name
        self.doc_title.configure(text=f"Collection: {name}")
        try:
            doc_ids = self.ds.list_docs(name)
            docs = []
            for did in doc_ids[:50]:  # Limit display
                doc = self.ds.get(name, did)
                if doc:
                    docs.append(doc)
            self._show_docs(docs)
        except Exception as e:
            self._show_text(f"Error: {e}")

    def _run_query(self):
        if not self._current_collection:
            return
        query = self.query_var.get().strip()
        if not query:
            self._select_collection(self._current_collection)
            return
        try:
            result = self.ds.compact_query(self._current_collection, query)
            self._show_text(f"Query: {query}\n\nResult:\n{result}")
        except Exception as e:
            self._show_text(f"Query error: {e}")

    def _show_docs(self, docs: list[dict]):
        import json
        text = json.dumps(docs, indent=2, default=str)
        self._show_text(text)
        self.status_label.configure(text=f"{len(docs)} documents")

    def _show_text(self, text: str):
        self.doc_text.configure(state="normal")
        self.doc_text.delete("1.0", "end")
        self.doc_text.insert("end", text)
        self.doc_text.configure(state="disabled")


class AdminApp(ctk.CTkFrame):
    """ComposeIO Admin Console — tabbed interface."""

    name = "ComposeIO Admin"
    icon_path = None

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="#1a1a1a")
        self.client = BusStorageClient(BUS_STORAGE_URL)
        self.ds_client = DocStore(host=DOCSTORE_HOST)

        # Title
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(title_frame, text="ComposeIO Admin Console",
                     font=("Helvetica", 20, "bold")).pack(side="left")

        # Connection indicator
        self.conn_label = ctk.CTkLabel(title_frame, text="", font=("Courier", 10))
        self.conn_label.pack(side="right")
        self._check_connection()

        # Tabview
        self.tabview = ctk.CTkTabview(self, fg_color="#1a1a1a")
        self.tabview.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        # Tab 1: Storage Browser
        tab_storage = self.tabview.add("Storage Browser")
        self.storage_tab = StorageBrowserTab(tab_storage, self.client)
        self.storage_tab.pack(fill="both", expand=True)
        self.storage_tab.refresh()

        # Tab 2: Services Monitor
        tab_services = self.tabview.add("Services Monitor")
        self.services_tab = ServicesMonitorTab(tab_services, self.client)
        self.services_tab.pack(fill="both", expand=True)

        # Tab 3: Namespace Inspector
        tab_ns = self.tabview.add("Namespace Inspector")
        self.ns_tab = NamespaceInspectorTab(tab_ns, self.client)
        self.ns_tab.pack(fill="both", expand=True)

        # Tab 4: Document Browser
        tab_docs = self.tabview.add("Document Browser")
        self.docs_tab = DocumentBrowserTab(tab_docs, self.ds_client)
        self.docs_tab.pack(fill="both", expand=True)

    def _check_connection(self):
        try:
            status = self.client.status()
            self.conn_label.configure(
                text=f"Connected — {BUS_STORAGE_URL}",
                text_color="#00FF88",
            )
        except Exception:
            self.conn_label.configure(
                text=f"Disconnected — {BUS_STORAGE_URL}",
                text_color="#FF4444",
            )

    def cleanup(self):
        self.services_tab.stop()
        self.ns_tab.stop()

    # Launcher lifecycle
    def on_activate(self, hw=None):
        self.storage_tab.refresh()

    def on_deactivate(self):
        pass


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("ComposeIO Admin Console")
    root.geometry("1200x800")

    app = AdminApp(root)
    app.pack(fill="both", expand=True)

    root.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
