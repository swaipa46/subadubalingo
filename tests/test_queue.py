"""The queue model (queue_view.VideoQueue, no Tk): adding with choices, moving and removing, the order of the rounds
(every video's first round before any second), skipping, errors and stops, and saving/resuming after a crash.
  python tests/test_queue.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from subadubalingo import common

tmp = Path(tempfile.mkdtemp(prefix="sdl-queue-test-"))
common._DATA_DIR = tmp          # never the real ~/.subadubalingo
from subadubalingo import i18n, queue_view as qv

i18n.set_language("da")
DA = ("da", None, "da", False, False, False)
SV_DUB = ("da", "sv", "no", True, False, False)

# Adding: same video with the same choices once; with other choices again
q = qv.VideoQueue(tmp / "queue.json")
assert q.add(["C:/v/a.mp4", "C:/v/b.mp4", "C:/v/A.mp4"], DA) == 2
assert q.add(["C:/v/a.mp4"], SV_DUB) == 1
assert [Path(i.video).name for i in q.items] == ["a.mp4", "b.mp4", "a.mp4"]
assert q.items[2].choices() == SV_DUB

# Moving and removing
assert q.move(2, -1) == 1 and q.items[1].speech == "sv"
assert q.move(0, -1) == 0          # the first cannot move up
assert q.move(2, 1) == 2           # the last cannot move down
q.remove(1)
assert [i.speech for i in q.items] == [None, None]
q.add(["C:/v/c.mp4"], SV_DUB)
a, b, c = q.items

# The rounds: all first rounds, then all Claude rounds, then all the making
order = []
while (step := q.next_step()) is not None:
    item, phase = step
    q.begin(item, phase)
    assert item.state == "running"
    order.append((Path(item.video).stem, qv.PHASES[phase]))
    if phase == 0 and item is b:
        q.skip(item, phase)          # nothing to listen to (subtitles already there)
    else:
        q.end(item, phase, "done", f"C:/v/{Path(item.video).stem}-{phase}.out", files=[f"C:/v/f{phase}"],
              words=[["fløv", "flow"]])
assert order == [("a", "listen"), ("b", "listen"), ("c", "listen"), ("a", "claude"), ("b", "claude"), ("c", "claude"),
                 ("a", "make"), ("b", "make"), ("c", "make")], order
assert all(i.state == "done" and i.phase == 3 for i in q.items)
assert a.result == "C:/v/a-2.out" and a.files == ["C:/v/f0", "C:/v/f1", "C:/v/f2"] and a.words == [["fløv", "flow"]]

# Errors, Claude rounds left unfinished and stops do not stop the others; their later rounds are not run
q = qv.VideoQueue(tmp / "queue.json")
q.add(["C:/v/1.mp4", "C:/v/2.mp4", "C:/v/3.mp4", "C:/v/4.mp4"], SV_DUB)
one, two, three, four = q.items
seen = []
while (step := q.next_step()) is not None:
    item, phase = step
    q.begin(item, phase)
    seen.append((Path(item.video).stem, phase))
    if item is one and phase == 0:
        q.end(item, phase, "error", "ffmpeg fejlede")
    elif item is two and phase == 1:
        q.end(item, phase, "skipped", "Claude-trinnet blev afbrudt")
    elif item is three and phase == 1:
        q.end(item, phase, "done", "C:/v/3 - klippet.mp4", no_cut=True)
    elif item is four and phase == 1:
        q.end(item, phase, "cancelled")
    else:
        q.end(item, phase, "done", "x")
assert ("1", 1) not in seen and ("2", 2) not in seen and ("4", 2) not in seen and ("3", 2) in seen, seen
assert [i.state for i in q.items] == ["error", "skipped", "done", "cancelled"]
assert one.message == "ffmpeg fejlede" and three.no_cut
assert q.counts()["done"] == 1 and not q.pending()

# Dropping the rest: the ones not started stay waiting and are shown as not started
q = qv.VideoQueue(tmp / "queue.json")
q.add(["C:/v/x.mp4", "C:/v/y.mp4"], DA)
x, y = q.items
q.begin(x, 0)
q.end(x, 0, "cancelled")
q.stop()
assert q.next_step() is None and y.state == "waiting"
assert qv.state_text(y, stopped=True) == "Ikke startet"

# Saved while running, resumed after a crash: the round that was running runs again
q = qv.VideoQueue(tmp / "queue.json")
q.add(["C:/v/r1.mp4", "C:/v/r2.mp4"], SV_DUB)
r1, r2 = q.items
q.running = True
q.begin(r1, 0)
q.end(r1, 0, "done", "C:/v/r1.srt", files=["C:/v/r1.srt"])
q.begin(r2, 0)
q.save()
data = json.loads((tmp / "queue.json").read_text(encoding="utf-8"))
assert data["running"] and data["current"] == [r2.id, 0]
back = qv.VideoQueue.load_unfinished(tmp / "queue.json")
assert back is not None and [i.video for i in back.items] == [r1.video, r2.video]
assert back.items[1].state == "waiting" and back.items[1].phase == 0
assert back.next_step() == (back.items[1], 0)
assert back.items[0].files == ["C:/v/r1.srt"] and back.items[0].choices() == SV_DUB
# Unknown keys from a newer version are ignored
data["items"][0]["future_field"] = 1
(tmp / "queue.json").write_text(json.dumps(data), encoding="utf-8")
assert qv.VideoQueue.load_unfinished(tmp / "queue.json") is not None
# A queue that was not running, a finished one, a broken file or none: nothing to resume
q.running = False
q.save()
assert qv.VideoQueue.load_unfinished(tmp / "queue.json") is None
q.running = True
for i in q.items:
    i.state = "done"
q.save()
assert qv.VideoQueue.load_unfinished(tmp / "queue.json") is None
(tmp / "queue.json").write_text("{not json", encoding="utf-8")
assert qv.VideoQueue.load_unfinished(tmp / "queue.json") is None
q.forget()
assert not (tmp / "queue.json").exists() and qv.VideoQueue.load_unfinished(tmp / "queue.json") is None

# Prøv igen: same video and choices, nothing of the old run
f = three.fresh()
assert f.choices() == three.choices() and f.state == "waiting" and f.phase == 0 and not f.files and f.id != three.id

# Words for the list, in all four languages (never upper/lowercased language names)
it = qv.Item("C:/v/a.mp4", *SV_DUB)
assert qv.choices_text(it) == "Dansk · svensk tale · norske undertekster · klippes først", qv.choices_text(it)
assert qv.choices_text(qv.Item("C:/v/a.mp4", *DA)) == "Dansk · danske undertekster"
assert qv.choices_text(qv.Item("C:/v/a.mp4", "en")) == "Engelsk · kun teksten"
it.state, it.phase = "running", 1
assert qv.state_text(it) == "Claude-runde nu"
i18n.set_language("sv")
assert qv.choices_text(qv.Item("C:/v/a.mp4", *SV_DUB)).startswith("Danska · tal på svenska · norska undertexter")
i18n.set_language("da")

# Dropped paths: videos only, a folder gives its videos
d = tmp / "drop"
(d / "sub").mkdir(parents=True)
for n in ("b.MP4", "a.mkv", "notes.txt"):
    (d / n).write_text("x")
(d / "sub" / "c.mp4").write_text("x")
got = qv.videos_in([d, d / "notes.txt", d / "sub" / "c.mp4", d / "a.mkv"])
assert [p.name for p in got] == ["a.mkv", "b.MP4", "c.mp4"], got

# Same keys in all four locale files
root = Path(__file__).resolve().parent.parent / "assets" / "locales"
sets = {c: set(json.loads((root / c / "queue.json").read_text(encoding="utf-8"))) for c in ("da", "sv", "no", "en")}
assert all(s == sets["da"] for s in sets.values()), {c: s ^ sets["da"] for c, s in sets.items()}
assert all(k.startswith("queue.") for k in sets["da"])
print("all ok")
