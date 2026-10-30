"""Platform-independent logic: link detection, yt-dlp bootstrap/update, quality
options and downloading. Nothing in here touches Android APIs, so it can be
unit-tested on a desktop Python."""

import copy
import glob
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
import zipfile

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

KNOWN_HOSTS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "facebook.com", "fb.watch", "vimeo.com", "reddit.com", "redd.it",
    "twitch.tv", "dailymotion.com", "streamable.com", "threads.net",
    "pinterest.", "pin.it", "soundcloud.com", "bilibili.com", "linkedin.com",
    "snapchat.com", "tumblr.com", "bsky.app", "9gag.com", "imgur.com",
)

AUDIO_EXTS = {"m4a", "mp3", "opus", "ogg", "oga", "wav", "aac", "weba", "flac"}

YTDLP_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"


# --------------------------------------------------------------------------- links

def extract_url(text):
    """Return the first http(s) URL found in text, or None."""
    if not text:
        return None
    m = URL_RE.search(str(text))
    if not m:
        return None
    return m.group(0).rstrip(".,;:!?)]}>")


def is_known_video_host(url):
    host = re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()
    return any(h in host for h in KNOWN_HOSTS)


def short_host(url):
    host = re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


# ----------------------------------------------------------------- yt-dlp bootstrap

_state = {"files_dir": None, "override": None}


def _zip_version(path):
    try:
        with zipfile.ZipFile(path) as z:
            src = z.read("yt_dlp/version.py").decode("utf-8", "replace")
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", src)
        return m.group(1) if m else None
    except Exception:
        return None


def _bundled_version():
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return "0"


def init(files_dir):
    """Must run before the first `import yt_dlp`. If a newer yt-dlp was
    downloaded by update_ytdlp(), put it first on sys.path."""
    if _state["files_dir"]:
        return
    _state["files_dir"] = files_dir
    path = os.path.join(files_dir, "yt-dlp.zip")
    if os.path.exists(path):
        v = _zip_version(path)
        if v and v > _bundled_version():
            if path not in sys.path:
                sys.path.insert(0, path)
            _state["override"] = v
        else:
            # Stale or broken (e.g. the app itself was updated with a newer one).
            try:
                os.remove(path)
            except OSError:
                pass


def ytdlp_version():
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return _state["override"] or _bundled_version()


def update_ytdlp(force=False):
    """Download the latest yt-dlp release (a zipimport-able file) into
    files_dir. Takes effect on the next app start. Returns (new_version, msg)."""
    files_dir = _state["files_dir"]
    if not files_dir:
        raise RuntimeError("core.init() was not called")
    stamp = os.path.join(files_dir, "yt-dlp.checked")
    if not force and os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
        return None, "checked recently"
    req = urllib.request.Request(YTDLP_RELEASE_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ClipSnag"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    with open(stamp, "w") as f:
        f.write(str(time.time()))
    latest = rel.get("tag_name", "").lstrip("v")
    current = ytdlp_version()
    if latest and latest <= current:
        return None, f"yt-dlp {current} je najnoviji"
    asset = next((a for a in rel.get("assets", []) if a.get("name") == "yt-dlp"), None)
    if not asset:
        return None, "nema yt-dlp fajla u izdanju"
    tmp = os.path.join(files_dir, "yt-dlp.zip.part")
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "ClipSnag"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    v = _zip_version(tmp)
    if not v:
        os.remove(tmp)
        raise RuntimeError("preuzeti yt-dlp nije ispravan")
    os.replace(tmp, os.path.join(files_dir, "yt-dlp.zip"))
    return v, f"yt-dlp {v} preuzet, važi od sledećeg pokretanja"


# ---------------------------------------------------------------------- extraction

_info_cache = {}
_cache_lock = threading.Lock()


def _ydl_base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 5,
        "cachedir": False,
    }


def get_info(url, max_age=600):
    """yt-dlp metadata for url (first entry if it is a playlist/carousel).
    Cached in-process so the download service can reuse what the picker fetched."""
    with _cache_lock:
        hit = _info_cache.get(url)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
    import yt_dlp
    with yt_dlp.YoutubeDL(_ydl_base_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("Link ne sadrži video")
        info = entries[0]
    with _cache_lock:
        _info_cache[url] = (time.time(), info)
    return info


# ------------------------------------------------------------------------- options

def _has_video(f):
    if f.get("vcodec") == "none":
        return False
    if (f.get("ext") or "") in AUDIO_EXTS and not f.get("height"):
        return False
    if f.get("resolution") == "audio only":
        return False
    return True


def _has_audio(f):
    return f.get("acodec") != "none"


def _size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def _usable(f):
    proto = f.get("protocol") or "https"
    if (f.get("format_note") or "").lower() == "storyboard" or f.get("ext") == "mhtml":
        return False
    if proto in ("m3u8", "f4m", "rtmp", "rtsp", "ism", "websocket_frag", "mms"):
        return False
    return bool(f.get("url") or f.get("fragments") or f.get("manifest_url"))


def _is_hls(f):
    return (f.get("protocol") or "").startswith("m3u8")


def _vfamily(f):
    """Container family a video-only stream can be muxed into with MediaMuxer."""
    vc = (f.get("vcodec") or "").lower()
    ext = (f.get("ext") or "").lower()
    if vc.startswith(("avc", "h264")) or (ext == "mp4" and not vc):
        return "mp4", 3
    if vc.startswith(("vp9", "vp09", "vp8")) or ext == "webm":
        return "webm", 2
    if vc.startswith(("hev", "hvc", "h265")):
        return "mp4", 1
    return None, 0  # av01 and friends: skip, MediaMuxer support is spotty


def _afamily(f):
    ac = (f.get("acodec") or "").lower()
    ext = (f.get("ext") or "").lower()
    if ac.startswith("mp4a") or ext in ("m4a", "mp4", "aac"):
        return "mp4"
    if ac.startswith(("opus", "vorbis")) or ext in ("webm", "weba", "ogg", "opus"):
        return "webm"
    return None


def _best(fmts, key):
    return max(fmts, key=key) if fmts else None


def _fmt_size(n):
    if not n:
        return ""
    if n >= 1024 ** 3:
        return f"~{n / 1024 ** 3:.1f} GB"
    return f"~{max(1, round(n / 1024 ** 2))} MB"


def build_options(info, max_options=8):
    """Turn yt-dlp formats into a short human list. Each option is a dict:
    {label, kind: 'single'|'merge'|'audio', video, audio, ext, height}."""
    formats = [f for f in (info.get("formats") or []) if _usable(f)]
    if not formats and info.get("url"):
        formats = [dict(info, format_id=info.get("format_id") or "0")]

    combined = [f for f in formats if _has_video(f) and _has_audio(f)]
    vonly = [f for f in formats if _has_video(f) and not _has_audio(f)]
    aonly = [f for f in formats if not _has_video(f) and _has_audio(f)]

    def aud_key(f):
        return (f.get("abr") or f.get("tbr") or 0, _size(f))

    best_audio = {
        fam: _best([a for a in aonly if _afamily(a) == fam], aud_key)
        for fam in ("mp4", "webm")
    }

    heights = sorted({f.get("height") for f in combined + vonly if f.get("height")}, reverse=True)
    options = []
    for h in heights:
        # Prefer a ready-made file with sound (no muxing needed).
        cands = [f for f in combined if f.get("height") == h]
        single = _best(cands, lambda f: ((f.get("ext") == "mp4"), f.get("protocol", "").startswith("http"),
                                         f.get("fps") or 0, f.get("tbr") or 0))
        merge = None
        vids = [f for f in vonly if f.get("height") == h and _vfamily(f)[0]
                and best_audio.get(_vfamily(f)[0])]
        if vids:
            v = _best(vids, lambda f: (_vfamily(f)[1], f.get("fps") or 0, f.get("tbr") or 0))
            merge = (v, best_audio[_vfamily(v)[0]])
        fps = None
        if single and (not merge or (single.get("tbr") or 0) >= (merge[0].get("tbr") or 0) * 0.8):
            ext = single.get("ext") or "mp4"
            fps = single.get("fps")
            opt = dict(kind="single", video=single["format_id"], audio=None, ext=ext, height=h,
                       size=_size(single), remux=_is_hls(single))
            if opt["remux"]:
                opt["ext"] = "mp4"
        elif merge:
            v, a = merge
            ext = _vfamily(v)[0]
            fps = v.get("fps")
            opt = dict(kind="merge", video=v["format_id"], audio=a["format_id"], ext=ext, height=h,
                       size=_size(v) + _size(a))
        else:
            continue
        fps_txt = f"{int(fps)}" if fps and fps > 30 else ""
        opt["label"] = " · ".join(x for x in (f"{h}p{fps_txt}", opt["ext"].upper(),
                                               _fmt_size(opt["size"])) if x)
        options.append(opt)
        if len(options) >= max_options:
            break

    if not options:
        # Unknown resolutions (common for Instagram/Twitter/direct files).
        cands = combined or [f for f in formats if _has_video(f)]
        if cands:
            f = _best(cands, lambda f: (f.get("tbr") or 0, _size(f)))
            options.append(dict(kind="single", video=f["format_id"], audio=None, remux=_is_hls(f),
                                ext="mp4" if _is_hls(f) else (f.get("ext") or "mp4"),
                                height=f.get("height"), size=_size(f),
                                label=" · ".join(x for x in ("Najbolji kvalitet", (f.get("ext") or "mp4").upper(),
                                                             _fmt_size(_size(f))) if x)))

    a = best_audio.get("mp4") or best_audio.get("webm") or _best(aonly, aud_key)
    if a:
        ext = "m4a" if _afamily(a) == "mp4" else (a.get("ext") or "audio")
        if ext == "webm":
            ext = "weba"
        options.append(dict(kind="audio", video=None, audio=a["format_id"], ext=ext, height=None,
                            size=_size(a), label=" · ".join(x for x in ("Samo zvuk", ext.upper(),
                                                                       _fmt_size(_size(a))) if x)))
    return options


# ------------------------------------------------------------------------ download

class Cancelled(Exception):
    pass


def safe_filename(name, maxlen=80):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", name or "video")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:maxlen].rstrip(" .") or "video")


def output_name(info, option):
    title = info.get("title") or info.get("id") or "video"
    if title.lower().startswith(("video by", "post by")) and info.get("uploader"):
        title = f"{info['uploader']} {info.get('id', '')}"
    tag = f" [{option['height']}p]" if option.get("height") else ""
    return f"{safe_filename(title)}{tag}.{option['ext']}"


def download_parts(info, option, workdir, on_progress=None, cancel=None):
    """Download the stream(s) of `option` into workdir. Returns list of paths
    ([single] or [video, audio]). on_progress(fraction 0..1, text)."""
    import yt_dlp

    os.makedirs(workdir, exist_ok=True)
    parts = [p for p in (option.get("video"), option.get("audio")) if p]
    fmt_by_id = {f.get("format_id"): f for f in (info.get("formats") or [])}
    sizes = [(_size(fmt_by_id.get(p, {})) or 1) for p in parts]
    total = float(sum(sizes))
    done_before = [0.0]
    last = [0.0]

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if d.get("status") != "downloading" or on_progress is None:
            return
        now = time.time()
        if now - last[0] < 0.7:
            return
        last[0] = now
        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        got = d.get("downloaded_bytes") or 0
        frac_part = (got / tot) if tot else 0.0
        frac = (done_before[0] + frac_part * sizes[idx[0]]) / total
        speed = d.get("speed")
        txt = f"{got / 1024 ** 2:.1f}" + (f" / {tot / 1024 ** 2:.1f} MB" if tot else " MB")
        if speed:
            txt += f" · {speed / 1024 ** 2:.1f} MB/s"
        if len(parts) > 1:
            txt = ("slika " if idx[0] == 0 else "zvuk ") + txt
        on_progress(min(frac, 0.999), txt)

    idx = [0]
    paths = []
    for i, fid in enumerate(parts):
        idx[0] = i
        opts = _ydl_base_opts()
        opts.update({
            "format": fid,
            "outtmpl": os.path.join(workdir, f"part{i}.%(ext)s"),
            "progress_hooks": [hook],
            "overwrites": True,
            "continuedl": False,
            "concurrent_fragment_downloads": 4,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                res = ydl.process_ie_result(copy.deepcopy(info), download=True)
        except Cancelled:
            raise
        except yt_dlp.utils.DownloadError as e:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raise RuntimeError(_clean_error(str(e)))
        path = None
        for rd in (res or {}).get("requested_downloads") or []:
            path = rd.get("filepath") or rd.get("_filename")
        if not path or not os.path.exists(path):
            found = [p for p in glob.glob(os.path.join(workdir, f"part{i}.*"))
                     if not p.endswith((".part", ".ytdl"))]
            path = found[0] if found else None
        if not path:
            raise RuntimeError("yt-dlp nije napravio fajl")
        paths.append(path)
        done_before[0] += sizes[i]
    return paths


def _clean_error(msg):
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    if "login" in msg.lower() or "cookies" in msg.lower():
        msg = "Sajt traži prijavu (privatan ili ograničen sadržaj). " + msg
    return msg[:300]


def friendly_error(exc):
    s = str(exc) or exc.__class__.__name__
    return _clean_error(s)
