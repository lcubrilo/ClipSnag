"""Android glue written in Python (via Chaquopy): notifications, clipboard,
muxing with MediaMuxer and saving into the public Download folder."""

import os
import shutil
import traceback
import zlib

from java import cast, dynamic_proxy, jclass, jint
from java.lang import Runnable

from android.app import Notification, NotificationChannel, NotificationManager, PendingIntent
from android.content import ClipboardManager, ContentValues, Context, Intent
from android.graphics.drawable import Icon
from android.media import MediaCodec, MediaExtractor, MediaFormat, MediaMuxer
from android.os import Build, Environment, Handler, Looper
from android.provider import MediaStore
from java.nio import ByteBuffer

# Not `from io.github... import R`: the name `io` would resolve to Python's io module.
R = jclass("io.github.lcubrilo.clipsnag.R")

from . import core

PKG = "clipsnag.components"
CH_LINKS = "links"
CH_PROGRESS = "progress"
CH_DONE = "done"
FOLDER = "ClipSnag"


# ------------------------------------------------------------------ small helpers

class Run(dynamic_proxy(Runnable)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.fn()
        except Exception:
            traceback.print_exc()


_main_handler = None


def on_main(fn):
    global _main_handler
    if _main_handler is None:
        _main_handler = Handler(Looper.getMainLooper())
    _main_handler.post(Run(fn))


def ensure_core(ctx):
    core.init(str(ctx.getFilesDir().getAbsolutePath()))


def component_intent(ctx, cls_name):
    return Intent().setClassName(ctx, f"{PKG}.{cls_name}")


def nid_for(url):
    return 1000 + (zlib.crc32(url.encode("utf-8")) & 0x0FFFFFFF)


def notif_manager(ctx):
    return cast(NotificationManager, ctx.getSystemService(Context.NOTIFICATION_SERVICE))


def can_notify(ctx):
    return notif_manager(ctx).areNotificationsEnabled()


def ensure_channels(ctx):
    nm = notif_manager(ctx)
    if nm.getNotificationChannel(CH_LINKS) is None:
        ch = NotificationChannel(CH_LINKS, "Linkovi spremni za skidanje",
                                 NotificationManager.IMPORTANCE_HIGH)
        ch.setDescription("Iskače kad je video link spreman")
        nm.createNotificationChannel(ch)
    if nm.getNotificationChannel(CH_PROGRESS) is None:
        ch = NotificationChannel(CH_PROGRESS, "Preuzimanje u toku",
                                 NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(ch)
    if nm.getNotificationChannel(CH_DONE) is None:
        ch = NotificationChannel(CH_DONE, "Završena preuzimanja",
                                 NotificationManager.IMPORTANCE_DEFAULT)
        nm.createNotificationChannel(ch)


def read_clipboard_url(ctx):
    cm = cast(ClipboardManager, ctx.getSystemService(Context.CLIPBOARD_SERVICE))
    if cm is None or not cm.hasPrimaryClip():
        return None
    clip = cm.getPrimaryClip()
    if clip is None or clip.getItemCount() == 0:
        return None
    text = clip.getItemAt(0).coerceToText(ctx)
    return core.extract_url(text.toString() if text is not None else None)


FLAGS_PI = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT


def _action(ctx, icon_res, title, pi):
    return Notification.Action.Builder(Icon.createWithResource(ctx, icon_res), title, pi).build()


def picker_pending(ctx, url, nid):
    i = component_intent(ctx, "PickerActivity")
    i.putExtra("url", url)
    i.putExtra("nid", jint(nid))
    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    return PendingIntent.getActivity(ctx, nid, i, FLAGS_PI)


def show_link_notification(ctx, url):
    """The 'video link ready' notification with Download / Dismiss buttons."""
    ensure_channels(ctx)
    nid = nid_for(url)
    dismiss = component_intent(ctx, "DismissReceiver")
    dismiss.putExtra("nid", jint(nid))
    dismiss_pi = PendingIntent.getBroadcast(ctx, nid, dismiss, FLAGS_PI)
    dl_pi = picker_pending(ctx, url, nid)
    host = core.short_host(url)
    n = (Notification.Builder(ctx, CH_LINKS)
         .setSmallIcon(R.drawable.ic_stat)
         .setContentTitle("Video link spreman za skidanje")
         .setContentText(f"{host} · {url}")
         .setStyle(Notification.BigTextStyle().bigText(url))
         .setContentIntent(dl_pi)
         .setAutoCancel(True)
         .setCategory(Notification.CATEGORY_RECOMMENDATION)
         .addAction(_action(ctx, R.drawable.ic_stat, "Skini", dl_pi))
         .addAction(_action(ctx, R.drawable.ic_close, "Ne skini", dismiss_pi))
         .build())
    notif_manager(ctx).notify(nid, n)
    return nid


def progress_notification(ctx, title, text, frac, cancel_pi, queued=0):
    ensure_channels(ctx)
    b = (Notification.Builder(ctx, CH_PROGRESS)
         .setSmallIcon(R.drawable.ic_stat)
         .setContentTitle(title)
         .setContentText(text + (f" · još {queued} u redu" if queued else ""))
         .setOngoing(True)
         .setOnlyAlertOnce(True))
    if frac is None:
        b.setProgress(0, 0, True)
    else:
        b.setProgress(1000, int(frac * 1000), False)
    if cancel_pi is not None:
        b.addAction(_action(ctx, R.drawable.ic_close, "Otkaži", cancel_pi))
    return b.build()


def notify_done(ctx, nid, name, uri, mime):
    ensure_channels(ctx)
    view = Intent(Intent.ACTION_VIEW)
    view.setDataAndType(uri, mime)
    view.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK)
    view_pi = PendingIntent.getActivity(ctx, nid, view, FLAGS_PI)
    share = Intent(Intent.ACTION_SEND)
    share.setType(mime)
    share.putExtra(Intent.EXTRA_STREAM, uri)
    share.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    chooser = Intent.createChooser(share, "Podeli video")
    chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    share_pi = PendingIntent.getActivity(ctx, nid + 1, chooser, FLAGS_PI)
    n = (Notification.Builder(ctx, CH_DONE)
         .setSmallIcon(R.drawable.ic_stat)
         .setContentTitle("Preuzeto ✓")
         .setContentText(f"Download/{FOLDER}/{name}")
         .setStyle(Notification.BigTextStyle().bigText(f"Download/{FOLDER}/{name}"))
         .setContentIntent(view_pi)
         .setAutoCancel(True)
         .addAction(_action(ctx, R.drawable.ic_stat, "Otvori", view_pi))
         .addAction(_action(ctx, R.drawable.ic_stat, "Podeli", share_pi))
         .build())
    notif_manager(ctx).notify(nid, n)


def notify_error(ctx, nid, url, message):
    ensure_channels(ctx)
    retry_pi = picker_pending(ctx, url, nid)
    n = (Notification.Builder(ctx, CH_DONE)
         .setSmallIcon(R.drawable.ic_close)
         .setContentTitle("Preuzimanje nije uspelo")
         .setContentText(message)
         .setStyle(Notification.BigTextStyle().bigText(f"{message}\n{url}"))
         .setAutoCancel(True)
         .addAction(_action(ctx, R.drawable.ic_stat, "Probaj ponovo", retry_pi))
         .build())
    notif_manager(ctx).notify(nid, n)


# ----------------------------------------------------------------------- muxing

def remux(sources, out_path, container):
    """Copy tracks into one file without re-encoding.
    sources: list of (path, 'video/' | 'audio/'). container: 'mp4' | 'webm'."""
    fmt = (MediaMuxer.OutputFormat.MUXER_OUTPUT_WEBM if container == "webm"
           else MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
    muxer = MediaMuxer(out_path, fmt)
    tracks = []  # [extractor, muxer_track]
    max_size = 1024 * 1024
    try:
        for path, prefix in sources:
            ex = MediaExtractor()
            ex.setDataSource(path)
            for i in range(ex.getTrackCount()):
                f = ex.getTrackFormat(i)
                mime = f.getString(MediaFormat.KEY_MIME) or ""
                if mime.startswith(prefix):
                    ex.selectTrack(i)
                    if f.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE):
                        max_size = max(max_size, f.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                    tracks.append([ex, muxer.addTrack(f)])
                    break
            else:
                ex.release()
        if not tracks:
            raise RuntimeError("nema traka za spajanje")
        buf = ByteBuffer.allocateDirect(max(max_size, 8 * 1024 * 1024))
        info = MediaCodec.BufferInfo()
        muxer.start()
        active = list(tracks)
        while active:
            # Interleave by timestamp.
            t = min(active, key=lambda tr: tr[0].getSampleTime())
            ex, ti = t
            n = ex.readSampleData(buf, 0)
            if n < 0:
                active.remove(t)
                continue
            flags = MediaCodec.BUFFER_FLAG_KEY_FRAME if (ex.getSampleFlags() & MediaExtractor.SAMPLE_FLAG_SYNC) else 0
            info.set(0, n, ex.getSampleTime(), flags)
            muxer.writeSampleData(ti, buf, info)
            ex.advance()
        muxer.stop()
    finally:
        for ex, _ in tracks:
            try:
                ex.release()
            except Exception:
                pass
        try:
            muxer.release()
        except Exception:
            pass


# ------------------------------------------------------------------- MediaStore

MIME = {
    "mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska", "mov": "video/quicktime",
    "m4a": "audio/mp4", "weba": "audio/webm", "mp3": "audio/mpeg", "opus": "audio/ogg",
    "ogg": "audio/ogg", "aac": "audio/aac", "jpg": "image/jpeg", "png": "image/png",
}


def mime_for(name):
    return MIME.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def save_to_downloads(ctx, src_path, name):
    """Copy a finished file into Download/ClipSnag via MediaStore. Returns Uri."""
    resolver = ctx.getContentResolver()
    mime = mime_for(name)
    cv = ContentValues()
    cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name)
    cv.put(MediaStore.MediaColumns.MIME_TYPE, mime)
    cv.put(MediaStore.MediaColumns.RELATIVE_PATH, f"{Environment.DIRECTORY_DOWNLOADS}/{FOLDER}")
    uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv)
    if uri is None:
        raise RuntimeError("ne mogu da napravim fajl u Download folderu")
    try:
        pfd = resolver.openFileDescriptor(uri, "w")
        fd = pfd.detachFd()
        with open(src_path, "rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    except Exception:
        resolver.delete(uri, None, None)
        raise
    return uri


def sdk():
    return Build.VERSION.SDK_INT
