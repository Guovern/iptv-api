from html import escape

from PySide6.QtCore import QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QDesktopServices, QHideEvent, QPixmap, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets import FluentIcon, IconWidget, TransparentToolButton, isDarkTheme, qconfig

from utils.i18n import t
from utils.resources import resource_path
from utils.sponsors import HELODATA_APP_URL


class MarqueeLinkLabel(QWidget):
    link_activated = Signal()
    FRAME_INTERVAL_MS = 25
    EDGE_PAUSE_TICKS = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.label.setOpenExternalLinks(False)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.linkActivated.connect(lambda _link: self.link_activated.emit())
        self._offset = 0
        self._direction = -1
        self._pause_ticks = self.EDGE_PAUSE_TICKS
        self.timer = QTimer(self)
        self.timer.setInterval(self.FRAME_INTERVAL_MS)
        self.timer.timeout.connect(self._advance)

    def set_content(self, coupon: str, coupon_separator: str, message: str, link_text: str, text_color: str, link_color: str):
        self.label.setText(
            f'<span style="color:{text_color};"><b>{escape(coupon)}</b>{escape(coupon_separator)}&nbsp;{escape(message)}&nbsp;&nbsp;'
            f'<a href="helodata" style="color:{link_color}; text-decoration:none;">'
            f'<b>{escape(link_text)}</b></a></span>'
        )
        self.label.setAccessibleName(f"{coupon} {message} {link_text}")
        self.label.adjustSize()
        self._reset_motion()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._reset_motion()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self._reset_motion()

    def hideEvent(self, event: QHideEvent):
        self.timer.stop()
        super().hideEvent(event)

    def _reset_motion(self):
        text_width = self.label.sizeHint().width()
        self._offset = 0
        self._direction = -1
        self._pause_ticks = self.EDGE_PAUSE_TICKS
        self.label.setGeometry(0, 0, max(text_width, self.width()), self.height())
        if text_width > self.width() and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()

    def _advance(self):
        overflow = self.label.sizeHint().width() - self.width()
        if overflow <= 0:
            self._reset_motion()
            return
        if self._pause_ticks:
            self._pause_ticks -= 1
            return
        self._offset += self._direction
        if self._offset <= -overflow:
            self._offset = -overflow
            self._direction = 1
            self._pause_ticks = self.EDGE_PAUSE_TICKS
        elif self._offset >= 0:
            self._offset = 0
            self._direction = -1
            self._pause_ticks = self.EDGE_PAUSE_TICKS
        self.label.move(self._offset, 0)


class SponsorBanner(QWidget):
    dismissed = Signal()
    LOGO_SIZE = (55, 24)
    LOGO_SOURCE_RECT = (17, 0, 114, 50)
    LOGO_DEVICE_PIXEL_RATIO = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sponsorBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(9)

        self.icon = IconWidget(self)
        self.icon.setFixedSize(18, 18)
        self.logo = QLabel(self)
        self.logo.setFixedSize(*self.LOGO_SIZE)
        self.logo.setAccessibleName("Helodata")
        source_logo = QPixmap(resource_path("docs/images/helodata.png"))
        logo = source_logo.copy(*self.LOGO_SOURCE_RECT).scaled(
            self.LOGO_SIZE[0] * self.LOGO_DEVICE_PIXEL_RATIO,
            self.LOGO_SIZE[1] * self.LOGO_DEVICE_PIXEL_RATIO,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        logo.setDevicePixelRatio(self.LOGO_DEVICE_PIXEL_RATIO)
        self.logo.setPixmap(logo)
        self.marquee = MarqueeLinkLabel(self)
        self.close_button = TransparentToolButton(FluentIcon.CLOSE, self)
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self._dismiss)
        self.marquee.link_activated.connect(self._open_sponsor)

        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.marquee, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)

        qconfig.themeChangedFinished.connect(self._apply_theme)
        self._apply_theme()

    def retranslate(self):
        self._apply_theme()

    def _apply_theme(self, *_args):
        dark = isDarkTheme()
        background = "#172554" if dark else "#EFF6FF"
        border = "#1E40AF" if dark else "#BFDBFE"
        foreground = "#DBEAFE" if dark else "#1E3A8A"
        accent = "#93C5FD" if dark else "#1D4ED8"
        self.setStyleSheet(
            f"QWidget#sponsorBanner {{ background-color: {background}; "
            f"border: 1px solid {border}; border-radius: 8px; }}"
        )
        self.icon.setIcon(FluentIcon.MEGAPHONE.icon(color=QColor(accent)))
        self.marquee.set_content(
            t("desktop.sponsor_coupon"),
            t("desktop.sponsor_coupon_separator"),
            t("desktop.sponsor_promotion"),
            t("desktop.sponsor_link"),
            foreground,
            accent,
        )
        self.close_button.setToolTip(t("desktop.sponsor_close"))
        self.close_button.setAccessibleName(t("desktop.sponsor_close"))

    def _open_sponsor(self):
        QDesktopServices.openUrl(QUrl(HELODATA_APP_URL))

    def _dismiss(self):
        self.hide()
        self.dismissed.emit()
