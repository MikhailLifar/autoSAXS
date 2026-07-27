from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QTimer, QUrl, Qt
from PyQt5.QtGui import (
    QDesktopServices,
    QImage,
    QPixmap,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
)
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
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
    """Load bundled HTML pages from a directory on disk.

    QTextBrowser's CSS engine does not reliably honor ``max-width`` / ``height: auto``
    on images, so large screenshots overflow and can distort layout/text. Images are
    therefore scaled in code to the current viewport width, preserving aspect ratio.
    """

    def __init__(self, *, help_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._help_root = help_root
        self._fitting_images = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_images_to_viewport)
        # Wrap to the viewport, not to the intrinsic width of large screenshots.
        self.setLineWrapMode(QTextEdit.WidgetWidth)
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

    def setSource(self, url: QUrl) -> None:  # noqa: N802 — Qt API
        super().setSource(url)
        self._schedule_fit_images()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._schedule_fit_images()

    def _schedule_fit_images(self) -> None:
        self._fit_timer.start(40)

    def _image_pixmap(self, name: str) -> QPixmap | None:
        doc = self.document()
        url = QUrl(name)
        res = doc.resource(QTextDocument.ImageResource, url)
        if isinstance(res, QPixmap) and not res.isNull():
            return res
        if isinstance(res, QImage) and not res.isNull():
            return QPixmap.fromImage(res)
        src = self.source()
        if src.isValid() and src.isLocalFile():
            cand = (Path(src.toLocalFile()).parent / name).resolve()
            if cand.is_file():
                pm = QPixmap(str(cand))
                if not pm.isNull():
                    return pm
        return None

    def _fit_images_to_viewport(self) -> None:
        if self._fitting_images:
            return
        doc = self.document()
        margin = int(doc.documentMargin())
        max_w = int(self.viewport().width()) - 2 * margin - 8
        if max_w < 80:
            return

        fragments: list[tuple[int, int, QTextImageFormat]] = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isImageFormat():
                    fragments.append(
                        (frag.position(), frag.length(), frag.charFormat().toImageFormat())
                    )
                it += 1
            block = block.next()
        if not fragments:
            return

        self._fitting_images = True
        missing = False
        try:
            cursor = QTextCursor(doc)
            for pos, length, ifmt in fragments:
                pm = self._image_pixmap(ifmt.name())
                if pm is None:
                    missing = True
                    continue
                ow = int(pm.width())
                oh = int(pm.height())
                if ow <= 0 or oh <= 0:
                    missing = True
                    continue
                nw = min(ow, max_w)
                nh = max(1, int(round(oh * float(nw) / float(ow))))
                if abs(float(ifmt.width()) - nw) < 0.5 and abs(float(ifmt.height()) - nh) < 0.5:
                    continue
                ifmt.setWidth(nw)
                ifmt.setHeight(nh)
                cursor.setPosition(pos)
                cursor.setPosition(pos + length, QTextCursor.KeepAnchor)
                cursor.setCharFormat(ifmt)
        finally:
            self._fitting_images = False
        if missing:
            # Image resources can arrive after setSource; retry briefly.
            self._fit_timer.start(120)


class HtmlHelpDialog(QDialog):
    def __init__(self, *, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setMinimumSize(900, 620)
        self.setSizeGripEnabled(True)
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
