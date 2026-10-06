"""프로미스나인 영상 캘린더(ICS)에서 2022~2026년 '오프' 일정만 골라 events.json으로 저장한다.
캘린더에 없는 2017~2021년 일정은 history.py에서 합친다.

사용법: python sync.py
결과: out/events.json  ({"syncedAt": ..., "events": {id: {d, t, c, u}}})
      콘솔에 새로 추가/변경된 일정 요약 출력
"""
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from history import HISTORY, MUSIC_SHOWS

ICS_URL = (
    "https://calendar.google.com/calendar/ical/"
    "c04c6ea6c1d803550616182c4ef41f596fc5fbe4d5b51d2294b2003c3c14b1ce"
    "%40group.calendar.google.com/public/basic.ics"
)
YEARS = ("2022", "2023", "2024", "2025", "2026")
ROOT = Path(__file__).parent
# 공개 사이트(GitHub Actions)에서는 OFF_OUT=docs/events.json 으로 실행한다
OUT = Path(os.environ["OFF_OUT"]) if os.environ.get("OFF_OUT") else ROOT / "out" / "events.json"
KST = timezone(timedelta(hours=9))

# 제목에 이게 들어가면 영상/온라인 콘텐츠로 보고 제외
EXCLUDE = re.compile(
    r"영상|VCR|비하인드|BEHIND|[Bb]ehind|챌린지|[Cc]hallenge|쇼츠|[Ss]horts|인스타|유튜브|"
    r"리뷰|티저|[Tt]easer|TEASER|선공개|예고|[Ii]nterview|인터뷰|브이로그|[Vv]log|_log|"
    r"무대감독|미방분|메이킹|솔로무대|카메라|교차편집|엔딩요정|Split|NEXT WEEK|차차차|"
    r"매점가요|대신가드림|1위|퇴근|출근길 옆|개그콘서트|입덕투어|워크돌|FM_1\.24|"
    r"\d/\d|캐스팅|HIGHLIGHT|하이라이트|뉴스|News|NEWS|웃으면 안되는|시상식 채영|"
    r"채팅|발매|응원법|COUNTDOWN LIVE|COMEBACK LIVE|트위터|공식 X|워너비|직캠|"
    r"브이라이브|위버스 라이브|온라인|방구석|Choreography|[Vv]ideo|Stationhead|We Log|Stage cam|"
    r"출근길|아이컨택캠|Stage Mix|Film|Ep\.|EP\.|백스테이지|퇵길"
)

# 설명란에 이게 있으면 홍보·예고 영상으로 보고 제외 (예: 공연 날이 아닌 '콘서트 홍보영상')
DESC_EXCLUDE = re.compile(r"홍보 ?영상|예고편|미출연|송출|[Ss]tationhead")

# 음악방송: 방송사 이름 정리
SHOWS = [
    ("뮤직뱅크", r"뮤직뱅크|뮤뱅|Music ?Bank"),
    ("쇼! 음악중심", r"음악중심|음중|MusicCore"),
    ("인기가요", r"인기가요|인가|Inkigayo"),
    ("엠카운트다운", r"엠카운트다운|엠카|MCOUNTDOWN"),
    ("더쇼", r"더쇼|THE SHOW"),
    ("쇼챔피언", r"쇼 ?챔피언"),
]
SHOWS = [(n, re.compile(p)) for n, p in SHOWS]

# 음방 활동 이름 (활동 첫 음방의 연-월 → 타이틀곡). 없으면 설명란에서 곡명을 찾아 쓴다.
ACTIVITIES = {
    "2017-12": "유리구두",
    "2018-01": "To Heart",
    "2018-06": "두근두근",
    "2018-10": "LOVE BOMB",
    "2019-06": "FUN!",
    "2022-01": "DM",
    "2022-06": "Stay This Way",
    "2023-06": "#menow",
    "2023-12": "What are we (지원 피처링)",
    "2024-08": "Supersonic",
    "2025-06": "LIKE YOU BETTER",
    "2025-12": "하얀 그리움",
    "2026-07": "Vitamin ME",
}
# 관객 없이 진행돼 음방 오프가 없었던 활동 (코로나, 사용자 확인)
NO_AUDIENCE = {"DM"}

SONG_RX = [
    re.compile(r"fromis_9 \(프로미스나인\) [-–] ([^|#\[(]+?)\s*(?:[|#\[(]|$)"),
    re.compile(r"(?:^|/)\s*([^/|\[]+?) - (?:fromis_9|프로미스나인)"),
]

# 사용자가 빼 달라고 한 일정: (날짜, 제목에 들어간 말)
SKIP = [
    # 출근길은 전부 EXCLUDE에서 뺀다 (사용자 요청)
]

# 사녹을 본방과 다른 날에 한 경우: (본방 날짜, 방송 이름) → 사녹 날짜 (사용자 확인)
PREREC_DATE = {
    ("2025-06-28", "쇼! 음악중심"): "2025-06-26",  # LIKE YOU BETTER 음중 사녹을 엠카 사녹 날 같이 함
}

# 캘린더에 없지만 직접 넣는 오프: id → 일정 (공식 X 공지 기준)
EXTRA_EVENTS = {
    "x-2025-06-28-minifm": {"d": "2025-06-28", "t": "[LIKE YOU BETTER] 1주차 음악중심 미니 팬미팅", "c": "music"},
    "x-2024-08-24-minifm": {"d": "2024-08-24", "t": "[Supersonic] 2주차 음악중심 미니 팬미팅", "c": "music"},
    "x-2025-12-06-minifm": {"d": "2025-12-06", "t": "[하얀 그리움] 1주차 음악중심 미니 팬미팅", "c": "music"},
    "x-2026-08-30-public-fansign": {"d": "2026-08-30", "t": "[Vitamin ME] 공개 팬사인회 (구경)", "c": "event"},
    "x-2026-07-25-minifm": {"d": "2026-07-25", "t": "[Vitamin ME] 1주차 음악중심 미니 팬미팅", "c": "music"},
}

# 설명란에 시간이 없어도 하루 2회(낮공·밤공)였던 공연 (사용자 확인): (날짜, 제목에 들어간 말)
TWO_SHOWS = [
    ("2025-09-23", "IN Tokyo"),
]

# 사녹이 있었던 활동 주차 (사용자 확인). 활동 첫 음방이 있는 주(월~일)가 1주차.
# 키는 연도 또는 활동명(활동명이 먼저). 값이 None이면 그 주의 모든 음방에 사녹, 집합이면 그 방송에만 사녹.
PREREC_WEEKS = {
    "#menow": {1: None, 2: None},
    "2024": {1: None, 2: None},
    "2025": {1: None},
    "2026": {1: None, 2: {"뮤직뱅크", "쇼! 음악중심"}},
}

# (카테고리, 정규식) — 위에서부터 먼저 맞는 것으로 분류
RULES = [
    ("fansign", r"팬사인|팬싸"),
    # 팬미팅과 따로 세는 팬 이벤트: #wenow, 한가위 대잔치, Supersonic 발대식 (사용자 요청)
    ("fanevent", r"OFFLINE EVENT|발대식"),
    ("fanmeeting", r"팬미팅|팬밋업|FAN PARTY|팬파티|팬콘"),
    # 단독 공연이 아닌 합동 무대는 페스티벌로 (사용자 확인)
    ("festival", r"모모콘|Plant Our Planet|한미동맹|위문공연|위문열차|노사문화 ?콘서트|슈퍼히어로 ?콘서트|K-?POP ?(콘서트|CONCERT)|특집 ?콘서트"),
    ("concert", r"콘서트|앵콜콘|TOUR|월드투어|[Cc]oncert"),
    ("music", r"^\s*(쇼!\s*)?(뮤직뱅크|음악중심|인기가요|엠카운트다운|더쇼|쇼 ?챔피언)"),
    ("campus", r"대학교?.*축제|성균관대|과학기술원|사관학교|카이스트|KAIST|대학 축제|SPRING BREEZE in CAMPUS"),
    ("musical", r"뮤지컬"),
    ("festival", r"페스티벌|페스타|FESTA|[Ff]estival|FESTIVAL|워터밤|Waterbomb|워터 ?뮤직|풀파티|"
                 r"K-PULSE|KWAVE|SUPERPOP|THE SHINE|AKMF|PASSTIVAL|축제|문화제|드론제전|"
                 r"팬스티벌|Kstyle PARTY|가요대제전|가요대축제|가요대전|뮤직어워즈|하트 ?드림 ?어워즈|Awards|AWARDS|MAMA"),
    ("event", r"행사|포토콜|포토월|그린카펫|시사회|개막|전야제|축하공연|코리아 온 스테이지|로드 ?쇼|"
              r"리스닝파티|뷰잉 파티|사진전|론칭 이벤트|시구|SHOWCASE|쇼케이스|공개방송"),
]
RULES = [(c, re.compile(p)) for c, p in RULES]


def fetch_ics() -> str:
    req = urllib.request.Request(ICS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def parse(ics: str):
    ics = re.sub(r"\r?\n[ \t]", "", ics.replace("\r\n", "\n"))
    for block in ics.split("BEGIN:VEVENT")[1:]:
        ev = {}
        for line in block.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                ev.setdefault(k.split(";")[0], v)
        yield ev


def unescape(s: str) -> str:
    return s.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("&amp;", "&").strip()


def classify(title: str):
    if EXCLUDE.search(title):
        return None
    for cat, rx in RULES:
        if rx.search(title):
            return cat
    return None


def show_name(text: str):
    for name, rx in SHOWS:
        if rx.search(text):
            return name
    return None


def guess_song(texts):
    found = Counter()
    for t in texts:
        for rx in SONG_RX:
            for m in rx.finditer(t):
                found[m.group(1).strip()] += 1
    return found.most_common(1)[0][0] if found else None


def group_music(events: dict, descs: dict):
    """음방 본방을 활동 단위(14일 넘게 비면 새 활동)로 묶어
    제목 앞에 [활동명]을 붙이고, 주차 규칙에 맞는 날에 사녹 표시(s=1)를 한다."""
    shows, seen_day = [], set()
    for k, e in sorted(events.items(), key=lambda kv: (kv[1]["d"], kv[0])):
        if e.get("k") != "live":
            continue
        if (e["d"], e["n"]) in seen_day:  # 같은 날 같은 방송이 두 번 올라온 경우
            del events[k]
            continue
        seen_day.add((e["d"], e["n"]))
        shows.append((k, e))
    groups = []
    for k, e in shows:
        day = datetime.fromisoformat(e["d"])
        if not groups or (day - groups[-1]["last"]).days > 14:
            groups.append({"monday": day - timedelta(days=day.weekday()), "first": e["d"], "last": day, "items": []})
        groups[-1]["last"] = day
        groups[-1]["items"].append((k, e))

    for g in groups:
        act = ACTIVITIES.get(g["first"][:7]) or guess_song(descs.get(k, "") for k, _ in g["items"])
        if act in NO_AUDIENCE:
            for k, _ in g["items"]:
                del events[k]
            continue
        prefix = f"[{act}] " if act else ""
        rule = PREREC_WEEKS.get(act) or PREREC_WEEKS.get(g["first"][:4], {})
        for k, e in g["items"]:
            week = (datetime.fromisoformat(e["d"]) - g["monday"]).days // 7 + 1
            e["t"] = f"{prefix}{week}주차 {e['n']} 본방"
            e["w"] = week
            e.pop("s", None)
            if week in rule and (rule[week] is None or e["n"] in rule[week]):
                e["s"] = 1
                e.pop("sd", None)
                if (e["d"], e["n"]) in PREREC_DATE:
                    e["sd"] = PREREC_DATE[(e["d"], e["n"])]
        # 같은 기간의 출근길도 활동명을 붙인다
        for e in events.values():
            if e["c"] == "music" and "출근길" in e["t"] and g["first"] <= e["d"] <= g["last"].strftime("%Y-%m-%d") and prefix and not e["t"].startswith("["):
                e["t"] = prefix + e["t"]


def main():
    ics = fetch_ics()
    events = {}
    seen = set()  # 캘린더에 아직 있는 모든 일정 id
    descs = {}    # 음방 설명란 (활동명 찾기용)
    for ev in parse(ics):
        start = ev.get("DTSTART", "")
        if start[:4] not in YEARS or ev.get("STATUS") == "CANCELLED":
            continue
        uid = re.sub(r"[^A-Za-z0-9_-]", "", ev.get("UID", "").split("@")[0])[:120]
        if not uid:
            continue
        seen.add(uid)
        title = re.sub(r"\s+", " ", unescape(ev.get("SUMMARY", "")))
        desc = ev.get("DESCRIPTION", "")
        day = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        # 페스티벌 녹화분을 음악방송으로 송출한 경우: 제목 속 실제 날짜·행사로 바꾼다
        rec = re.search(r"\((\d{6}) ([^)]+)\)", title)
        if rec and show_name(title):
            ymd = rec.group(1)
            day = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
            start = "20" + ymd
            place = re.sub(r"^\d{4} ", "", rec.group(2))
            title = f"{place} ({show_name(title)} 녹화)"
        cat = classify(title)
        if any(day == d and key in title for d, key in SKIP):
            continue
        if not cat or DESC_EXCLUDE.search(re.sub(r"<[^>]+>", " ", unescape(desc))):
            continue
        m = re.search(r'href="([^"]+)"', desc) or re.search(r"(https?://[^\s<\"\\]+)", desc)
        if start[:4] not in YEARS:
            continue
        item = {"d": day, "t": title, "c": cat}
        name = show_name(title)
        plain_show = re.fullmatch(r"\s*(쇼!\s*)?(뮤직뱅크|음악중심|인기가요|엠카운트다운|더쇼|쇼 ?챔피언)\s*(MCOUNTDOWN)?\s*", title)
        if cat == "music" and name and plain_show:
            # 음악방송 본방. 사녹이 있던 날은 페이지에서 '사녹' 항목이 하나 더 생긴다
            item["t"] = f"{name} 본방"
            item["n"] = name
            item["k"] = "live"
            descs[uid] = re.sub(r"<[^>]+>", " ", re.sub(r"<br>", " / ", unescape(desc)))
        elif rec and name:
            item["c"] = "festival"
        if m:
            item["u"] = unescape(m.group(1))
        events[uid] = item
        # 하루 2회 공연(설명란 '13시 / 18시')은 낮공·밤공 두 항목으로 나눈다
        two = re.search(r"(\d{1,2})시\s*/\s*(\d{1,2})시", re.sub(r"<[^>]+>", " ", unescape(desc)))
        manual = any(day == d and key in title for d, key in TWO_SHOWS)
        if cat == "concert" and (two or manual):
            t1, t2 = (f" ({two.group(1)}시)", f" ({two.group(2)}시)") if two else ("", "")
            events[uid + "-2"] = {**item, "t": f"{title} 밤공{t2}"}
            item["t"] = f"{title} 낮공{t1}"
            seen.add(uid + "-2")

    # 캘린더에 없는 2017~2021년 일정 (history.py)
    show_ids = {"뮤직뱅크": "kbs", "쇼! 음악중심": "mbc", "인기가요": "sbs", "엠카운트다운": "mnet", "더쇼": "theshow", "쇼챔피언": "showchamp"}
    for d, n in MUSIC_SHOWS:
        k = f"h-{d}-{show_ids[n]}"
        events[k] = {"d": d, "t": f"{n} 본방", "c": "music", "n": n, "k": "live"}
        seen.add(k)
    for d, t, c in HISTORY:
        k = f"h-{d}-{hashlib.md5(t.encode()).hexdigest()[:8]}"
        events[k] = {"d": d, "t": t, "c": c}
        seen.add(k)

    group_music(events, descs)
    for k, e in EXTRA_EVENTS.items():
        events[k] = dict(e)
        seen.add(k)

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("events", {})
        except Exception:
            old = {}

    added = [k for k in events if k not in old]
    changed = [k for k in events if k in old and old[k] != events[k]]
    removed = [k for k in old if k not in events]
    # 캘린더에서 아예 사라진 일정은 체크 기록이 있을 수 있으니 유지하고,
    # 캘린더에 남아 있지만 규칙상 오프가 아니게 된 일정만 목록에서 뺀다
    kept = [k for k in removed if k not in seen]
    dropped = [k for k in removed if k in seen]
    for k in kept:
        events[k] = old[k]

    ordered = dict(sorted(events.items(), key=lambda kv: (kv[1]["d"], kv[1]["t"])))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps({"syncedAt": datetime.now(KST).isoformat(timespec="seconds"), "events": ordered},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print(f"오프 일정 {len(events)}개 (새로 추가 {len(added)}, 변경 {len(changed)}, 제외 {len(dropped)}, 사라짐 {len(kept)})")
    for k in added:
        print(f"  + {events[k]['d']} [{events[k]['c']}] {events[k]['t']}")
    for k in changed:
        print(f"  ~ {events[k]['d']} [{events[k]['c']}] {events[k]['t']}")
    for k in dropped:
        print(f"  x {old[k]['d']} {old[k]['t']} (오프 아님으로 제외)")
    for k in kept:
        print(f"  - {old[k]['d']} {old[k]['t']} (캘린더에서 사라짐, 목록에는 유지)")


if __name__ == "__main__":
    main()
