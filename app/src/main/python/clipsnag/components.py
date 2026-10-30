"""Android components, written in Python. Chaquopy's static proxy generator
turns each `static_proxy` class below into a real Java class with the same
name (e.g. clipsnag.components.MainActivity), which the manifest refers to."""

import json
import os
import shutil
import threading
import traceback
import uuid

from java import Override, cast, dynamic_proxy, jboolean, jclass, jint, jvoid, static_proxy

from android.app import Activity, AlertDialog, PendingIntent, Service, StatusBarManager
from android.content import BroadcastReceiver, ComponentName, Context, DialogInterface, Intent
from android.content.pm import ServiceInfo
from android.graphics.drawable import Icon
from android.os import Bundle, IBinder
from android.service.quicksettings import Tile, TileService
from android.view import View
from android.widget import Button, EditText, LinearLayout, ScrollView, TextView, Toast
from java.util.function import Consumer

from clipsnag import core, droid

AR = jclass("android.R")
FG_ID = 42
# StatusBarManager.TILE_ADD_REQUEST_RESULT_*
TILE_RESULTS = {0: "Pločica nije dodata", 1: "Pločica je već dodata", 2: "Pločica dodata ✓"}


# --------------------------------------------------------------- listener proxies

class ClickListener(dynamic_proxy(View.OnClickListener)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def onClick(self, view):
        try:
            self.fn()
        except Exception:
            traceback.print_exc()


class DialogClick(dynamic_proxy(DialogInterface.OnClickListener)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def onClick(self, dialog, which):
        try:
            self.fn(which)
        except Exception:
            traceback.print_exc()


class DialogCancel(dynamic_proxy(DialogInterface.OnCancelListener)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def onCancel(self, dialog):
        self.fn()


class DialogDismiss(dynamic_proxy(DialogInterface.OnDismissListener)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def onDismiss(self, dialog):
        self.fn()


class ResultConsumer(dynamic_proxy(Consumer)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def accept(self, value):
        try:
            self.fn(value)
        except Exception:
            traceback.print_exc()


def toast(ctx, text, long=False):
    Toast.makeText(ctx.getApplicationContext(), text,
                   Toast.LENGTH_LONG if long else Toast.LENGTH_SHORT).show()


def dialog_builder(activity):
    night = (activity.getResources().getConfiguration().uiMode & 0x30) == 0x20
    theme = (AR.style.Theme_DeviceDefault_Dialog_Alert if night
             else AR.style.Theme_DeviceDefault_Light_Dialog_Alert)
    return AlertDialog.Builder(activity, theme)


def open_picker(ctx, url, nid=0):
    i = droid.component_intent(ctx, "PickerActivity")
    i.putExtra("url", url)
    i.putExtra("nid", jint(nid))
    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    ctx.startActivity(i)


# ----------------------------------------------------------------------- main UI

class MainActivity(static_proxy(Activity)):

    @Override(jvoid, [Bundle])
    def onCreate(self, state):
        Activity.onCreate(self, state)
        droid.ensure_core(self)
        droid.ensure_channels(self)
        self.build_ui()
        if droid.sdk() >= 33 and \
                self.checkSelfPermission("android.permission.POST_NOTIFICATIONS") != 0:
            self.requestPermissions(["android.permission.POST_NOTIFICATIONS"], 1)
        threading.Thread(target=self.auto_update, daemon=True).start()
        self.maybe_selftest(self.getIntent())

    @Override(jvoid, [Intent])
    def onNewIntent(self, intent):
        Activity.onNewIntent(self, intent)
        self.setIntent(intent)
        self.maybe_selftest(intent)

    def maybe_selftest(self, intent):
        """CI hook (debug builds only): adb shell am start ... --es selftest_url URL"""
        debuggable = (self.getApplicationInfo().flags & 2) != 0  # FLAG_DEBUGGABLE
        url = intent.getStringExtra("selftest_url") if intent is not None else None
        if debuggable and url:
            threading.Thread(target=selftest, args=(self, url), daemon=True).start()

    def build_ui(self):
        dp = self.getResources().getDisplayMetrics().density
        pad = int(20 * dp)
        root = LinearLayout(self)
        root.setOrientation(LinearLayout.VERTICAL)
        root.setPadding(pad, pad, pad, pad)

        title = TextView(self)
        title.setText("ClipSnag")
        title.setTextSize(30.0)
        root.addView(title)

        how = TextView(self)
        how.setText(
            "Kako radi:\n"
            "1. Kopiraj link videa (Instagram, YouTube, TikTok, X, Facebook…)\n"
            "2. Povuci Quick Settings i dodirni pločicu „Skini video“\n"
            "3. Iskoči notifikacija → Skini → izaberi kvalitet\n\n"
            "Ili u bilo kojoj aplikaciji: Podeli → ClipSnag.\n"
            "Fajlovi idu u Download/ClipSnag.")
        how.setTextSize(15.0)
        how.setPadding(0, int(8 * dp), 0, int(16 * dp))
        root.addView(how)

        self.url_edit = EditText(self)
        self.url_edit.setHint("Nalepi link ovde")
        self.url_edit.setSingleLine(True)
        root.addView(self.url_edit)

        dl = Button(self)
        dl.setText("Izaberi kvalitet i skini")
        dl.setOnClickListener(ClickListener(self.on_download))
        root.addView(dl)

        if droid.sdk() >= 33:
            tile = Button(self)
            tile.setText("Dodaj pločicu u Quick Settings")
            tile.setOnClickListener(ClickListener(self.on_add_tile))
            root.addView(tile)

        upd = Button(self)
        upd.setText("Ažuriraj yt-dlp")
        upd.setOnClickListener(ClickListener(self.on_update))
        root.addView(upd)

        self.status = TextView(self)
        self.status.setTextSize(13.0)
        self.status.setPadding(0, int(16 * dp), 0, 0)
        root.addView(self.status)
        self.set_status()

        scroll = ScrollView(self)
        scroll.setFitsSystemWindows(True)
        scroll.addView(root)
        self.setContentView(scroll)

    def set_status(self, extra=""):
        txt = f"yt-dlp {core.ytdlp_version()}"
        if extra:
            txt += f"\n{extra}"
        self.status.setText(txt)

    @Override(jvoid, [jboolean])
    def onWindowFocusChanged(self, has_focus):
        Activity.onWindowFocusChanged(self, has_focus)
        if has_focus and not str(self.url_edit.getText().toString()).strip():
            url = droid.read_clipboard_url(self)
            if url:
                self.url_edit.setText(url)

    def on_download(self):
        url = core.extract_url(self.url_edit.getText().toString())
        if not url:
            toast(self, "Nema linka")
            return
        open_picker(self, url)

    def on_add_tile(self):
        sbm = cast(StatusBarManager, self.getSystemService(StatusBarManager.getClass()))
        comp = ComponentName(self, f"{droid.PKG}.LinkTileService")
        sbm.requestAddTileService(
            comp, "Skini video", Icon.createWithResource(self, droid.R.drawable.ic_tile),
            self.getMainExecutor(),
            ResultConsumer(lambda r: toast(self, TILE_RESULTS.get(
                r if isinstance(r, int) else r.intValue(), "OK"))))

    def on_update(self):
        self.set_status("proveravam…")

        def work():
            try:
                _, msg = core.update_ytdlp(force=True)
            except Exception as e:
                msg = f"Greška: {core.friendly_error(e)}"
            droid.on_main(lambda: self.set_status(msg))
        threading.Thread(target=work, daemon=True).start()

    def auto_update(self):
        try:
            v, msg = core.update_ytdlp(force=False)
            if v:
                droid.on_main(lambda: self.set_status(msg))
        except Exception:
            traceback.print_exc()


def selftest(ctx, url):
    print("CLIPSNAG_SELFTEST start", url, flush=True)
    try:
        droid.show_link_notification(ctx, url)
        info = core.get_info(url)
        opts = core.build_options(info)
        print("CLIPSNAG_SELFTEST options", json.dumps(opts), flush=True)
        pref = [o for o in opts if o["kind"] != "audio" and (o.get("height") or 0) <= 720] or opts
        merge = [o for o in pref if o["kind"] == "merge"]
        opt = (merge or pref)[0]
        i = droid.component_intent(ctx, "DownloadService")
        i.putExtra("url", url)
        i.putExtra("title", info.get("title") or "selftest")
        i.putExtra("option", json.dumps(opt))
        ctx.startForegroundService(i)
        print("CLIPSNAG_SELFTEST started", json.dumps(opt), flush=True)
    except Exception as e:
        traceback.print_exc()
        print("CLIPSNAG_SELFTEST failed", core.friendly_error(e), flush=True)


# ------------------------------------------------------- clipboard / share entry

class GrabActivity(static_proxy(Activity)):
    """Invisible activity. Android 10+ only lets the focused app read the
    clipboard, so the tile opens this for a split second."""

    @Override(jvoid, [Bundle])
    def onCreate(self, state):
        Activity.onCreate(self, state)
        droid.ensure_core(self)
        self.handled = False
        intent = self.getIntent()
        if intent is not None and intent.getAction() == Intent.ACTION_SEND:
            self.handled = True
            url = core.extract_url(intent.getStringExtra(Intent.EXTRA_TEXT))
            if url:
                open_picker(self, url)
            else:
                toast(self, "U podeljenom tekstu nema linka")
            self.finish()

    @Override(jvoid, [jboolean])
    def onWindowFocusChanged(self, has_focus):
        Activity.onWindowFocusChanged(self, has_focus)
        if not has_focus or self.handled:
            return
        self.handled = True
        try:
            url = droid.read_clipboard_url(self)
            if not url:
                toast(self, "U clipboardu nema linka. Prvo kopiraj link videa.", long=True)
            elif droid.can_notify(self):
                droid.show_link_notification(self, url)
            else:
                open_picker(self, url)
        except Exception:
            traceback.print_exc()
            toast(self, "Ne mogu da pročitam clipboard")
        self.finish()


class LinkTileService(static_proxy(TileService)):

    @Override(jvoid, [])
    def onStartListening(self):
        TileService.onStartListening(self)
        t = self.getQsTile()
        if t is not None:
            t.setState(Tile.STATE_INACTIVE)
            t.setLabel("Skini video")
            t.setSubtitle("link iz clipboarda")
            t.updateTile()

    @Override(jvoid, [])
    def onClick(self):
        TileService.onClick(self)

        def go():
            i = droid.component_intent(self, "GrabActivity")
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if droid.sdk() >= 34:
                self.startActivityAndCollapse(
                    PendingIntent.getActivity(self, 0, i, droid.FLAGS_PI))
            else:
                self.startActivityAndCollapse(i)

        if self.isLocked():
            self.unlockAndRun(droid.Run(go))
        else:
            go()


class DismissReceiver(static_proxy(BroadcastReceiver)):

    @Override(jvoid, [Context, Intent])
    def onReceive(self, context, intent):
        droid.notif_manager(context).cancel(intent.getIntExtra("nid", 0))


# ------------------------------------------------------------------ quality picker

class PickerActivity(static_proxy(Activity)):

    @Override(jvoid, [Bundle])
    def onCreate(self, state):
        Activity.onCreate(self, state)
        droid.ensure_core(self)
        intent = self.getIntent()
        self.url = intent.getStringExtra("url")
        nid = intent.getIntExtra("nid", 0)
        if nid:
            droid.notif_manager(self).cancel(nid)
        self.dialog = None
        if not self.url:
            self.finish()
            return
        self.dialog = (dialog_builder(self)
                       .setTitle("ClipSnag")
                       .setMessage(f"Učitavam dostupne kvalitete…\n{core.short_host(self.url)}")
                       .setNegativeButton("Otkaži", DialogClick(lambda w: self.finish()))
                       .setOnCancelListener(DialogCancel(self.finish))
                       .create())
        self.dialog.show()
        threading.Thread(target=self.fetch, daemon=True).start()

    def fetch(self):
        info = opts = err = None
        try:
            info = core.get_info(self.url)
            opts = core.build_options(info)
            if not opts:
                err = "Nisam našao video na ovom linku."
        except Exception as e:
            traceback.print_exc()
            err = core.friendly_error(e)
        droid.on_main(lambda: self.show_options(info, opts, err))

    def show_options(self, info, opts, err):
        if self.isFinishing() or self.isDestroyed():
            return
        if self.dialog is not None:
            self.dialog.setOnCancelListener(None)
            self.dialog.dismiss()
        b = dialog_builder(self).setOnDismissListener(DialogDismiss(self.finish))
        if err:
            self.dialog = (b.setTitle("Ne mogu da skinem ovo")
                           .setMessage(err)
                           .setPositiveButton("OK", None)
                           .show())
            return
        title = (info.get("title") or core.short_host(self.url))[:90]
        labels = [o["label"] for o in opts]

        def pick(which):
            opt = opts[which]
            i = droid.component_intent(self, "DownloadService")
            i.putExtra("url", self.url)
            i.putExtra("title", title)
            i.putExtra("option", json.dumps(opt))
            self.startForegroundService(i)
            toast(self, f"Skidam {opt['label'].split(' · ')[0]}…")

        self.dialog = (b.setTitle(title)
                       .setItems(labels, DialogClick(pick))
                       .setNegativeButton("Otkaži", None)
                       .show())

    @Override(jvoid, [])
    def onDestroy(self):
        if getattr(self, "dialog", None) is not None:
            try:
                self.dialog.setOnDismissListener(None)
                self.dialog.dismiss()
            except Exception:
                pass
        Activity.onDestroy(self)


# --------------------------------------------------------------- download service

class Job:
    def __init__(self, url, title, option):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.title = title
        self.option = option
        self.nid = droid.nid_for(url)
        self.cancel = threading.Event()


_lock = threading.Lock()
_svc = {"pending": [], "current": None, "thread": None}


class DownloadService(static_proxy(Service)):

    @Override(IBinder, [Intent])
    def onBind(self, intent):
        return None

    @Override(jint, [Intent, jint, jint])
    def onStartCommand(self, intent, flags, start_id):
        droid.ensure_core(self)
        if intent is None:
            return Service.START_NOT_STICKY
        if intent.getAction() == "cancel":
            cur = _svc["current"]
            if cur is not None:
                cur.cancel.set()
            return Service.START_NOT_STICKY

        job = Job(intent.getStringExtra("url"), intent.getStringExtra("title") or "video",
                  json.loads(intent.getStringExtra("option")))
        self.startForeground(
            FG_ID,
            droid.progress_notification(self, job.title, "u redu…", None, self.cancel_pi()),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        with _lock:
            _svc["pending"].append(job)
            if _svc["thread"] is None:
                t = threading.Thread(target=self.worker, daemon=True)
                _svc["thread"] = t
                t.start()
        return Service.START_NOT_STICKY

    def cancel_pi(self):
        i = droid.component_intent(self, "DownloadService").setAction("cancel")
        return PendingIntent.getService(self, 1, i, droid.FLAGS_PI)

    def worker(self):
        while True:
            with _lock:
                if not _svc["pending"]:
                    _svc["thread"] = None
                    break
                job = _svc["pending"].pop(0)
                _svc["current"] = job
            try:
                self.run_job(job)
            finally:
                _svc["current"] = None
        droid.on_main(self.maybe_stop)

    def maybe_stop(self):
        with _lock:
            idle = _svc["thread"] is None and not _svc["pending"]
        if idle:
            self.stopForeground(jint(Service.STOP_FOREGROUND_REMOVE))
            self.stopSelf()

    def run_job(self, job):
        nm = droid.notif_manager(self)
        cancel_pi = self.cancel_pi()

        def progress(frac, text):
            nm.notify(FG_ID, droid.progress_notification(
                self, job.title, text, frac, cancel_pi, queued=len(_svc["pending"])))

        workdir = os.path.join(str(self.getCacheDir().getAbsolutePath()), "dl", job.id)
        try:
            progress(None, "čitam informacije…")
            info = core.get_info(job.url)
            paths = core.download_parts(info, job.option, workdir, progress, job.cancel)
            name = core.output_name(info, job.option)
            kind = job.option["kind"]
            if kind == "merge":
                progress(None, "spajam sliku i zvuk…")
                out = os.path.join(workdir, "out." + job.option["ext"])
                droid.remux([(paths[0], "video/"), (paths[1], "audio/")], out, job.option["ext"])
            elif job.option.get("remux"):
                progress(None, "pakujem u MP4…")
                out = os.path.join(workdir, "out.mp4")
                droid.remux([(paths[0], "video/"), (paths[0], "audio/")], out, "mp4")
            else:
                out = paths[0]
                actual = out.rsplit(".", 1)[-1].lower()
                if kind == "audio" and actual == "webm":
                    actual = "weba"
                name = name.rsplit(".", 1)[0] + "." + actual
            progress(None, "čuvam u Download/ClipSnag…")
            uri = droid.save_to_downloads(self, out, name)
            droid.notify_done(self, job.nid, name, uri, droid.mime_for(name))
            print("CLIPSNAG_DONE", name, os.path.getsize(out), flush=True)
        except core.Cancelled:
            nm.cancel(job.nid)
        except Exception as e:
            traceback.print_exc()
            if job.cancel.is_set():
                nm.cancel(job.nid)
            else:
                droid.notify_error(self, job.nid, job.url, core.friendly_error(e))
            print("CLIPSNAG_ERROR", core.friendly_error(e), flush=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
