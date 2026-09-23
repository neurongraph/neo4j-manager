"""Textual TUI: presentation layer over config/instance/sync/docker_ops.

No orchestration logic lives here -- every action calls straight into the
existing modules, exactly as the flag-based CLI does.
"""

from __future__ import annotations

import webbrowser
from typing import Callable

import pyperclip
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    Static,
    Switch,
)

from neo4j_manager import config as cfg
from neo4j_manager import docker_ops
from neo4j_manager import instance as inst
from neo4j_manager import sync as sync_mod
from neo4j_manager.docker_ops import DockerOpsError
from neo4j_manager.sync import SyncError


def _instance_url(name: str) -> str | None:
    """Neo4j Browser URL for a running instance, or None if it isn't running."""
    _, instance = inst.get(name)
    if docker_ops.container_status(instance.container_name) != "running":
        return None
    return f"http://127.0.0.1:{instance.http_port}"


class BusyScreen(ModalScreen[None]):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="busy-box"):
            yield Label(self._message)
            yield LoadingIndicator()


class MessageScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_me", "Close")]

    def __init__(self, title: str, message: str, error: bool = False) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="message-box", classes="error" if self._error else ""):
            yield Label(self._title, classes="title")
            with VerticalScroll():
                yield Static(self._message)
            yield Button("OK", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_me(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._question)
            with Horizontal():
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class RemoveConfirmScreen(ModalScreen[dict | None]):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(f"Remove instance {self._name!r}?")
            with Horizontal():
                yield Label("Also delete local data (purge): ")
                yield Switch(id="purge")
            with Horizontal():
                yield Button("Remove", id="yes", variant="error")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss({"purge": self.query_one("#purge", Switch).value})
        else:
            self.dismiss(None)


class CreateInstanceScreen(ModalScreen[dict | None]):
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form-box"):
            yield Label("Create new instance", classes="title")
            yield Label("Name (required)")
            yield Input(placeholder="mydb", id="f-name")
            yield Label("HTTP port (blank = auto)")
            yield Input(placeholder="auto", id="f-http")
            yield Label("Bolt port (blank = auto)")
            yield Input(placeholder="auto", id="f-bolt")
            yield Label("Data dir (blank = ~/neo4j-data/<name>)")
            yield Input(placeholder="auto", id="f-datadir")
            yield Label("Image")
            yield Input(value="neo4j:latest", id="f-image")
            yield Label("Plugins (comma separated)")
            yield Input(value="apoc", id="f-plugins")
            yield Label("Data repo URL (optional, can set later)")
            yield Input(placeholder="git@github.com:you/mydb-neo4j-data.git", id="f-repo")
            with Horizontal():
                yield Button("Create", id="submit", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        name = self.query_one("#f-name", Input).value.strip()
        if not name:
            self.query_one("#f-name", Input).focus()
            return

        def int_or_none(value: str) -> int | None:
            value = value.strip()
            return int(value) if value else None

        self.dismiss(
            {
                "name": name,
                "http_port": int_or_none(self.query_one("#f-http", Input).value),
                "bolt_port": int_or_none(self.query_one("#f-bolt", Input).value),
                "data_dir": self.query_one("#f-datadir", Input).value.strip() or None,
                "image": self.query_one("#f-image", Input).value.strip() or "neo4j:latest",
                "plugins": [
                    p.strip() for p in self.query_one("#f-plugins", Input).value.split(",") if p.strip()
                ]
                or ["apoc"],
                "repo": self.query_one("#f-repo", Input).value.strip(),
            }
        )


class WorkerScreen(Screen):
    """Adds `_run_worker`: run a blocking call in a thread behind a busy overlay."""

    def _refresh(self) -> None:
        raise NotImplementedError

    def _run_worker(self, fn: Callable[[], None], busy_message: str, on_success: Callable[[], None] | None = None) -> None:
        busy = BusyScreen(busy_message)
        self.app.push_screen(busy)
        self._do_run_worker(fn, busy, on_success)

    @work(thread=True)
    def _do_run_worker(self, fn: Callable[[], None], busy_screen: BusyScreen, on_success) -> None:
        error = None
        try:
            fn()
        except (DockerOpsError, SyncError, SystemExit) as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 -- surfaced to the user, not swallowed
            error = f"{type(e).__name__}: {e}"
        self.app.call_from_thread(self._worker_done, busy_screen, error, on_success)

    def _worker_done(self, busy_screen: BusyScreen, error: str | None, on_success) -> None:
        busy_screen.dismiss(None)
        self._refresh()
        if error:
            self.app.push_screen(MessageScreen("Error", error, error=True))
        elif on_success:
            on_success()


class InstanceListScreen(WorkerScreen):
    BINDINGS = [
        Binding("n", "new_instance", "New"),
        Binding("s", "instance_start", "Start"),
        Binding("x", "instance_stop", "Stop"),
        Binding("r", "instance_restart", "Restart"),
        Binding("l", "instance_shell", "Shell"),
        Binding("b", "instance_browser", "Browser"),
        Binding("p", "instance_push", "Sync push"),
        Binding("u", "instance_pull", "Sync pull"),
        Binding("D", "instance_remove", "Remove"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="instances")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "State", "HTTP", "Bolt", "Image", "Repo linked")
        self.sub_title = "Enter: open selected"
        self._names: list[str] = []
        self._refresh()
        self.set_interval(4.0, self._refresh)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_detail()

    def _refresh(self) -> None:
        table = self.query_one(DataTable)
        prev_row = table.cursor_row
        table.clear()
        self._names = []
        config = cfg.load()
        for row in inst.list_status():
            linked = "yes" if config.instances[row["name"]].data_repo else "no"
            table.add_row(
                row["name"], row["state"], str(row["http_port"]), str(row["bolt_port"]), row["image"], linked
            )
            self._names.append(row["name"])
        if self._names and prev_row is not None:
            table.move_cursor(row=min(prev_row, len(self._names) - 1))

    def _selected_name(self) -> str | None:
        table = self.query_one(DataTable)
        if not self._names or table.cursor_row is None:
            return None
        if 0 <= table.cursor_row < len(self._names):
            return self._names[table.cursor_row]
        return None

    def action_new_instance(self) -> None:
        def handle(data: dict | None) -> None:
            if not data:
                return
            self._run_worker(
                lambda: inst.create(
                    data["name"],
                    http_port=data["http_port"],
                    bolt_port=data["bolt_port"],
                    data_dir=data["data_dir"],
                    plugins=data["plugins"],
                    image=data["image"],
                    repo=data["repo"],
                ),
                f"Creating {data['name']!r}...",
            )

        self.app.push_screen(CreateInstanceScreen(), handle)

    def action_open_detail(self) -> None:
        name = self._selected_name()
        if name:
            self.app.push_screen(InstanceDetailScreen(name))

    def action_instance_start(self) -> None:
        name = self._selected_name()
        if name:
            self._run_worker(lambda: inst.start(name), f"Starting {name!r}...")

    def action_instance_stop(self) -> None:
        name = self._selected_name()
        if name:
            self._run_worker(lambda: inst.stop(name), f"Stopping {name!r}...")

    def action_instance_restart(self) -> None:
        name = self._selected_name()
        if name:
            self._run_worker(lambda: inst.restart(name), f"Restarting {name!r}...")

    def action_instance_shell(self) -> None:
        name = self._selected_name()
        if not name:
            return
        _, instance = inst.get(name)
        with self.app.suspend():
            docker_ops.cypher_shell_interactive(instance)
        self._refresh()

    def action_instance_browser(self) -> None:
        name = self._selected_name()
        if not name:
            return
        url = _instance_url(name)
        if url is None:
            self.app.push_screen(MessageScreen("Not running", f"{name!r} is not running -- start it first."))
            return
        webbrowser.open(url)

    def action_instance_push(self) -> None:
        name = self._selected_name()
        if name:
            self._run_worker(lambda: sync_mod.push(name), f"Pushing {name!r}...")

    def action_instance_pull(self) -> None:
        name = self._selected_name()
        if not name:
            return
        st = sync_mod.status(name)
        if not st.get("configured"):
            self.app.push_screen(
                MessageScreen("Not linked", f"{name!r} has no data repo linked yet. Open it (Enter) to set one.")
            )
            return
        if st.get("dirty"):
            def handle(result: bool) -> None:
                if result:
                    self._run_worker(lambda: sync_mod.pull(name, force=True), f"Pulling {name!r} (force)...")

            self.app.push_screen(
                ConfirmScreen(
                    f"Local repo for {name!r} has unpushed changes:\n{st['dirty_files']}\nDiscard and pull anyway?"
                ),
                handle,
            )
        else:
            self._run_worker(lambda: sync_mod.pull(name), f"Pulling {name!r}...")

    def action_instance_remove(self) -> None:
        name = self._selected_name()
        if not name:
            return

        def handle(result: dict | None) -> None:
            if result:
                self._run_worker(lambda: inst.remove(name, purge_data=result["purge"]), f"Removing {name!r}...")

        self.app.push_screen(RemoveConfirmScreen(name), handle)

    def action_quit(self) -> None:
        self.app.exit()


class InstanceDetailScreen(WorkerScreen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("v", "toggle_password", "Show/hide password"),
        Binding("c", "copy_password", "Copy password"),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.instance_name = name
        self._show_password = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="detail-box"):
            yield Static(id="detail-summary")

            yield Label("Sync settings", classes="section-title")
            yield Label("Data repo URL")
            yield Input(id="f-repo")
            yield Label("Export branch")
            yield Input(id="f-branch")
            yield Button("Save sync settings", id="save-sync")

            yield Label(
                "Instance settings (changing these recreates the container; on-disk data is preserved)",
                classes="section-title",
            )
            yield Label("Image")
            yield Input(id="f-image")
            yield Label("Plugins (comma separated)")
            yield Input(id="f-plugins")
            yield Label("HTTP port")
            yield Input(id="f-http")
            yield Label("Bolt port")
            yield Input(id="f-bolt")
            yield Button("Save instance settings", id="save-instance", variant="warning")

            yield Label("Data paths (read-only)", classes="section-title")
            yield Static(id="detail-paths")

            yield Label("Actions", classes="section-title")
            with Horizontal():
                yield Button("Start", id="act-start")
                yield Button("Stop", id="act-stop")
                yield Button("Restart", id="act-restart")
            with Horizontal():
                yield Button("Shell", id="act-shell")
                yield Button("Open browser", id="act-browser")
                yield Button("Sync push", id="act-push")
                yield Button("Sync pull", id="act-pull")
                yield Button("Sync status", id="act-syncstatus")
            yield Button("Remove instance", id="act-remove", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        _, instance = inst.get(self.instance_name)
        self._instance = instance
        state = docker_ops.container_status(instance.container_name) or "not created"
        password = instance.auth_password if self._show_password else "*" * 12
        self.query_one("#detail-summary", Static).update(
            f"[b]{instance.name}[/b]  container={instance.container_name}  state={state}\n"
            f"user={instance.auth_user}  password={password}  (press 'v' to reveal/hide)"
        )
        self.query_one("#f-repo", Input).value = instance.data_repo
        self.query_one("#f-branch", Input).value = instance.export_branch
        self.query_one("#f-image", Input).value = instance.image
        self.query_one("#f-plugins", Input).value = ", ".join(instance.plugins)
        self.query_one("#f-http", Input).value = str(instance.http_port)
        self.query_one("#f-bolt", Input).value = str(instance.bolt_port)
        self.query_one("#detail-paths", Static).update(
            f"data:    {instance.data_dir}\n"
            f"logs:    {instance.logs_dir}\n"
            f"import:  {instance.import_dir}\n"
            f"plugins: {instance.plugins_dir}\n"
            f"repo:    {instance.data_repo_path or '(not linked)'}"
        )

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_toggle_password(self) -> None:
        self._show_password = not self._show_password
        self._refresh()

    def action_copy_password(self) -> None:
        _, instance = inst.get(self.instance_name)
        pyperclip.copy(instance.auth_password)
        self.app.push_screen(MessageScreen("Copied", "Password copied to clipboard."))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save-sync":
            self._save_sync()
        elif bid == "save-instance":
            self._save_instance()
        elif bid == "act-start":
            self._run_worker(lambda: inst.start(self.instance_name), "Starting...")
        elif bid == "act-stop":
            self._run_worker(lambda: inst.stop(self.instance_name), "Stopping...")
        elif bid == "act-restart":
            self._run_worker(lambda: inst.restart(self.instance_name), "Restarting...")
        elif bid == "act-shell":
            self._open_shell()
        elif bid == "act-browser":
            self._open_browser()
        elif bid == "act-push":
            self._run_worker(lambda: sync_mod.push(self.instance_name), "Pushing...")
        elif bid == "act-pull":
            self._do_pull()
        elif bid == "act-syncstatus":
            self._show_sync_status()
        elif bid == "act-remove":
            self._do_remove()

    def _save_sync(self) -> None:
        repo_url = self.query_one("#f-repo", Input).value.strip()
        branch = self.query_one("#f-branch", Input).value.strip() or "main"

        config, instance = inst.get(self.instance_name)
        instance.export_branch = branch
        config.instances[self.instance_name] = instance
        cfg.save(config)

        if not repo_url:
            self._refresh()
            self.app.push_screen(MessageScreen("Saved", "Sync settings updated."))
            return

        # Goes through sync.init_repo() (same as `sync init` on the CLI) so
        # data_repo_path is always set correctly and the local working copy
        # is actually cloned/initialized -- never hand-set data_repo alone.
        self._run_worker(
            lambda: sync_mod.init_repo(self.instance_name, repo_url),
            "Linking data repo...",
            on_success=lambda: self.app.push_screen(MessageScreen("Saved", "Sync settings updated.")),
        )

    def _save_instance(self) -> None:
        image = self.query_one("#f-image", Input).value.strip() or "neo4j:latest"
        plugins = [p.strip() for p in self.query_one("#f-plugins", Input).value.split(",") if p.strip()] or ["apoc"]
        try:
            http_port = int(self.query_one("#f-http", Input).value.strip())
            bolt_port = int(self.query_one("#f-bolt", Input).value.strip())
        except ValueError:
            self.app.push_screen(MessageScreen("Invalid input", "Ports must be numbers.", error=True))
            return
        self._run_worker(
            lambda: inst.update(
                self.instance_name, image=image, plugins=plugins, http_port=http_port, bolt_port=bolt_port
            ),
            "Applying changes (may recreate container)...",
        )

    def _open_shell(self) -> None:
        _, instance = inst.get(self.instance_name)
        with self.app.suspend():
            docker_ops.cypher_shell_interactive(instance)
        self._refresh()

    def _open_browser(self) -> None:
        url = _instance_url(self.instance_name)
        if url is None:
            self.app.push_screen(MessageScreen("Not running", "Start the instance first."))
            return
        webbrowser.open(url)

    def _do_pull(self) -> None:
        st = sync_mod.status(self.instance_name)
        if not st.get("configured"):
            self.app.push_screen(MessageScreen("Not linked", "Set a data repo URL under Sync settings first."))
            return
        if st.get("dirty"):
            def handle(result: bool) -> None:
                if result:
                    self._run_worker(lambda: sync_mod.pull(self.instance_name, force=True), "Pulling (force)...")

            self.app.push_screen(
                ConfirmScreen(
                    f"Local repo has unpushed changes:\n{st['dirty_files']}\nDiscard and pull anyway?"
                ),
                handle,
            )
        else:
            self._run_worker(lambda: sync_mod.pull(self.instance_name), "Pulling...")

    def _show_sync_status(self) -> None:
        st = sync_mod.status(self.instance_name)
        if not st.get("configured"):
            msg = "No data repo linked yet. Set one under Sync settings above."
        else:
            msg = f"ahead={st['ahead']} behind={st['behind']} dirty={'yes' if st['dirty'] else 'no'}"
            if st["dirty"]:
                msg += f"\n\n{st['dirty_files']}"
        self.app.push_screen(MessageScreen("Sync status", msg))

    def _do_remove(self) -> None:
        def handle(result: dict | None) -> None:
            if result:
                self._run_worker(
                    lambda: inst.remove(self.instance_name, purge_data=result["purge"]),
                    "Removing...",
                    on_success=self.app.pop_screen,
                )

        self.app.push_screen(RemoveConfirmScreen(self.instance_name), handle)


class Neo4jManagerApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "neo4j-manager"

    def on_mount(self) -> None:
        self.push_screen(InstanceListScreen())


def run() -> None:
    Neo4jManagerApp().run()
