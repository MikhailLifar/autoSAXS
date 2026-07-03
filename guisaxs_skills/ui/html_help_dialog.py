from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autosaxs.resources.help.guisaxs_liveview_loader import (
    liveview_help_manifest_path,
    liveview_help_root,
)

from .help_toc import HelpTocNode, page_file_url, parse_help_manifest


class HtmlHelpBrowser(QTextBrowser):
    """Load bundled HTML pages from a directory on disk."""

    def __init__(self, *, help_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._help_root = help_root
        self.setOpenExternalLinks(True)
        self.anchorClicked.connect(self._on_anchor_clicked)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        self.setSource(url)

    def show_page(self, page: str) -> None:
        url = page_file_url(help_root=self._help_root, page=page)
        self.setSource(QUrl(url))


class HtmlHelpDialog(QDialog):
    def __init__(self, *, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(900, 620)
        self._ready = False
        self._home_page = "html/index.html"

        try:
            self._help_root = liveview_help_root()
            home, toc_nodes = parse_help_manifest(liveview_help_manifest_path())
            self._home_page = home
        except (FileNotFoundError, ValueError, OSError) as e:
            QMessageBox.critical(self, title, str(e))
            return

        self._browser = HtmlHelpBrowser(help_root=self._help_root, parent=self)
        try:
            self._browser.show_page(self._home_page)
        except FileNotFoundError as e:
            QMessageBox.critical(self, title, str(e))
            return

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(240)
        self._populate_toc_tree(toc_nodes)
        self._tree.itemClicked.connect(self._on_toc_clicked)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._tree)
        splitter.addWidget(self._browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640])

        btn_back = QPushButton("← Back")
        btn_back.clicked.connect(self._browser.backward)
        btn_forward = QPushButton("Forward →")
        btn_forward.clicked.connect(self._browser.forward)
        btn_home = QPushButton("Home")
        btn_home.clicked.connect(lambda: self._browser.show_page(self._home_page))

        nav_row = QHBoxLayout()
        nav_row.addWidget(btn_back)
        nav_row.addWidget(btn_forward)
        nav_row.addWidget(btn_home)
        nav_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(splitter, 1)
        lay.addLayout(nav_row)
        lay.addWidget(buttons)
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def _populate_toc_tree(self, nodes: list[HelpTocNode]) -> None:
        for node in nodes:
            item = QTreeWidgetItem([node.title])
            if node.has_page():
                item.setData(0, Qt.UserRole, node.page)
            for child in self._populate_toc_children(node.children):
                item.addChild(child)
            self._tree.addTopLevelItem(item)
        self._tree.expandToDepth(0)

    def _populate_toc_children(self, nodes: list[HelpTocNode]) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        for node in nodes:
            item = QTreeWidgetItem([node.title])
            if node.has_page():
                item.setData(0, Qt.UserRole, node.page)
            for child in self._populate_toc_children(node.children):
                item.addChild(child)
            items.append(item)
        return items

    def _on_toc_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        page = item.data(0, Qt.UserRole)
        if not isinstance(page, str) or not page.strip():
            return
        try:
            self._browser.show_page(page)
        except FileNotFoundError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
