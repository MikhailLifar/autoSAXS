from __future__ import annotations

from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..logic.package_update import (
    LIVEVIEW_UPDATE_SPEC,
    is_editable_install,
    pip_upgrade_argv,
)


class UpdateDialog(QDialog):
    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update autosaxs")
        self.setMinimumSize(640, 420)
        self._process: QProcess | None = None
        self._finished_ok = False

        intro = QLabel(
            "This will upgrade <b>autosaxs[gui]</b> in the current Python environment "
            "from the project git <code>main</code> branch.<br><br>"
            f"<code>{LIVEVIEW_UPDATE_SPEC}</code><br><br>"
            "The application must be restarted after a successful update."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(intro.RichText)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("pip output will appear here…")

        self._btn_run = QPushButton("Start update")
        self._btn_run.clicked.connect(self._start_update)
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.reject)
        self._btn_quit = QPushButton("Quit now")
        self._btn_quit.setEnabled(False)
        self._btn_quit.clicked.connect(self._quit_application)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._btn_run)
        row.addWidget(self._btn_quit)
        row.addWidget(self._btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self._log, 1)
        lay.addLayout(row)

        if is_editable_install():
            QMessageBox.warning(
                self,
                "Editable install",
                "autosaxs appears to be installed in editable mode. "
                "In-app pip upgrade may not replace your working copy. "
                "Consider pulling the git repo and reinstalling manually.",
            )

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self._log.moveCursor(self._log.textCursor().End)
        self._log.insertPlainText(text)
        if not text.endswith("\n"):
            self._log.insertPlainText("\n")

    def _start_update(self) -> None:
        if self._process is not None and self._process.state() != QProcess.NotRunning:
            return
        self._btn_run.setEnabled(False)
        self._log.clear()
        self._append_log("$ " + " ".join(pip_upgrade_argv()))

        proc = QProcess(self)
        self._process = proc
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_ready_read)
        proc.finished.connect(self._on_finished)
        proc.start(pip_upgrade_argv()[0], pip_upgrade_argv()[1:])

    def _on_ready_read(self) -> None:
        proc = self._process
        if proc is None:
            return
        data = proc.readAllStandardOutput()
        try:
            self._append_log(bytes(data).decode("utf-8", errors="replace"))
        except Exception:
            pass

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._btn_run.setEnabled(True)
        self._finished_ok = int(exit_code) == 0
        if self._finished_ok:
            self._append_log("\nUpdate finished successfully.")
            self._btn_quit.setEnabled(True)
            QMessageBox.information(
                self,
                "Update complete",
                "autosaxs[gui] was upgraded.\n\n"
                "Restart guisaxs-liveview to use the new version.",
            )
        else:
            QMessageBox.critical(
                self,
                "Update failed",
                f"pip exited with code {exit_code}. See the log for details.",
            )

    def _quit_application(self) -> None:
        win = self.window()
        while win is not None and win.parentWidget() is not None:
            win = win.parentWidget()
        if win is not None:
            win.close()
        self.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        proc = self._process
        if proc is not None and proc.state() != QProcess.NotRunning:
            proc.kill()
            proc.waitForFinished(3000)
        super().closeEvent(event)
