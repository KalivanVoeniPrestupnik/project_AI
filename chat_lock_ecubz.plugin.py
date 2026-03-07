import json
import weakref
import traceback
from base_plugin import BasePlugin, HookResult, HookStrategy, MethodHook, MenuItemData, MenuItemType
from client_utils import get_last_fragment, get_messages_controller, get_user_config
from android_utils import run_on_ui_thread, log, OnClickListener
from ui.alert import AlertDialogBuilder
from ui.settings import Header, Text, Divider, Input, Switch
from hook_utils import find_class
__id__ = "chat_lock_ecubz"
__name__ = "Chat Lock"
__description__ = """RU: Блокировка чатов и папок под пароль.
Папка = все чаты в ней закрыты.
Команды: .лок .анлок .локлист
EN: Lock chats and folders with password.
Folder = all chats inside are locked.
Commands: .lock .unlock .locklist"""
__author__ = "@eCubzPlugins - @eCubz"
__version__ = "3.5"
__min_version__ = "10.0.0"
__icon__ = "eCubzPlugin/2"
try:
    ChatActivity = find_class("org.telegram.ui.ChatActivity")
    View = find_class("android.view.View")
    ViewGroup = find_class("android.view.ViewGroup")
    FrameLayout = find_class("android.widget.FrameLayout")
    ImageView = find_class("android.widget.ImageView")
    Bitmap = find_class("android.graphics.Bitmap")
    Canvas = find_class("android.graphics.Canvas")
    KeyguardManager = find_class("android.app.KeyguardManager")
    Intent = find_class("android.content.Intent")
    Integer = find_class("java.lang.Integer")
    DialogsActivity = find_class("org.telegram.ui.DialogsActivity")
    RecyclerListView = find_class("org.telegram.ui.Components.RecyclerListView")
    Activity = find_class("android.app.Activity")
    BSBuilder = find_class("org.telegram.ui.ActionBar.BottomSheet$Builder")
    JAVA_CLASSES_OK = True
except Exception as e:
    log(f"[ChatLock] Ошибка импорта Java-классов: {e}")
    JAVA_CLASSES_OK = False
try:
    from org.telegram.messenger import AndroidUtilities
    from org.telegram.ui.ActionBar import Theme
    from android.text import InputType
    from android.widget import EditText, LinearLayout, TextView
    from android.view import Gravity
except Exception:
    pass
class ChatLockPlugin(BasePlugin):
    def __init__(self):
        super().__init__()
        self._chat_resume_hook = None
        self._chat_pause_hook = None
        self._dialogs_resume_hook = None
        self._dialogs_pause_hook = None
        self._archive_menu_hook = None
        self._unlocked_sessions = set()
        self._is_authenticating = False
        self._blur_overlay = None
        self._auth_scrim = None
        self._bitmaps_to_recycle = []
        self._protected_fragment_ref = None
        self._lock_menu_item = None
        self._activity_hook = None
        self._success_callback = None
        self._failure_callback = None
        self._authenticating_dialog_id = None
        self._blocked_window = None
        self._touch_blocker = None
        self._blocked_fv = None
        self._blocked_ab = None
        self._blocked_container = None
    def on_plugin_load(self):
        self.add_on_send_message_hook()
        if JAVA_CLASSES_OK:
            self._hook_chat_lifecycle()
            self._hook_dialogs_lifecycle()
            self._hook_archive_context_menu()
        self._update_lock_menu(None)
        log("[ChatLock] Плагин загружен!")
    def on_plugin_unload(self):
        self._unhook_chat_lifecycle()
        self._unhook_dialogs_lifecycle()
        self._unhook_archive_context_menu()
        self._unhook_activity_result()
        self._cleanup_overlays()
        if self._lock_menu_item:
            self.remove_menu_item(self._lock_menu_item)
            self._lock_menu_item = None
        log("[ChatLock] Плагин выгружен!")
    def _refresh_settings(self):
        try:
            from com.exteragram.messenger.plugins import PluginsController
            PluginsController.getInstance().loadPluginSettings(self.id)
        except Exception as e:
            log(f"[ChatLock] Не удалось обновить настройки: {e}")
    def _toggle_lock_on_exit(self, value):
        self.set_setting("lock_on_exit", value)
        label = "сразу при выходе" if value else "после перезагрузки"
        self._show_toast(f"Блокировка: {label}")
    def _toggle_archive_lock_setting(self, value):
        if value and not self._get_password():
            self._show_toast("Сначала установите пароль!")
            self.set_setting("archive_locked", False)
            self._refresh_settings()
            return
        self.set_setting("archive_locked", value)
        if not value:
            self._unlocked_sessions.discard(1000001)
        label = "заблокирован" if value else "разблокирован"
        self._show_toast(f"Архив {label}")
        self._refresh_settings()
    def _update_lock_menu(self, chat_activity):
        if self._lock_menu_item:
            self.remove_menu_item(self._lock_menu_item)
            self._lock_menu_item = None
        password = self._get_password()
        if not password:
            return
        dialog_id = None
        if chat_activity:
            try:
                dialog_id = chat_activity.getDialogId()
            except Exception:
                pass
        is_locked = self._is_dialog_locked(dialog_id) if dialog_id else False
        if is_locked:
            text = "Разблокировать чат"
            icon = "msg_secret_hw"
        else:
            text = "Заблокировать чат"
            icon = "msg_secret"
        self._lock_menu_item = self.add_menu_item(MenuItemData(
            menu_type=MenuItemType.CHAT_ACTION_MENU,
            text=text,
            on_click=self._handle_menu_lock_toggle,
            icon=icon
        ))
    def _handle_menu_lock_toggle(self, context):
        try:
            fragment = get_last_fragment()
            if not fragment or not isinstance(fragment, ChatActivity):
                self._show_toast("Откройте чат")
                return
            password = self._get_password()
            if not password:
                self._show_toast("Сначала установите пароль!")
                return
            dialog_id = fragment.getDialogId()
            title = self._get_chat_title_by_id(dialog_id)
            chats = self._get_locked_chats()
            already_locked = any(c.get("id") == dialog_id for c in chats)
            if already_locked:
                chats = [c for c in chats if c.get("id") != dialog_id]
                self._save_locked_chats(chats)
                self._unlocked_sessions.discard(dialog_id)
                self._show_toast(f"«{title}» разблокирован")
            else:
                chats.append({"id": dialog_id, "title": title})
                self._save_locked_chats(chats)
                self._show_toast(f"«{title}» заблокирован")
            self._refresh_settings()
            run_on_ui_thread(lambda: self._update_lock_menu(fragment), delay=100)
        except Exception as e:
            log(f"[ChatLock] Ошибка переключения блокировки: {e}")
    def _get_locked_chats(self):
        try:
            data = self.get_setting("locked_chats_json", "[]")
            return json.loads(data)
        except Exception:
            return []
    def _save_locked_chats(self, chats):
        self.set_setting("locked_chats_json", json.dumps(chats))
    def _get_locked_folders(self):
        try:
            data = self.get_setting("locked_folders_json", "[]")
            return json.loads(data)
        except Exception:
            return []
    def _save_locked_folders(self, folders):
        self.set_setting("locked_folders_json", json.dumps(folders))
    def _get_password(self):
        return self.get_setting("lock_password", "")
    def _is_dialog_in_lock_config(self, dialog_id):
        did = int(dialog_id)
        chats = self._get_locked_chats()
        for c in chats:
            if int(c.get("id")) == did:
                return True
        if self._is_dialog_in_locked_folder(did):
            return True
        return False
    def _is_dialog_locked(self, dialog_id):
        did = int(dialog_id)
        if did in self._unlocked_sessions:
            return False
        return self._is_dialog_in_lock_config(did)
    def _is_dialog_in_locked_folder(self, dialog_id):
        try:
            folders = self._get_locked_folders()
            if not folders:
                return False
            locked_ids = {f.get("id") for f in folders}
            mc = get_messages_controller()
            filters = mc.getDialogFilters()
            for i in range(filters.size()):
                df = filters.get(i)
                if df.id not in locked_ids:
                    continue
                dialogs = df.dialogs
                if dialogs:
                    for j in range(dialogs.size()):
                        if dialogs.get(j).id == dialog_id:
                            return True
        except Exception as e:
            log(f"[ChatLock] Ошибка проверки папки: {e}")
        return False
    def _get_chat_title_by_id(self, dialog_id):
        try:
            mc = get_messages_controller()
            uc = get_user_config()
            if dialog_id == uc.getClientUserId():
                return "Избранное"
            elif dialog_id > 0:
                user = mc.getUser(dialog_id)
                if user:
                    return f"{user.first_name or ''} {user.last_name or ''}".strip()
            elif dialog_id < 0:
                chat = mc.getChat(-dialog_id)
                if chat:
                    return chat.title
        except Exception:
            pass
        return str(dialog_id)
    def create_settings(self):
        chats = self._get_locked_chats()
        chat_count = len(chats)
        folders = self._get_locked_folders()
        folder_count = len(folders)
        password = self._get_password()
        pw_status = "✓ Установлен" if password else "✗ Не установлен"
        return [
            Header(text="Блокировка"),
            Text(
                text=f"Пароль: {pw_status}",
                icon="msg_permissions",
                on_click=lambda _: self._show_set_password_dialog()
            ),
            Divider(text="Чаты"),
            Text(
                text="Добавить чат",
                icon="msg_discussion",
                on_click=lambda _: self._add_chat_to_lock()
            ),
            Text(
                text=f"Чатов: {chat_count}",
                icon="msg_list",
                on_click=lambda _: self._show_locked_chats_list()
            ),
            Divider(text="Папки"),
            Text(
                text="Добавить папку",
                icon="msg_folders",
                on_click=lambda _: self._add_folder_to_lock()
            ),
            Text(
                text=f"Папок: {folder_count}",
                icon="msg_list",
                on_click=lambda _: self._show_locked_folders_list()
            ),
            Divider(text="Настройки"),
            Switch(
                key="lock_on_exit",
                text="Блокировать сразу при выходе",
                default=True,
                icon="msg_timer",
                on_change=lambda v: self._toggle_lock_on_exit(v)
            ),
            Switch(
                key="use_biometrics",
                text="Использовать биометрию",
                default=False,
                icon="fingerprint",
                on_change=lambda v: (self.set_setting("use_biometrics", v), self._refresh_settings())
            ),
            Switch(
                key="archive_locked",
                text="Блокировать архив",
                default=False,
                icon="chats_archive_solar",
                on_change=lambda v: self._toggle_archive_lock_setting(v)
            ),
            Divider(text="Действия"),
            Text(
                text="Очистить всё",
                icon="msg_delete",
                red=True,
                on_click=lambda _: self._clear_all_locks()
            ),
            Divider(text="Инструкция"),
            Text(text="1. Установи пароль", icon="msg_info"),
            Text(text="2. Добавь чат или папку", icon="msg_info"),
            Text(text="3. Вход — только по паролю", icon="msg_info"),
            Text(text=".лок / .lock — заблокировать", icon="msg_info"),
            Text(text=".анлок / .unlock — снять", icon="msg_info"),
            Text(text=".локлист / .locklist", icon="msg_info"),
        ]
    def on_send_message_hook(self, account, params):
        try:
            msg = getattr(params, "message", None)
            if not isinstance(msg, str):
                return HookResult()
            msg = msg.strip()
            if msg in (".лок", ".lock"):
                run_on_ui_thread(self._cmd_lock_current_chat)
                return HookResult(strategy=HookStrategy.CANCEL)
            if msg in (".анлок", ".unlock"):
                run_on_ui_thread(self._cmd_unlock_current_chat)
                return HookResult(strategy=HookStrategy.CANCEL)
            if msg in (".локлист", ".locklist"):
                run_on_ui_thread(self._show_locked_list_dialog)
                return HookResult(strategy=HookStrategy.CANCEL)
        except Exception as e:
            log(f"[ChatLock] Ошибка хука: {e}")
        return HookResult()
    def _cmd_lock_current_chat(self):
        try:
            password = self._get_password()
            if not password:
                self._show_toast("Сначала установите пароль!")
                return
            fragment = get_last_fragment()
            if not fragment or not isinstance(fragment, ChatActivity):
                self._show_toast("Откройте чат")
                return
            dialog_id = fragment.getDialogId()
            title = self._get_chat_title_by_id(dialog_id)
            chats = self._get_locked_chats()
            if any(c.get("id") == dialog_id for c in chats):
                self._show_toast(f"«{title}» уже заблокирован")
                return
            chats.append({"id": dialog_id, "title": title})
            self._save_locked_chats(chats)
            self._show_toast(f"«{title}» заблокирован")
            run_on_ui_thread(lambda: self._refresh_settings(), delay=300)
            run_on_ui_thread(lambda f=fragment: self._update_lock_menu(f), delay=100)
        except Exception as e:
            log(f"[ChatLock] Ошибка .лок: {e}")
    def _cmd_unlock_current_chat(self):
        try:
            fragment = get_last_fragment()
            if not fragment or not isinstance(fragment, ChatActivity):
                self._show_toast("Откройте чат")
                return
            dialog_id = fragment.getDialogId()
            title = self._get_chat_title_by_id(dialog_id)
            chats = self._get_locked_chats()
            if not any(c.get("id") == dialog_id for c in chats):
                self._show_toast(f"«{title}» не заблокирован")
                return
            chats = [c for c in chats if c.get("id") != dialog_id]
            self._save_locked_chats(chats)
            self._unlocked_sessions.discard(dialog_id)
            self._show_toast(f"«{title}» разблокирован")
            run_on_ui_thread(lambda: self._refresh_settings(), delay=300)
            run_on_ui_thread(lambda f=fragment: self._update_lock_menu(f), delay=100)
        except Exception as e:
            log(f"[ChatLock] Ошибка .анлок: {e}")
    def _show_locked_list_dialog(self):
        try:
            chats = self._get_locked_chats()
            folders = self._get_locked_folders()
            if not chats and not folders:
                self._show_toast("Нет блокировок")
                return
            lines = []
            if chats:
                lines.append("Чаты:")
                for c in chats:
                    lines.append(f"  • {c.get('title', '?')}")
            if folders:
                if chats:
                    lines.append("")
                lines.append("Папки:")
                for f in folders:
                    lines.append(f"  • {f.get('name', '?')}")
            text = "\n".join(lines)
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            builder = AlertDialogBuilder(context)
            builder.set_title("Заблокировано")
            builder.set_message(text)
            builder.set_positive_button("OK", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка показа списка: {e}")
    def _hook_chat_lifecycle(self):
        try:
            if not self._chat_resume_hook:
                resume_method = ChatActivity.getClass().getDeclaredMethod("onResume")
                self._chat_resume_hook = self.hook_method(resume_method, self._ChatLifecycleHook(self, "resume"))
                pause_method = ChatActivity.getClass().getDeclaredMethod("onPause")
                self._chat_pause_hook = self.hook_method(pause_method, self._ChatLifecycleHook(self, "pause"))
                log("[ChatLock] Хуки ChatActivity установлены.")
        except Exception:
            log(f"[ChatLock] Ошибка хуков ChatActivity: {traceback.format_exc()}")
    def _unhook_chat_lifecycle(self):
        for hook_name in ['_chat_resume_hook', '_chat_pause_hook']:
            hook = getattr(self, hook_name, None)
            if hook:
                try:
                    self.unhook_method(hook)
                except Exception:
                    pass
                setattr(self, hook_name, None)
    def _show_biometric_prompt(self, fragment, on_success, on_failure):
        try:
            self._success_callback = on_success
            self._failure_callback = on_failure
            activity = fragment.getParentActivity()
            if not activity:
                on_failure()
                return
            km = activity.getSystemService("keyguard")
            if not km or not km.isKeyguardSecure():
                self._show_password_prompt(fragment, on_success, on_failure)
                return
            intent = km.createConfirmDeviceCredentialIntent("Подтверждение личности", "Для входа в чат")
            if intent is None:
                self._show_password_prompt(fragment, on_success, on_failure)
                return
            self._hook_activity_result(activity)
            activity.startActivityForResult(intent, 1337)
        except Exception as e:
            log(f"[ChatLock] Ошибка биометрии: {e}")
            self._show_password_prompt(fragment, on_success, on_failure)
    def _hook_activity_result(self, activity):
        self._unhook_activity_result()
        plugin_ref = weakref.ref(self)
        class ActivityResultHook(MethodHook):
            def before_hooked_method(self, param):
                p = plugin_ref()
                if not p: return
                req_code = param.args[0]
                res_code = param.args[1]
                if req_code == 1337:
                    p._unhook_activity_result()
                    if res_code == -1: # Activity.RESULT_OK
                        if p._authenticating_dialog_id is not None:
                            aid = int(p._authenticating_dialog_id)
                            p._unlocked_sessions.add(aid)
                        p._is_authenticating = False
                        if p._success_callback:
                            run_on_ui_thread(p._success_callback)
                    else:
                        p._is_authenticating = True # Keep status synchronously
                        def fallback():
                            f = get_last_fragment()
                            if f:
                                p._show_password_prompt(f, p._success_callback, p._failure_callback)
                            else:
                                if p._failure_callback: p._failure_callback()
                        run_on_ui_thread(fallback)
                    param.setResult(None)
        try:
            method = activity.getClass().getDeclaredMethod("onActivityResult", Integer.TYPE, Integer.TYPE, Intent)
            self._activity_hook = self.hook_method(method, ActivityResultHook())
        except Exception as e:
            log(f"[ChatLock] Ошибка хука onActivityResult: {e}")
    def _unhook_activity_result(self):
        if self._activity_hook:
            try:
                self.unhook_method(self._activity_hook)
            except Exception:
                pass
            self._activity_hook = None
    def _show_password_prompt(self, fragment, on_success, on_failure):
        try:
            context = fragment.getParentActivity()
            if not context:
                return
            input_field = EditText(context)
            input_field.setHint("Введите пароль")
            input_field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD)
            input_field.setTextSize(16)
            input_field.setSingleLine(True)
            try:
                input_field.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                input_field.setHintTextColor(Theme.getColor(Theme.key_dialogTextGray3))
            except Exception:
                pass
            container = LinearLayout(context)
            container.setOrientation(LinearLayout.VERTICAL)
            padding = AndroidUtilities.dp(24)
            container.setPadding(padding, AndroidUtilities.dp(8), padding, AndroidUtilities.dp(8))
            container.addView(input_field)
            builder = AlertDialogBuilder(context)
            builder.set_title("Защищено")
            builder.set_message("Введите пароль для доступа")
            builder.set_view(container)
            builder.set_cancelable(False)
            dismiss_state = {"handled": False}
            def on_unlock(d, w):
                entered = input_field.getText().toString().strip()
                saved = self._get_password()
                if entered == saved:
                    dismiss_state["handled"] = True
                    if self._authenticating_dialog_id is not None:
                        self._unlocked_sessions.add(self._authenticating_dialog_id)
                    on_success()
                else:
                    dismiss_state["handled"] = True
                    self._show_toast("✗ Неверный пароль!")
                    self._block_fragment_input(fragment)
                    on_failure()
            def on_cancel(d, w):
                dismiss_state["handled"] = True
                self._block_fragment_input(fragment)
                on_failure()
            def on_dismiss(b):
                if not dismiss_state["handled"]:
                    on_failure()
            builder.set_on_dismiss_listener(on_dismiss)
            builder.set_canceled_on_touch_outside(False)
            builder.set_positive_button("Войти", on_unlock)
            builder.set_negative_button("Отмена", on_cancel)
            builder.show()
            try:
                pass
            except Exception: pass
        except Exception as e:
            log(f"[ChatLock] Ошибка диалога пароля: {e}")
            on_failure()
    def _apply_blur_effect(self, activity):
        if self._blur_overlay:
            return
        try:
            decor_view = activity.getWindow().getDecorView()
            root_view = decor_view.getRootView()
            width = root_view.getWidth()
            height = root_view.getHeight()
            if width <= 0 or height <= 0:
                return
            screenshot = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            self._bitmaps_to_recycle.append(screenshot)
            root_view.draw(Canvas(screenshot))
            scale = 16
            small = Bitmap.createScaledBitmap(screenshot, width // scale, height // scale, True)
            self._bitmaps_to_recycle.append(small)
            blurred = Bitmap.createScaledBitmap(small, width, height, True)
            self._bitmaps_to_recycle.append(blurred)
            overlay = FrameLayout(activity)
            overlay.setLayoutParams(ViewGroup.LayoutParams(-1, -1))
            overlay.setClickable(True)
            image_view = ImageView(activity)
            image_view.setImageBitmap(blurred)
            image_view.setScaleType(ImageView.ScaleType.CENTER_CROP)
            overlay.addView(image_view)
            if isinstance(decor_view, ViewGroup):
                decor_view.addView(overlay)
                self._blur_overlay = overlay
        except Exception:
            log(f"[ChatLock] Ошибка blur: {traceback.format_exc()}")
            self._remove_blur()
    def _remove_blur(self):
        if not self._blur_overlay:
            return
        try:
            if self._blur_overlay.getParent():
                self._blur_overlay.getParent().removeView(self._blur_overlay)
        except Exception:
            pass
        self._blur_overlay = None
        for bmp in self._bitmaps_to_recycle:
            try:
                if bmp and not bmp.isRecycled():
                    bmp.recycle()
            except Exception:
                pass
        self._bitmaps_to_recycle.clear()
    def _add_scrim(self, fragment):
        if self._auth_scrim is not None:
            return
        try:
            container = fragment.getLayoutContainer()
            if not container:
                fv = fragment.getFragmentView()
                if fv and isinstance(fv, ViewGroup):
                    container = fv
            if container and isinstance(container, ViewGroup):
                scrim = View(fragment.getParentActivity())
                scrim.setBackgroundColor(0x7F000000)
                scrim.setClickable(True)
                scrim.setFocusable(True)
                scrim.setFocusableInTouchMode(True)
                container.addView(scrim, ViewGroup.LayoutParams(-1, -1))
                self._auth_scrim = scrim
        except Exception:
            pass
    def _remove_scrim(self):
        if self._auth_scrim:
            try:
                if self._auth_scrim.getParent():
                    self._auth_scrim.getParent().removeView(self._auth_scrim)
            except Exception:
                pass
            self._auth_scrim = None
    def _block_fragment_input(self, fragment):
        try:
            from android_utils import OnTouchListener
            def block_callback(v, event):
                return True
            self._touch_blocker = OnTouchListener(block_callback)
            fv = fragment.getFragmentView()
            if fv:
                fv.setOnTouchListener(self._touch_blocker)
                self._blocked_fv = fv
            try:
                ab = fragment.getActionBar()
                if ab:
                    ab.setOnTouchListener(self._touch_blocker)
                    self._blocked_ab = ab
            except Exception:
                pass
            container = fragment.getLayoutContainer()
            if container:
                container.setOnTouchListener(self._touch_blocker)
                self._blocked_container = container
        except Exception:
            pass
    def _unblock_fragment_input(self):
        for attr in ('_blocked_fv', '_blocked_ab', '_blocked_container'):
            v = getattr(self, attr, None)
            if v:
                try:
                    v.setOnTouchListener(None)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._touch_blocker = None
    def _cleanup_overlays(self):
        self._remove_blur()
        self._remove_scrim()
        self._unblock_fragment_input()
    def _show_set_password_dialog(self):
        try:
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            input_field = EditText(context)
            input_field.setHint("Новый пароль")
            current = self._get_password()
            if current:
                input_field.setText(current)
            input_field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD)
            input_field.setTextSize(16)
            input_field.setSingleLine(True)
            try:
                input_field.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                input_field.setHintTextColor(Theme.getColor(Theme.key_dialogTextGray3))
            except Exception:
                pass
            container = LinearLayout(context)
            container.setOrientation(LinearLayout.VERTICAL)
            padding = AndroidUtilities.dp(24)
            container.setPadding(padding, AndroidUtilities.dp(8), padding, AndroidUtilities.dp(8))
            container.addView(input_field)
            builder = AlertDialogBuilder(context)
            builder.set_title("Установить пароль")
            builder.set_view(container)
            def on_save(d, w):
                pw = input_field.getText().toString().strip()
                if pw:
                    self.set_setting("lock_password", pw)
                    self._show_toast("✓ Пароль сохранён")
                    run_on_ui_thread(lambda: self._refresh_settings(), delay=300)
                else:
                    self._show_toast("✗ Пароль не может быть пустым")
                d.dismiss()
            builder.set_positive_button("Сохранить", on_save)
            builder.set_negative_button("Отмена", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка диалога пароля: {e}")
    def _add_chat_to_lock(self):
        try:
            password = self._get_password()
            if not password:
                self._show_toast("Сначала установите пароль!")
                return
            from mandre_lib import MandreUI
            MandreUI.select_chat(
                title="Выберите чат для блокировки",
                on_select=self._on_chat_selected_for_lock,
                search_hint="Поиск чата..."
            )
        except ImportError:
            self._add_chat_to_lock_fallback()
        except Exception as e:
            log(f"[ChatLock] Ошибка выбора чата: {e}")
            self._add_chat_to_lock_fallback()
    def _add_chat_to_lock_fallback(self):
        try:
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            input_field = EditText(context)
            input_field.setHint("ID чата (напр: -100123456789)")
            input_field.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_SIGNED)
            input_field.setTextSize(16)
            input_field.setSingleLine(True)
            try:
                input_field.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                input_field.setHintTextColor(Theme.getColor(Theme.key_dialogTextGray3))
            except Exception:
                pass
            container = LinearLayout(context)
            container.setOrientation(LinearLayout.VERTICAL)
            padding = AndroidUtilities.dp(24)
            container.setPadding(padding, AndroidUtilities.dp(8), padding, AndroidUtilities.dp(8))
            container.addView(input_field)
            builder = AlertDialogBuilder(context)
            builder.set_title("Добавить чат")
            builder.set_message("Введите ID чата")
            builder.set_view(container)
            def on_add(d, w):
                try:
                    text = input_field.getText().toString().strip()
                    dialog_id = int(text)
                    title = self._get_chat_title_by_id(dialog_id)
                    self._on_chat_selected_for_lock({"id": dialog_id, "title": title})
                except ValueError:
                    self._show_toast("✗ Некорректный ID")
                d.dismiss()
            builder.set_positive_button("Добавить", on_add)
            builder.set_negative_button("Отмена", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка fallback: {e}")
    def _on_chat_selected_for_lock(self, chat_info):
        try:
            dialog_id = chat_info.get("id")
            title = chat_info.get("title", str(dialog_id))
            chats = self._get_locked_chats()
            for c in chats:
                if c.get("id") == dialog_id:
                    self._show_toast(f"«{title}» уже заблокирован")
                    return
            chats.append({"id": dialog_id, "title": title})
            self._save_locked_chats(chats)
            self._show_toast(f"«{title}» заблокирован")
            run_on_ui_thread(lambda: self._refresh_settings(), delay=300)
        except Exception as e:
            log(f"[ChatLock] Ошибка добавления чата: {e}")
    def _add_folder_to_lock(self):
        try:
            password = self._get_password()
            if not password:
                self._show_toast("Сначала установите пароль!")
                return
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            mc = get_messages_controller()
            filters = mc.getDialogFilters()
            if not filters or filters.size() == 0:
                self._show_toast("Нет папок")
                return
            locked_folders = self._get_locked_folders()
            locked_ids = {f.get("id") for f in locked_folders}
            folder_names = []
            folder_data = []
            for i in range(filters.size()):
                df = filters.get(i)
                if df.id == 0:
                    continue
                name = df.name if df.name else f"Папка {df.id}"
                status = " [locked]" if df.id in locked_ids else ""
                folder_names.append(f"{name}{status}")
                folder_data.append({"id": df.id, "name": name})
            if not folder_names:
                self._show_toast("Нет папок для блокировки")
                return
            builder = AlertDialogBuilder(context)
            builder.set_title("Выберите папку")
            def on_select(d, index):
                if 0 <= index < len(folder_data):
                    fd = folder_data[index]
                    if fd["id"] in locked_ids:
                        self._show_toast(f"«{fd['name']}» уже заблокирована")
                    else:
                        folders = self._get_locked_folders()
                        folders.append(fd)
                        self._save_locked_folders(folders)
                        self._show_toast(f"Папка «{fd['name']}» заблокирована")
                        run_on_ui_thread(lambda: self._refresh_settings(), delay=300)
                d.dismiss()
            builder.set_items(folder_names, on_select)
            builder.set_negative_button("Отмена", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка добавления папки: {e}")
    def _show_locked_chats_list(self):
        try:
            chats = self._get_locked_chats()
            if not chats:
                self._show_toast("Нет заблокированных чатов")
                return
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            items = [f"{c.get('title', '?')}" for c in chats]
            builder = AlertDialogBuilder(context)
            builder.set_title("Заблокированные чаты")
            def on_select(d, index):
                if 0 <= index < len(chats):
                    self._confirm_unlock_chat(chats[index], d)
            builder.set_items(items, on_select)
            builder.set_negative_button("Закрыть", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка списка: {e}")
    def _confirm_unlock_chat(self, chat_info, parent_dialog):
        try:
            parent_dialog.dismiss()
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            title = chat_info.get("title", "?")
            builder = AlertDialogBuilder(context)
            builder.set_title("Разблокировать?")
            builder.set_message(f"Убрать блокировку с «{title}»?")
            def on_confirm(d, w):
                dialog_id = chat_info.get("id")
                chats = self._get_locked_chats()
                chats = [c for c in chats if c.get("id") != dialog_id]
                self._save_locked_chats(chats)
                self._unlocked_sessions.discard(dialog_id)
                self._show_toast(f"«{title}» разблокирован")
                self._refresh_settings()
                d.dismiss()
            builder.set_positive_button("Разблокировать", on_confirm)
            builder.set_negative_button("Отмена", None)
            builder.make_button_red(AlertDialogBuilder.BUTTON_POSITIVE)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка подтверждения: {e}")
    def _show_locked_folders_list(self):
        try:
            folders = self._get_locked_folders()
            if not folders:
                self._show_toast("Нет заблокированных папок")
                return
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            items = [f"{f.get('name', '?')}" for f in folders]
            builder = AlertDialogBuilder(context)
            builder.set_title("Заблокированные папки")
            def on_select(d, index):
                if 0 <= index < len(folders):
                    self._confirm_unlock_folder(folders[index], d)
            builder.set_items(items, on_select)
            builder.set_negative_button("Закрыть", None)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка списка папок: {e}")
    def _confirm_unlock_folder(self, folder_info, parent_dialog):
        try:
            parent_dialog.dismiss()
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            name = folder_info.get("name", "?")
            builder = AlertDialogBuilder(context)
            builder.set_title("Разблокировать?")
            builder.set_message(f"Убрать блокировку с папки «{name}»?")
            def on_confirm(d, w):
                fid = folder_info.get("id")
                folders = self._get_locked_folders()
                folders = [f for f in folders if f.get("id") != fid]
                self._save_locked_folders(folders)
                self._unlocked_sessions.discard(fid)
                self._show_toast(f"Папка «{name}» разблокирована")
                self._refresh_settings()
                d.dismiss()
            builder.set_positive_button("Разблокировать", on_confirm)
            builder.set_negative_button("Отмена", None)
            builder.make_button_red(AlertDialogBuilder.BUTTON_POSITIVE)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка подтверждения папки: {e}")
    def _clear_all_locks(self):
        try:
            chats = self._get_locked_chats()
            folders = self._get_locked_folders()
            total = len(chats) + len(folders)
            if total == 0:
                self._show_toast("Список уже пуст")
                return
            fragment = get_last_fragment()
            if not fragment:
                return
            context = fragment.getParentActivity()
            if not context:
                return
            builder = AlertDialogBuilder(context)
            builder.set_title("Очистить всё?")
            builder.set_message(f"Будет разблокировано: {len(chats)} чат(ов), {len(folders)} папок.")
            def on_confirm(d, w):
                self._save_locked_chats([])
                self._save_locked_folders([])
                self._unlocked_sessions.clear()
                self._show_toast("Все блокировки удалены")
                self._refresh_settings()
                d.dismiss()
            builder.set_positive_button("Очистить", on_confirm)
            builder.set_negative_button("Отмена", None)
            builder.make_button_red(AlertDialogBuilder.BUTTON_POSITIVE)
            builder.show()
        except Exception as e:
            log(f"[ChatLock] Ошибка очистки: {e}")
    def _show_toast(self, text):
        def t():
            try:
                from android.widget import Toast
                frag = get_last_fragment()
                if frag and frag.getParentActivity():
                    Toast.makeText(frag.getParentActivity(), text, Toast.LENGTH_SHORT).show()
            except Exception:
                pass
        run_on_ui_thread(t)
    class _ChatLifecycleHook(MethodHook):
        def __init__(self, plugin, hook_type):
            super().__init__()
            self.plugin_ref = weakref.ref(plugin)
            self.hook_type = hook_type
        def after_hooked_method(self, param):
            if self.hook_type != "resume":
                return
            plugin = self.plugin_ref()
            if not plugin:
                return
            fragment = param.thisObject
            if isinstance(fragment, ChatActivity):
                run_on_ui_thread(lambda f=fragment: plugin._update_lock_menu(f), delay=100)
        def before_hooked_method(self, param):
            plugin = self.plugin_ref()
            if not plugin:
                return
            fragment = param.thisObject
            if not isinstance(fragment, ChatActivity):
                return
            if self.hook_type == "pause":
                try:
                    dialog_id = int(fragment.getDialogId())
                    if not plugin._is_dialog_in_lock_config(dialog_id):
                        return
                    lock_on_exit = plugin.get_setting("lock_on_exit", True)
                    if lock_on_exit:
                        plugin._unlocked_sessions.discard(dialog_id)
                    if not plugin._is_authenticating:
                        plugin._cleanup_overlays()
                except Exception:
                    pass
                return
            if self.hook_type != "resume":
                return
            try:
                dialog_id = int(fragment.getDialogId())
            except Exception:
                return
            if not plugin._is_dialog_locked(dialog_id):
                return
            password = plugin._get_password()
            if not password:
                return
            plugin._protected_fragment_ref = weakref.ref(fragment)
            if plugin._is_authenticating:
                return
            plugin._authenticating_dialog_id = dialog_id
            plugin._is_authenticating = True
            try:
                plugin._apply_blur_effect(fragment.getParentActivity())
            except Exception:
                pass
            plugin._add_scrim(fragment)
            def on_success():
                plugin._is_authenticating = False
                plugin._cleanup_overlays()
                plugin._show_toast("✓ Доступ разрешён")
            def on_failure():
                plugin._block_fragment_input(fragment)
                plugin._is_authenticating = False
                try:
                    fragment.dismissCurrentDialog()
                except Exception: pass
                fragment.finishFragment(False)
                try:
                    fragment.removeSelfFromStack()
                except Exception: pass
                plugin._cleanup_overlays()
            if plugin.get_setting("use_biometrics", False):
                plugin._show_biometric_prompt(fragment, on_success, on_failure)
            else:
                plugin._show_password_prompt(fragment, on_success, on_failure)
    def _hook_dialogs_lifecycle(self):
        try:
            if not self._dialogs_resume_hook:
                m_resume = DialogsActivity.getClass().getDeclaredMethod("onResume")
                self._dialogs_resume_hook = self.hook_method(m_resume, self._DialogsLifecycleHook(self, "resume"))
                m_pause = DialogsActivity.getClass().getDeclaredMethod("onPause")
                self._dialogs_pause_hook = self.hook_method(m_pause, self._DialogsLifecycleHook(self, "pause"))
        except Exception as e:
            log(f"[ChatLock] Ошибка хука DialogsActivity: {e}")
    def _unhook_dialogs_lifecycle(self):
        if self._dialogs_resume_hook:
            self.unhook_method(self._dialogs_resume_hook)
            self._dialogs_resume_hook = None
        if self._dialogs_pause_hook:
            self.unhook_method(self._dialogs_pause_hook)
            self._dialogs_pause_hook = None
    class _DialogsLifecycleHook(MethodHook):
        def __init__(self, plugin, hook_type):
            super().__init__()
            self.plugin_ref = weakref.ref(plugin)
            self.hook_type = hook_type
        def before_hooked_method(self, param):
            plugin = self.plugin_ref()
            if not plugin: return
            fragment = param.thisObject
            if not isinstance(fragment, DialogsActivity): return
            folder_id = fragment.getArguments().getInt("folderId", 0)
            if folder_id != 1: return
            if self.hook_type == "pause":
                if not plugin._is_authenticating:
                    plugin._cleanup_overlays()
                    if plugin.get_setting("lock_on_exit", True):
                        try:
                            last = get_last_fragment()
                            if last and isinstance(last, ChatActivity):
                                return
                        except: pass
                        plugin._unlocked_sessions.discard(1000001)
                return
            if self.hook_type != "resume": return
            if not plugin.get_setting("archive_locked", False): return
            if 1000001 in plugin._unlocked_sessions: return
            if not plugin._get_password(): return
            if plugin._is_authenticating: return
            plugin._is_authenticating = True
            plugin._authenticating_dialog_id = 1000001
            try:
                plugin._apply_blur_effect(fragment.getParentActivity())
            except Exception: pass
            plugin._add_scrim(fragment)
            def on_success():
                plugin._unlocked_sessions.add(1000001)
                plugin._is_authenticating = False
                plugin._cleanup_overlays()
                plugin._show_toast("✓ Архивы открыты")
            def on_failure():
                plugin._block_fragment_input(fragment)
                plugin._is_authenticating = False
                try: fragment.dismissCurrentDialog()
                except Exception: pass
                fragment.finishFragment(False)
                try: fragment.removeSelfFromStack()
                except Exception: pass
                plugin._cleanup_overlays()
            if plugin.get_setting("use_biometrics", False):
                plugin._show_biometric_prompt(fragment, on_success, on_failure)
            else:
                plugin._show_password_prompt(fragment, on_success, on_failure)
    def _hook_archive_context_menu(self):
        try:
            m = DialogsActivity.getClass().getDeclaredMethod("onArchiveLongPress", View)
            self._archive_menu_hook = self.hook_method(m, self._ArchiveMenuHook(self))
        except Exception as e:
            log(f"[ChatLock] Ошибка хука меню архива: {e}")
    def _unhook_archive_context_menu(self):
        if self._archive_menu_hook:
            self.unhook_method(self._archive_menu_hook)
            self._archive_menu_hook = None
    class _ArchiveMenuHook(MethodHook):
        def __init__(self, plugin):
            super().__init__()
            self.plugin_ref = weakref.ref(plugin)
        def before_hooked_method(self, param):
            plugin = self.plugin_ref()
            if not plugin: return
            fragment = param.thisObject
            parent = fragment.getParentActivity()
            if not parent: return
            param.setResult(None) # Cancel original menu
            try:
                from android.view import HapticFeedbackConstants
                view = param.args[0]
                view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS, HapticFeedbackConstants.FLAG_IGNORE_GLOBAL_SETTING)
            except Exception: pass
            from org.telegram.messenger import LocaleController, SharedConfig
            builder = BSBuilder(parent)
            has_unread = plugin._get_archive_unread_count() != 0
            is_locked = plugin.get_setting("archive_locked", False)
            items = []
            icons = []
            if has_unread:
                items.append(LocaleController.getString("MarkAllAsRead"))
                icons.append(plugin._get_resource_id("msg_markread"))
            if SharedConfig.archiveHidden:
                items.append(LocaleController.getString("PinInTheList"))
                icons.append(plugin._get_resource_id("chats_pin"))
            else:
                items.append(LocaleController.getString("HideAboveTheList"))
                icons.append(plugin._get_resource_id("chats_unpin"))
            def on_click(dialog, which):
                if has_unread and which == 0:
                    get_messages_controller().getMessagesStorage().readAllDialogs(1)
                else:
                    plugin._toggle_archive_hidden(fragment, view)
            builder.setItems(items, icons, on_click)
            plugin._show_bottom_sheet(builder.create())
    def _get_archive_unread_count(self):
        try:
            return get_messages_controller().getMessagesStorage().getArchiveUnreadCount()
        except Exception: return 0
    def _get_resource_id(self, name):
        try:
            from org.telegram.messenger import ApplicationLoader
            return ApplicationLoader.applicationContext.getResources().getIdentifier(name, "drawable", ApplicationLoader.applicationContext.getPackageName())
        except Exception: return 0
    def _toggle_archive_hidden(self, fragment, view):
        try:
            view_pages = getattr(fragment, "viewPages", None)
            if view_pages:
                for vp in view_pages:
                    if vp.dialogsType == 0:
                        archive_cell = self._find_archive_cell(vp)
                        vp.listView.toggleArchiveHidden(True, archive_cell)
        except Exception: pass
    def _find_archive_cell(self, page):
        try:
            lv = page.listView
            for i in range(lv.getChildCount()):
                c = lv.getChildAt(i)
                if hasattr(c, "isFolderCell") and c.isFolderCell():
                    return c
        except Exception: return None
    def _show_bottom_sheet(self, sheet):
        try:
            from client_utils import get_last_fragment
            f = get_last_fragment()
            if f: f.showDialog(sheet)
        except Exception: pass
