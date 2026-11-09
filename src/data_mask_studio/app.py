import sys
import ctypes
from pathlib import Path
from collections.abc import Sequence
from typing import cast

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.gui.dialog_localization import install_dialog_button_translations
from data_mask_studio.gui.styles import apply_application_theme
from data_mask_studio.publication import PublicationError
from data_mask_studio.environment import EnvironmentError
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService

APP_USER_MODEL_ID = "com.gustavowallace.datamaskstudio"


def create_application(
    argv: Sequence[str] | None = None, *, translator: Translator | None = None,
) -> QApplication:
    """Cria e configura a aplicação Qt."""
    _set_windows_app_user_model_id()
    existing_application = QApplication.instance()
    if existing_application is not None:
        application = cast(QApplication, existing_application)
        _configure_application(application, translator=translator)
        return application

    application = QApplication(list(argv) if argv is not None else sys.argv)
    _configure_application(application, translator=translator)
    return application


def _configure_application(application: QApplication, *, translator: Translator | None = None) -> None:
    application.setApplicationName("Data Mask Studio")
    install_dialog_button_translations(application, translator=translator)
    apply_application_theme(application)
    icon_path = _application_icon_path()
    if icon_path is not None:
        application.setWindowIcon(QIcon(str(icon_path)))


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
        APP_USER_MODEL_ID
    )
    if result != 0:
        raise OSError(f"Não foi possível configurar o AppUserModelID ({result}).")


def _application_icon_path() -> Path | None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_root is not None:
        candidates.append(
            Path(bundle_root) / "assets" / "branding" / "dms_icon.ico"
        )
    candidates.append(
        Path(__file__).resolve().parents[2] / "assets" / "branding" / "dms_icon.ico"
    )
    return next((path for path in candidates if path.is_file()), None)


def run() -> int:
    """Inicia a aplicação e executa o loop de eventos."""
    preferences = PreferencesService()
    language = preferences.resolve_language(preferences.load().preferences.language)
    if getattr(sys, "frozen", False) and preferences.path is not None and not preferences.path.exists():
        language = "zh-CN"
    translator = Translator(language)
    application = create_application(translator=translator)
    try:
        window = MainWindow(preferences_service=preferences, translator=translator)
    except (PublicationError, EnvironmentError) as error:
        title = "Ambiente indisponível" if isinstance(error, EnvironmentError) else "Publicação pendente"
        if translator.locale == "zh-CN":
            title = "本地环境不可用"
            detail = "本地加密库存在未完成的操作或无法访问。请保留加密库和原密钥，检查文件权限及待恢复备份。"
        else:
            detail = str(error)
        QMessageBox.warning(None, title, detail)
        return 1
    window.show()
    return application.exec()
