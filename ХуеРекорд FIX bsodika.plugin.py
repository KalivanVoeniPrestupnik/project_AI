import os
from datetime import datetime
from java import jclass, dynamic_proxy, jint, jarray
from java.lang import String, System, Integer
from android.view import View, MotionEvent, Gravity, WindowManager
from android.widget import ImageView, TextView, LinearLayout
from android.graphics import PixelFormat, PorterDuff
from android.graphics.drawable import GradientDrawable
from android.media import MediaRecorder
from android.content import Context, Intent
from android.app import Notification, NotificationChannel
from android.os import Build, Environment, Handler, Looper
from android.animation import LayoutTransition
from android_utils import log, run_on_ui_thread
from base_plugin import BasePlugin, MethodHook, MenuItemData, MenuItemType
from ui.bulletin import BulletinHelper
from ui.alert import AlertDialogBuilder
from ui.settings import Header, Selector, Divider, Switch, Text
from client_utils import get_last_fragment, get_user_config

__id__ = "screen_rec"
__name__ = "Запись экрана"
__version__ = "3.4"
__author__ = "@swagnonher"
__description__ = " Fix  "
__min_version__ = "11.0.0"

REQUEST_CODE_SCREEN_CAPTURE = 5566
ACTION_RECORDER_START = "com.exteragram.action.RECORDER_START"
EXTRA_RESULT_CODE = "exrec_result_code"
EXTRA_DATA_INTENT = "exrec_data_intent"
NOTIF_ID = 5566
CHANNEL_ID = "extera_recorder_channel"

AndroidUtilities = jclass("org.telegram.messenger.AndroidUtilities")
LayoutHelper = jclass("org.telegram.ui.Components.LayoutHelper")
MediaScannerConnection = jclass("android.media.MediaScannerConnection")
Theme = jclass("org.telegram.ui.ActionBar.Theme")

def to_jcolor(color):
    if color > 0x7FFFFFFF: return color - 0x100000000
    return color

def get_best_res_id(context, names, fallback=0):
    res = context.getResources()
    pkg = context.getPackageName()
    for name in names:
        id = res.getIdentifier(name, "drawable", pkg)
        if id != 0: return id
    return fallback

def find_method_smart(clazz, name, *params):
    try: return clazz.getDeclaredMethod(name, *params)
    except:
        for m in clazz.getDeclaredMethods():
            p_types = m.getParameterTypes()
            if len(p_types) == len(params):
                match = True
                for i in range(len(params)):
                    if p_types[i] != params[i]:
                        match = False
                        break
                if match: return m
    return None

def _find_existing_callback_from_service(service, callback_base, log_fn=None):
    def _l(msg):
        if log_fn:
            try: log_fn(msg)
            except: pass

    if service is None:
        return None

    try:
        q = [(service, 0)]
        seen = set()
        max_depth = 3
        interesting = ("voip", "webrtc", "screen", "captur", "projection")

        while q:
            obj, depth = q.pop(0)
            if obj is None:
                continue

            oid = id(obj)
            if oid in seen:
                continue
            seen.add(oid)

            try:
                if callback_base.isInstance(obj):
                    _l(f"Callback найден в графе объектов: {obj.getClass().getName()}")
                    return obj
            except:
                pass

            try:
                cls = obj.getClass()
            except:
                continue

            cur = cls
            while cur is not None:
                try:
                    fields = cur.getDeclaredFields()
                except:
                    fields = []

                for f in fields:
                    try:
                        f.setAccessible(True)
                        v = f.get(obj)
                        if v is None:
                            continue
                        if callback_base.isInstance(v):
                            _l(f"Callback найден в поле: {cur.getName()}.{f.getName()}")
                            return v
                        if depth < max_depth:
                            try:
                                n = v.getClass().getName().lower()
                                if any(k in n for k in interesting):
                                    q.append((v, depth + 1))
                            except:
                                pass
                    except:
                        pass

                try:
                    methods = cur.getDeclaredMethods()
                except:
                    methods = []
                for m in methods:
                    try:
                        if len(m.getParameterTypes()) != 0:
                            continue
                        rt = m.getReturnType()
                        if rt is None or not callback_base.isAssignableFrom(rt):
                            continue
                        m.setAccessible(True)
                        v = m.invoke(obj)
                        if v is not None and callback_base.isInstance(v):
                            _l(f"Callback найден через метод: {cur.getName()}.{m.getName()}()")
                            return v
                    except:
                        pass

                try:
                    cur = cur.getSuperclass()
                except:
                    cur = None
    except:
        pass

    return None

def _build_vcd_callback(callback_base, log_fn=None):
    def _l(msg):
        if log_fn:
            try: log_fn(msg)
            except: pass

    try:
        JClass = jclass("java.lang.Class")
        JArray = jclass("java.lang.reflect.Array")
        vcd_cls = JClass.forName("org.telegram.messenger.voip.VideoCapturerDevice")
        cb_cls = JClass.forName("org.telegram.messenger.voip.VideoCapturerDevice$1")

        outer = None
        try:
            f = vcd_cls.getDeclaredField("instance")
            f.setAccessible(True)
            arr = f.get(None)
            if arr is not None:
                ln = int(JArray.getLength(arr))
                for i in range(ln):
                    try:
                        v = JArray.get(arr, i)
                        if v is not None:
                            outer = v
                            break
                    except:
                        pass
        except:
            pass

        if outer is None:
            try:
                for c in vcd_cls.getDeclaredConstructors():
                    try:
                        c.setAccessible(True)
                        p = c.getParameterTypes()
                        if len(p) == 1 and p[0].isPrimitive() and p[0].getName() == "boolean":
                            outer = c.newInstance(False)
                            _l("Создан temporary VideoCapturerDevice(false) для callback")
                            break
                    except:
                        pass
            except:
                pass

        for cons in cb_cls.getDeclaredConstructors():
            try:
                cons.setAccessible(True)
                params = cons.getParameterTypes()
                if len(params) == 1 and params[0] == vcd_cls:
                    cb = cons.newInstance(outer)
                    if cb is not None:
                        _l("Callback создан через VideoCapturerDevice$1")
                        return cb
            except:
                pass
    except Exception as e:
        _l(f"VCD callback build failed: {e}")
    return None

def get_stub_callback(log_fn=None, service=None):
    def _l(msg):
        if log_fn:
            try: log_fn(msg)
            except: pass

    try:
        CallbackBase = jclass("android.media.projection.MediaProjection$Callback")
        cb = _find_existing_callback_from_service(service, CallbackBase, _l)
        if cb is not None:
            return cb
        _l("Готовый MediaProjection callback в VoIPService не найден")
        cb = _build_vcd_callback(CallbackBase, _l)
        if cb is not None:
            return cb
        _l("Конструктор callback отключен (hard anti-crash mode)")
    except Exception as e:
        _l(f"Ошибка поиска callback: {e}")
    _l("Не удалось создать MediaProjection callback")
    return None

class RunnableImpl(dynamic_proxy(jclass("java.lang.Runnable"))):
    def __init__(self, func):
        super().__init__()
        self.func = func
    def run(self):
        try: self.func()
        except: pass

class FloatingPill:
    def __init__(self, activity, plugin):
        self.activity = activity
        self.plugin = plugin
        self.wm = activity.getSystemService(Context.WINDOW_SERVICE)
        self.handler = Handler(Looper.getMainLooper())
        self.is_expanded = False
        self.is_recording = False
        self.start_time = 0
        self.timer_runnable = RunnableImpl(self._update_timer)
        
        self.icon_pause = get_best_res_id(activity, ["ic_player_pause", "msg_pause"], 17301539)
        self.icon_play = get_best_res_id(activity, ["ic_player_play", "msg_play"], 17301540)
        self.icon_stop = get_best_res_id(activity, ["ic_ab_done", "msg_check"], 17301560)
        
        self._init_layout()

    def _init_layout(self):
        self.lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        )
        self.lp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL
        self.lp.y = AndroidUtilities.dp(60)

        self.root_view = LinearLayout(self.activity)
        self.root_view.setOrientation(0)
        self.root_view.setGravity(Gravity.CENTER_VERTICAL)
        
        shape = GradientDrawable()
        shape.setShape(GradientDrawable.RECTANGLE)
        shape.setColor(to_jcolor(0xCC1C1C1E))
        shape.setCornerRadius(float(AndroidUtilities.dp(20)))
        self.root_view.setBackground(shape)
        self.root_view.setPadding(AndroidUtilities.dp(12), AndroidUtilities.dp(8), AndroidUtilities.dp(12), AndroidUtilities.dp(8))
        if Build.VERSION.SDK_INT >= 21: self.root_view.setElevation(float(AndroidUtilities.dp(8)))
        self.root_view.setLayoutTransition(LayoutTransition())

        self.dot = ImageView(self.activity)
        dot_draw = GradientDrawable()
        dot_draw.setShape(GradientDrawable.OVAL)
        dot_draw.setColor(to_jcolor(0xFFF44336))
        dot_draw.setSize(AndroidUtilities.dp(10), AndroidUtilities.dp(10))
        self.dot.setImageDrawable(dot_draw)
        self.root_view.addView(self.dot, LayoutHelper.createLinear(10, 10, Gravity.CENTER_VERTICAL, 0, 0, 8, 0))

        self.timer_text = TextView(self.activity)
        self.timer_text.setText("REC")
        self.timer_text.setTextSize(1, 14.0)
        self.timer_text.setTextColor(to_jcolor(0xFFFFFFFF))
        self.timer_text.setTypeface(AndroidUtilities.bold())
        self.root_view.addView(self.timer_text, LayoutHelper.createLinear(-2, -2, Gravity.CENTER_VERTICAL))

        self.sep = View(self.activity)
        self.sep.setBackgroundColor(to_jcolor(0x40FFFFFF))
        self.sep.setVisibility(View.GONE)
        self.root_view.addView(self.sep, LayoutHelper.createLinear(1, 16, Gravity.CENTER_VERTICAL, 12, 0, 12, 0))

        self.ctrls = LinearLayout(self.activity)
        self.ctrls.setOrientation(0)
        self.ctrls.setVisibility(View.GONE)
        
        self.btn_pause = ImageView(self.activity)
        self.btn_pause.setImageResource(self.icon_pause)
        self.btn_pause.setColorFilter(to_jcolor(0xFFFFFFFF), PorterDuff.Mode.SRC_IN)
        self.btn_pause.setOnClickListener(self._click(self._on_pause))
        self.ctrls.addView(self.btn_pause, LayoutHelper.createLinear(24, 24, Gravity.CENTER_VERTICAL, 0, 0, 16, 0))
        
        self.btn_stop = ImageView(self.activity)
        self.btn_stop.setImageResource(self.icon_stop)
        self.btn_stop.setColorFilter(to_jcolor(0xFFF44336), PorterDuff.Mode.SRC_IN)
        self.btn_stop.setOnClickListener(self._click(self._on_stop))
        self.ctrls.addView(self.btn_stop, LayoutHelper.createLinear(24, 24, Gravity.CENTER_VERTICAL))

        self.root_view.addView(self.ctrls, LayoutHelper.createLinear(-2, -2, Gravity.CENTER_VERTICAL))
        self._setup_drag(self.root_view)
        self.root_view.setOnClickListener(self._click(self._on_click))

    def _click(self, f):
        class C(dynamic_proxy(jclass("android.view.View$OnClickListener"))):
            def onClick(self, v): f(v)
        return C()

    def _setup_drag(self, view):
        class T(dynamic_proxy(jclass("android.view.View$OnTouchListener"))):
            def __init__(self, c):
                super().__init__()
                self.c = c
                self.sx, self.sy, self.tx, self.ty, self.drg = 0, 0, 0, 0, False
            def onTouch(self, v, e):
                a = e.getAction()
                if a == MotionEvent.ACTION_DOWN:
                    self.sx, self.sy, self.tx, self.ty, self.drg = self.c.lp.x, self.c.lp.y, e.getRawX(), e.getRawY(), False
                    return True
                elif a == MotionEvent.ACTION_MOVE:
                    dx, dy = e.getRawX() - self.tx, e.getRawY() - self.ty
                    if abs(dx) > 10 or abs(dy) > 10: self.drg = True
                    if self.drg:
                        self.c.lp.x, self.c.lp.y = int(self.sx + dx), int(self.sy + dy)
                        self.c.wm.updateViewLayout(self.c.root_view, self.c.lp)
                    return True
                elif a == MotionEvent.ACTION_UP:
                    if not self.drg: v.performClick()
                    return True
                return False
        view.setOnTouchListener(T(self))

    def show(self):
        try: self.wm.addView(self.root_view, self.lp)
        except: pass

    def hide(self):
        try: self.wm.removeView(self.root_view)
        except: pass

    def _on_click(self, v):
        if not self.is_recording: self.plugin.request_permission()
        else:
            self.is_expanded = not self.is_expanded
            viz = View.VISIBLE if self.is_expanded else View.GONE
            self.sep.setVisibility(viz); self.ctrls.setVisibility(viz)

    def _on_pause(self, v):
        self.plugin.toggle_pause()
        self.btn_pause.setImageResource(self.icon_play if self.plugin.is_paused else self.icon_pause)

    def _on_stop(self, v): self.plugin.stop_recording()

    def set_rec_state(self, active):
        self.is_recording = active
        if active:
            self.start_time = System.currentTimeMillis()
            self.handler.postDelayed(self.timer_runnable, 1000)
        else:
            self.is_expanded = False
            self.sep.setVisibility(View.GONE); self.ctrls.setVisibility(View.GONE)
            self.handler.removeCallbacks(self.timer_runnable)
            self.timer_text.setText("REC")

    def _update_timer(self):
        if not self.is_recording: return
        if not self.plugin.is_paused:
            diff = (System.currentTimeMillis() - self.start_time) // 1000
            self.timer_text.setText(f"{diff//60:02d}:{diff%60:02d}")
        else: self.start_time += 1000
        self.handler.postDelayed(self.timer_runnable, 1000)

class ActivityResultHook(MethodHook):
    def __init__(self, plugin): self.plugin = plugin
    def after_hooked_method(self, param):
        if param.args[0] != REQUEST_CODE_SCREEN_CAPTURE:
            return
        if self.plugin.activity_hook:
            self.plugin.unhook_method(self.plugin.activity_hook)
            self.plugin.activity_hook = None
        if param.args[1] == -1:
            self.plugin._append_log("Разрешение на захват экрана выдано")
            self.plugin.start_service_hijack(param.args[1], param.args[2])
        else:
            self.plugin._append_log(f"Разрешение отклонено, resultCode={param.args[1]}")

class VoIPStartHook(MethodHook):
    def __init__(self, plugin): self.plugin = plugin
    def before_hooked_method(self, param):
        intent = param.args[0]
        if intent and intent.getAction() == ACTION_RECORDER_START:
            try:
                self.plugin.on_service_started(param.thisObject, intent)
            except Exception as e:
                self.plugin._append_log(f"VoIP hook error: {e}")
            # Always consume our synthetic action to avoid VoIPService crash path.
            param.setResult(Integer(2))

class ScreenRecorderPlugin(BasePlugin):
    def on_plugin_load(self):
        self.ui, self.service_ref, self.recorder, self.proj_cb = None, None, None, None
        self.activity_hook = None
        self._logs = []
        self.is_paused = False
        self.save_dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS).getAbsolutePath()
        self._append_log("Плагин загружен")

        try:
            JClass = jclass("java.lang.Class")
            VS = JClass.forName("org.telegram.messenger.voip.VoIPService")
            IT = Integer.TYPE
            
            m_start = find_method_smart(VS, "onStartCommand", Intent, IT, IT)

            if m_start: self.hook_method(m_start, VoIPStartHook(self))
            else:
                for m in VS.getDeclaredMethods():
                    try:
                        if m.getName() != "onStartCommand": continue
                        p = m.getParameterTypes()
                        if len(p) >= 1 and p[0] == Intent:
                            self.hook_method(m, VoIPStartHook(self))
                    except: pass
        except Exception as e: self._append_log(f"Universal Hook Error: {e}")

        self.add_menu_item(MenuItemData(
            menu_type=MenuItemType.DRAWER_MENU,
            text="Запись экрана", icon="msg_media_video",
            on_click=self._toggle_floating_btn
        ))

        if self.get_setting("btn_enabled", True): run_on_ui_thread(self._init_ui)

    def on_plugin_unload(self):
        if self.activity_hook:
            self.unhook_method(self.activity_hook)
            self.activity_hook = None
        if self.ui: run_on_ui_thread(self.ui.hide)
        self.stop_recording()

    def create_settings(self):
        return [
            Header(text="Качество"),
            Selector(key="fps", text="FPS", items=["30 FPS", "60 FPS", "120 FPS"], default=0),
            Selector(key="res_scale", text="Разрешение", items=["50%", "75%", "100%"], default=2),
            Selector(key="bitrate", text="Битрейт", items=["Низкий", "Средний", "Высокий"], default=1),
            Divider(),
            Header(text="Аудио"),
            Selector(key="audio_mode", text="Источник", items=["Без звука", "Микрофон", "Динамик"], default=1),
            Divider(),
            Switch(key="btn_enabled", text="Кнопка при запуске", default=True),
            Text(text="Логи", icon="msg_info", on_click=self._open_logs),
        ]

    def _append_log(self, message):
        try:
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
            self._logs.append(line)
            if len(self._logs) > 200:
                self._logs = self._logs[-200:]
            log(f"[screen_rec] {message}")
        except:
            try: log(f"[screen_rec] {message}")
            except: pass

    def _open_logs(self, view=None):
        frag = get_last_fragment()
        act = self.ui.activity if self.ui and hasattr(self.ui, "activity") else (frag.getParentActivity() if frag else None)
        if not act:
            BulletinHelper.show_error("Нет активного окна")
            return

        logs_text = "\n".join(self._logs[-120:]) if self._logs else "Логов пока нет."

        def on_copy(dlg, w):
            try: dlg.dismiss()
            except: pass
            try:
                ClipData = jclass("android.content.ClipData")
                app = jclass("org.telegram.messenger.ApplicationLoader").applicationContext
                app.getSystemService(Context.CLIPBOARD_SERVICE).setPrimaryClip(
                    ClipData.newPlainText("screen_rec_logs", logs_text)
                )
                BulletinHelper.show_copied_to_clipboard("Скопировано")
            except Exception as e:
                self._append_log(f"Copy logs error: {e}")
                BulletinHelper.show_error("Не удалось скопировать логи")

        AlertDialogBuilder(act) \
            .set_title("Логи") \
            .set_message(logs_text) \
            .set_positive_button("Копировать", on_copy) \
            .set_negative_button("Закрыть", lambda d, w: d.dismiss()) \
            .show()

    def _toggle_floating_btn(self, ctx):
        if self.ui:
            run_on_ui_thread(self.ui.hide); self.ui = None
            self.set_setting("btn_enabled", False)
            BulletinHelper.show_info("Кнопка скрыта")
        else:
            self._init_ui(); self.set_setting("btn_enabled", True)
            BulletinHelper.show_success("Кнопка включена")

    def _init_ui(self):
        frag = get_last_fragment()
        if frag and frag.getParentActivity():
            self.ui = FloatingPill(frag.getParentActivity(), self)
            self.ui.show()

    def _resolve_account(self):
        try:
            frag = get_last_fragment()
            if frag and hasattr(frag, "getCurrentAccount"):
                return int(frag.getCurrentAccount())
        except:
            pass
        try:
            uc = get_user_config()
            if uc:
                if hasattr(uc, "selectedAccount"):
                    return int(uc.selectedAccount)
                if hasattr(uc, "getSelectedAccount"):
                    return int(uc.getSelectedAccount())
        except:
            pass
        return 0

    def request_permission(self):
        act = self.ui.activity
        self._append_log("Запрошено разрешение на захват экрана")
        if self.activity_hook:
            self.unhook_method(self.activity_hook)
            self.activity_hook = None
        try:
            m = act.getClass().getDeclaredMethod("onActivityResult", Integer.TYPE, Integer.TYPE, Intent)
            self.activity_hook = self.hook_method(m, ActivityResultHook(self))
        except:
            for m in act.getClass().getDeclaredMethods():
                try:
                    if m.getName() != "onActivityResult": continue
                    p = m.getParameterTypes()
                    if len(p) >= 3 and p[0] == Integer.TYPE and p[1] == Integer.TYPE:
                        self.activity_hook = self.hook_method(m, ActivityResultHook(self))
                        break
                except: pass
        mgr = act.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
        act.startActivityForResult(mgr.createScreenCaptureIntent(), REQUEST_CODE_SCREEN_CAPTURE)

    def start_service_hijack(self, res_code, data):
        self.saved_res, self.saved_data = res_code, data
        self._append_log("Запуск VoIPService для инициализации записи")
        act = self.ui.activity
        intent = Intent(act, jclass("org.telegram.messenger.voip.VoIPService"))
        intent.setAction(ACTION_RECORDER_START)
        account = self._resolve_account()
        self._append_log(f"Используется account={account}")
        intent.putExtra("account", jint(account))
        intent.putExtra(EXTRA_RESULT_CODE, jint(res_code))
        try:
            intent.putExtra(EXTRA_DATA_INTENT, data)
        except Exception as e:
            self._append_log(f"Intent extra DATA set failed: {e}")
        if Build.VERSION.SDK_INT >= 26: act.startForegroundService(intent)
        else: act.startService(intent)

    def on_service_started(self, service, start_intent=None):
        self.service_ref = service
        try:
            self._start_foreground(service)
            res_code = self.saved_res
            data_intent = self.saved_data
            try:
                if start_intent is not None:
                    res_code = start_intent.getIntExtra(EXTRA_RESULT_CODE, res_code)
                    d = start_intent.getParcelableExtra(EXTRA_DATA_INTENT)
                    if d is not None:
                        data_intent = d
            except Exception as e:
                self._append_log(f"Read service extras failed: {e}")

            if data_intent is None:
                raise RuntimeError("DATA_INTENT is null")

            mgr = service.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
            self.projection = mgr.getMediaProjection(res_code, data_intent)

            try:
                self.proj_cb = get_stub_callback(self._append_log, service)
                if self.proj_cb:
                    self.projection.registerCallback(self.proj_cb, Handler(Looper.getMainLooper()))
                    self._append_log("MediaProjection callback зарегистрирован")
                else:
                    raise RuntimeError("Не удалось создать callback для MediaProjection")
            except Exception as e:
                self._append_log(f"Stub Error: {e}")
                raise
            
            self._init_recorder(service)
            run_on_ui_thread(lambda: self.ui.set_rec_state(True))
            self._append_log("Запись успешно запущена")
            BulletinHelper.show_success("Запись запущена")
            return True
        except Exception as e:
            self._append_log(f"Rec Error: {e}")
            try:
                self.stop_recording()
            except:
                pass
            return False

    def _start_foreground(self, s):
        fg_type = 32
        try:
            ServiceInfo = jclass("android.content.pm.ServiceInfo")
            fg_type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
        except:
            pass
        notif = None
        if Build.VERSION.SDK_INT >= 26:
            nm = s.getSystemService(Context.NOTIFICATION_SERVICE)
            chan = NotificationChannel(CHANNEL_ID, "Recorder", 2)
            nm.createNotificationChannel(chan)
            b = Notification.Builder(s, CHANNEL_ID)
        else: b = Notification.Builder(s)
        b.setContentTitle("Запись экрана").setSmallIcon(get_best_res_id(s, ["ic_ab_other"], 17301522))
        notif = b.build()
        if Build.VERSION.SDK_INT >= 29:
            try:
                s.startForeground(NOTIF_ID, notif, fg_type)
                self._append_log(f"Foreground started with type={fg_type}")
                return
            except Exception as e:
                self._append_log(f"startForeground(type) failed: {e}")
                try:
                    Service = jclass("android.app.Service")
                    m = Service.getMethod("startForeground", Integer.TYPE, Notification, Integer.TYPE)
                    m.invoke(s, NOTIF_ID, notif, fg_type)
                    self._append_log(f"Foreground started via reflection type={fg_type}")
                    return
                except Exception as e2:
                    self._append_log(f"startForeground reflection failed: {e2}")
                    raise
        s.startForeground(NOTIF_ID, notif)

    def _init_recorder(self, ctx):
        wm = ctx.getSystemService(Context.WINDOW_SERVICE)
        disp = wm.getDefaultDisplay()
        point = jclass("android.graphics.Point")()
        disp.getRealSize(point)
        
        scale = [0.5, 0.75, 1.0][self.get_setting("res_scale", 2)]
        w, h = (int(point.x * scale) // 16) * 16, (int(point.y * scale) // 16) * 16
        
        self.recorder = MediaRecorder()

        audio_mode = int(self.get_setting("audio_mode", 1))
        if audio_mode < 0 or audio_mode > 2:
            audio_mode = 1

        audio_label = "Без звука"
        if audio_mode == 1:
            try:
                self.recorder.setAudioSource(1)  # MIC
                audio_label = "Микрофон"
            except Exception as e:
                audio_mode = 0
                self._append_log(f"MIC недоступен, запись будет без звука: {e}")
        elif audio_mode == 2:
            try:
                self.recorder.setAudioSource(8)  # REMOTE_SUBMIX / speaker capture
                audio_label = "Динамик"
            except Exception as e:
                self._append_log(f"Динамик недоступен, пробую микрофон: {e}")
                try:
                    self.recorder.setAudioSource(1)
                    audio_mode = 1
                    audio_label = "Микрофон (fallback)"
                except Exception as e2:
                    audio_mode = 0
                    self._append_log(f"Fallback MIC недоступен, запись без звука: {e2}")

        self.recorder.setVideoSource(2)
        self.recorder.setOutputFormat(2)
        
        fps_idx = int(self.get_setting("fps", 0))
        if fps_idx < 0 or fps_idx > 2:
            fps_idx = 0
        fps = [30, 60, 120][fps_idx]

        br_idx = int(self.get_setting("bitrate", 1))
        if br_idx < 0 or br_idx > 2:
            br_idx = 1
        br_base = w * h * [3, 6, 12][br_idx]
        br = int(br_base * (fps / 30.0))
        if br < 1500000:
            br = 1500000
        if br > 80000000:
            br = 80000000
        
        self.recorder.setVideoEncoder(2) 
        if audio_mode != 0:
            self.recorder.setAudioEncoder(3)
        self.recorder.setVideoEncodingBitRate(br)
        self.recorder.setVideoFrameRate(fps)
        self.recorder.setVideoSize(w, h)
        self._append_log(f"Параметры записи: {w}x{h} {fps}fps, bitrate={br}, audio={audio_label}")
        
        path = os.path.join(self.save_dir, f"Extera_Rec_{datetime.now().strftime('%H%M%S')}.mp4")
        self.current_path = path
        self._append_log(f"Выходной файл: {path}")
        self.recorder.setOutputFile(path)
        self.recorder.prepare()
        
        dm = ctx.getResources().getDisplayMetrics()
        self.vd = self.projection.createVirtualDisplay("ExRec", w, h, dm.densityDpi, 16, self.recorder.getSurface(), None, None)
        self.recorder.start()

    def toggle_pause(self):
        if not self.recorder or Build.VERSION.SDK_INT < 24: return
        if self.is_paused: self.recorder.resume(); self.is_paused = False
        else: self.recorder.pause(); self.is_paused = True

    def stop_recording(self):
        try:
            stop_ok = True
            if self.recorder:
                try: self.recorder.stop()
                except:
                    stop_ok = False
                self.recorder.release(); self.recorder = None
            if hasattr(self, 'vd') and self.vd: self.vd.release(); self.vd = None
            
            if hasattr(self, 'projection') and self.projection:
                if hasattr(self, 'proj_cb') and self.proj_cb:
                    try: self.projection.unregisterCallback(self.proj_cb)
                    except: pass
                    self.proj_cb = None
                self.projection.stop(); self.projection = None
            
            if self.service_ref: self.service_ref.stopForeground(True); self.service_ref.stopSelf(); self.service_ref = None
            
            if hasattr(self, 'current_path') and os.path.exists(self.current_path):
                size_ok = os.path.getsize(self.current_path) > 2048
                if stop_ok and size_ok:
                    self._append_log(f"Запись сохранена: {self.current_path}")
                    MediaScannerConnection.scanFile(self.ui.activity, jarray(String)([self.current_path]), None, None)
                    BulletinHelper.show_success("Запись в загрузках")
                else:
                    self._append_log("Запись повреждена или пуста, файл удалён")
                    try: os.remove(self.current_path)
                    except: pass
                    BulletinHelper.show_info("Не удалось корректно сохранить запись")
            if self.ui: run_on_ui_thread(lambda: self.ui.set_rec_state(False))
        except Exception as e: self._append_log(f"Stop Err: {e}")
