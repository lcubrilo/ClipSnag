import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python"))
from clipsnag import core


def test_extract_url():
    assert core.extract_url("look https://www.instagram.com/reel/DfHixwjK8Bx/?rpxt=abc== wow") == \
        "https://www.instagram.com/reel/DfHixwjK8Bx/?rpxt=abc=="
    assert core.extract_url("(https://youtu.be/abc).") == "https://youtu.be/abc"
    assert core.extract_url("no link here") is None
    assert core.extract_url(None) is None


def test_known_host():
    assert core.is_known_video_host("https://m.youtube.com/watch?v=x")
    assert core.short_host("https://www.instagram.com/reel/x") == "instagram.com"


def yt_like_info():
    f = []
    f.append(dict(format_id="sb0", ext="mhtml", format_note="storyboard", vcodec="none", acodec="none", url="u", protocol="mhtml"))
    f.append(dict(format_id="18", ext="mp4", vcodec="avc1.42001E", acodec="mp4a.40.2", height=360, tbr=500, url="u", protocol="https", filesize=10_000_000))
    f.append(dict(format_id="140", ext="m4a", vcodec="none", acodec="mp4a.40.2", abr=129, url="u", protocol="https", filesize=4_000_000))
    f.append(dict(format_id="251", ext="webm", vcodec="none", acodec="opus", abr=140, url="u", protocol="https", filesize=4_200_000))
    f.append(dict(format_id="137", ext="mp4", vcodec="avc1.640028", acodec="none", height=1080, fps=30, tbr=4000, url="u", protocol="https", filesize=80_000_000))
    f.append(dict(format_id="248", ext="webm", vcodec="vp9", acodec="none", height=1080, fps=30, tbr=3000, url="u", protocol="https"))
    f.append(dict(format_id="313", ext="webm", vcodec="vp9", acodec="none", height=2160, fps=60, tbr=20000, url="u", protocol="https"))
    f.append(dict(format_id="401", ext="mp4", vcodec="av01.0.12M.08", acodec="none", height=2160, fps=60, tbr=18000, url="u", protocol="https"))
    f.append(dict(format_id="136", ext="mp4", vcodec="avc1.4d401f", acodec="none", height=720, tbr=2000, url="u", protocol="https"))
    return dict(id="abc", title="My: video/test?", formats=f)


def test_build_options_youtube():
    opts = core.build_options(yt_like_info())
    labels = [o["label"] for o in opts]
    assert labels[0].startswith("2160p60 · WEBM"), labels
    o2160 = opts[0]
    assert o2160["kind"] == "merge" and o2160["video"] == "313" and o2160["audio"] == "251"
    o1080 = next(o for o in opts if o["height"] == 1080)
    assert o1080["video"] == "137" and o1080["audio"] == "140" and o1080["ext"] == "mp4"
    o360 = next(o for o in opts if o["height"] == 360)
    assert o360["kind"] == "single" and o360["video"] == "18"
    assert opts[-1]["kind"] == "audio" and opts[-1]["audio"] == "140"
    assert not any(o.get("video") == "sb0" for o in opts)


def test_build_options_unknown_height():
    info = dict(id="x", title="Video by someone", uploader="someone",
                formats=[dict(format_id="dash-1", ext="mp4", url="u", protocol="https")])
    opts = core.build_options(info)
    assert opts[0]["kind"] == "single" and opts[0]["label"].startswith("Najbolji")
    assert core.output_name(info, opts[0]).startswith("someone x")


def test_hls_single_is_remuxed():
    info = dict(id="x", title="t", formats=[dict(format_id="hls-720", ext="mp4", vcodec="avc1", acodec="mp4a",
                                                 height=720, protocol="m3u8_native", url="u")])
    o = core.build_options(info)[0]
    assert o["remux"] and o["ext"] == "mp4"


def test_safe_filename():
    assert core.output_name(yt_like_info(), dict(height=1080, ext="mp4")) == "My video test [1080p].mp4"
