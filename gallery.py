"""필굿의 최신 '앞으로 일정 스케줄' 원문에서 오프 일정을 보완한다."""
import hashlib
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://gall.dcinside.com"
TITLE = "앞으로 일정 스케줄"
AUTHOR = "필굿"
DAY_LINE = re.compile(r"^\s*(\?)?\s*(\d{1,2})/(\d{1,2})\s+([월화수목금토일])\s+(.*)$")
ONLINE = re.compile(r"유튜브|라디오|SBS파워FM|MBC every1|방송|온라인|영상통화|영통", re.I)


class GalleryError(RuntimeError):
    pass


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read().decode("utf-8")


def list_candidates(html):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select_one(".gall_list"):
        raise GalleryError("갤러리 목록 접근 실패")
    candidates = []
    for row in soup.select("tr.ub-content"):
        writer = row.select_one(".gall_writer")
        link = row.select_one(".gall_tit a")
        date = row.select_one(".gall_date")
        number = row.select_one(".gall_num")
        if not all((writer, link, date, number)):
            continue
        nick = writer.get("data-nick") or writer.get_text(strip=True)
        # 아이콘과 댓글 수는 제목의 일부가 아니다.
        for tag in link.select("em, .reply_num"):
            tag.decompose()
        if nick != AUTHOR or link.get_text(strip=True) != TITLE:
            continue
        if not re.fullmatch(r"\d+", number.get_text(strip=True)):
            continue
        try:
            posted = datetime.strptime(date["title"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        except (KeyError, ValueError) as exc:
            raise GalleryError("일정 글 게시 시각 확인 실패") from exc
        candidates.append({"number": int(number.get_text(strip=True)), "posted": posted})
    return candidates


def read_post(candidate, fetch=fetch_html):
    number = candidate["number"]
    url = f"{BASE}/mgallery/board/view/?id=fromis&no={number}"
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
        title = soup.select_one(".title_subject")
        writer = soup.select_one(".gall_writer")
        date = soup.select_one(".gall_date")
        body = soup.select_one(".write_div")
        if not all((title, writer, date, body)):
            raise ValueError("본문 또는 게시 정보 누락")
        nick = writer.get("data-nick") or (writer.select_one(".nickname") or writer).get_text(strip=True)
        if title.get_text(strip=True) != TITLE or nick != AUTHOR:
            raise ValueError("제목/작성자 불일치")
        posted = datetime.strptime(date.get_text(strip=True), "%Y.%m.%d %H:%M:%S").replace(tzinfo=KST)
        if candidate.get("posted") and candidate["posted"] != posted:
            raise ValueError("목록/본문 게시 시각 불일치")
        # 링크 미리보기의 썸네일은 본문 일정 이미지가 아니다.
        for preview in body.select(".og-div"):
            preview.decompose()
        if body.select("img, iframe, video"):
            raise ValueError("텍스트로 검증할 수 없는 이미지/영상 본문")
        # 인라인 span 경계에서 날짜·시간이 잘리지 않도록 블록에만 줄바꿈.
        for tag in body.find_all(["br", "div", "p", "li"]):
            tag.insert_after("\n")
        text = body.get_text().strip()
        if not text or not re.search(r"[ㅡ─\-]{8,}", text):
            raise ValueError("일정 본문 구분선 누락")
        sections = re.split(r"[ㅡ─\-]{8,}", text)
        if len(sections) < 3:
            raise ValueError("일정 본문 끝 구분선 누락")
        schedule = sections[1].strip()
        if not any(DAY_LINE.match(line) for line in schedule.splitlines()):
            raise ValueError("일정 본문 날짜 누락")
        morning = (posted + timedelta(days=1)).date() if posted.hour >= 22 else posted.date()
        return {"number": number, "postedAt": posted.isoformat(timespec="seconds"),
                "morningDate": morning.isoformat(), "url": url, "body": text, "schedule": schedule}
    except Exception as exc:
        raise GalleryError(f"최신 글은 확인했으나 전체 본문 확보 실패 (글 {number}): {exc}") from exc


def find_latest(fetch=fetch_html):
    errors = []
    # 개념글에서 찾으면 전체글/검색엔진은 사용하지 않는다.
    for mode, label in (("recommend", "개념글"), ("", "전체글")):
        query = {"id": "fromis", "s_type": "search_subject", "s_keyword": TITLE}
        if mode:
            query["exception_mode"] = mode
        try:
            candidates = list_candidates(fetch(f"{BASE}/mgallery/board/lists/?{urlencode(query)}"))
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if candidates:
            latest = max(candidates, key=lambda c: (c["posted"], c["number"]))
            return {**read_post(latest, fetch), "foundIn": label}
        errors.append(f"{label}: 정확한 제목/작성자의 글 없음")

    # 두 목록이 모두 실패한 경우에만 검색엔진 보조. 검색 요약은 본문으로 사용하지 않는다.
    query = urlencode({"q": f'site:gall.dcinside.com/mgallery/board/view "{TITLE}" "{AUTHOR}" "fromis"', "format": "rss"})
    try:
        feed = ET.fromstring(fetch(f"https://www.bing.com/search?{query}"))
        posts = []
        for item in feed.findall(".//item"):
            link = item.findtext("link", "")
            match = re.search(r"https://gall\.dcinside\.com/mgallery/board/view/\?id=fromis&no=(\d+)", link)
            if match:
                try:
                    posts.append(read_post({"number": int(match.group(1))}, fetch))
                except GalleryError:
                    continue
        if posts:
            return {**max(posts, key=lambda p: (p["postedAt"], p["number"])), "foundIn": "검색엔진"}
        errors.append("검색엔진: 검증 가능한 원문 없음")
    except Exception as exc:
        errors.append(f"검색엔진: {exc}")
    raise GalleryError("; ".join(errors))


def offline_events(post, today, classify):
    events = {}
    posted = datetime.fromisoformat(post["postedAt"])
    count = 0
    for line in post["schedule"].splitlines():
        line = line.strip()
        if not line:
            continue
        match = DAY_LINE.match(line)
        if not match:
            if re.match(r"^[?\d]", line):
                raise GalleryError(f"해석할 수 없는 일정 줄: {line}")
            continue  # 부연 설명
        uncertain, month, day, weekday, rest = match.groups()
        year = posted.year + (int(month) == 1 and posted.month == 12)
        date = datetime(year, int(month), int(day), tzinfo=KST)
        if "월화수목금토일"[date.weekday()] != weekday:
            raise GalleryError(f"날짜/요일 불일치: {line}")
        count += 1
        if uncertain or ONLINE.search(rest):
            continue
        time = re.match(r"(\d{1,2})시(?:\s*(\d{1,2})분)?\s*", rest)
        clock = None
        if time:
            hour, minute = int(time[1]), int(time[2] or 0)
            if hour > 23 or minute > 59:
                raise GalleryError(f"잘못된 시간: {line}")
            clock = f"{hour:02}:{minute:02}"
            rest = rest[time.end():]
        title = re.sub(r"[\[\]]", "", rest)
        title = re.sub(r"\s*:\s*", " ", title).strip()
        # 도시 앞에 IN을 넣어 기존 사이트의 해외 투어 분류와 맞춘다.
        title = re.sub(r"\s+(TAIPEI|TOKYO|HONG KONG)(?=\s|$)", r" IN \1", title, flags=re.I)
        cat = classify(title)
        if cat is None:
            continue
        ymd = date.date().isoformat()
        if ymd < today:
            continue
        key = "dc-" + ymd + "-" + hashlib.sha256(title.encode()).hexdigest()[:12]
        event = {"d": ymd, "t": title, "c": cat, "u": post["url"], "source": "gallery",
                 "gu": post["url"]}
        if cat == "concert":
            event["sc"] = False  # 원문에 없는 사운드체크를 생성하지 않는다.
        if clock:
            event["time"] = clock
        events[key] = event
    if not count:
        raise GalleryError("검증 가능한 일정 날짜 없음")
    return events


def identity(event):
    title = event["t"]
    if event["c"] == "musical" and "헬스키친" in title and "지원" in title:
        title = "헬스키친 지원"
    else:
        title = re.sub(r"\bfromis_9\b|\b20\d\d\b|[\[\]]", "", title, flags=re.I)
    return event["d"], event["c"], re.sub(r"[\W_]", "", title).casefold()


def merge_events(events, additions, old):
    """캘린더와 겹치는 일정은 기존 ID를 유지해 참석 기록을 보존한다."""
    by_identity = {}
    for key, event in events.items():
        by_identity.setdefault(identity(event), []).append(key)
    old_gallery = {identity(e): key for key, e in old.items() if e.get("source") == "gallery"}
    for key, event in additions.items():
        matched = by_identity.get(identity(event), [])
        if matched:
            # 같은 날 낮공·밤공이 있으면 임의로 한 회차에 시간을 덮어쓰지 않는다.
            for existing in matched:
                if event.get("gu"):
                    events[existing]["gu"] = event["gu"]
                if len(matched) == 1 and event.get("time"):
                    events[existing]["time"] = event["time"]
            # 다음에 캘린더가 채워져도 최초 갤러리 ID를 계속 사용한다.
            previous = old_gallery.get(identity(event))
            if previous and len(matched) == 1:
                events[previous] = {**events.pop(matched[0]), "source": "gallery"}
                if event.get("sc") is False:
                    events[previous]["sc"] = False
        else:
            events[old_gallery.get(identity(event), key)] = event


def sync_gallery(events, old, previous=None, now=None, fetch=fetch_html, classify=None):
    now = now or datetime.now(KST)
    meta = {**(previous or {}), "checkedAt": now.isoformat(timespec="seconds")}
    today = now.date().isoformat()
    try:
        post = find_latest(fetch)
        if previous and previous.get("postedAt") and (post["postedAt"], post["number"]) < (previous["postedAt"], previous.get("number", 0)):
            raise GalleryError("이전 확인 원문보다 오래된 글이 반환됨")
        additions = offline_events(post, today, classify)
        merge_events(events, additions, old)
        meta = {k: v for k, v in post.items() if k not in ("body", "schedule")}
        meta.update(status="ok", checkedAt=now.isoformat(timespec="seconds"), eventCount=len(additions))
    except Exception as exc:
        # 원문 실패를 '일정 없음'으로 취급하지 않는다. 마지막 확인 일정을 유지한다.
        meta.update(status="failed", error=str(exc))
        cached = {key: dict(event) for key, event in old.items() if event.get("source") == "gallery"}
        merge_events(events, cached, old)
        for key, event in old.items():
            if key in events and event.get("gu"):
                for field in ("gu", "time"):
                    if field in event:
                        events[key][field] = event[field]
    # 성공 시 최신 글에서 빠진 미래 갤러리 일정은 삭제, 지난 기록은 유지.
    past = {key: dict(event) for key, event in old.items()
            if event.get("source") == "gallery" and event["d"] < today}
    # 공연이 지난 뒤 영상 캘린더에 올라와도 같은 ID로 합쳐 기록을 유지한다.
    merge_events(events, past, old)
    return meta
