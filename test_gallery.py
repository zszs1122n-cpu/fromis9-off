import unittest
from datetime import datetime
from unittest.mock import patch

import gallery
from sync import classify


SCHEDULE = """10/7 수 19시 유튜브 [워크맨 워크돌 EP.29] : 지원
10/8 목 19시 [호서대학교 아산캠퍼스 축제]
10/8 목 [아주대학교 축제]
10/9 금 14시 [뮤지컬 헬스키친] : 지원
? 10/12 월 17시 유튜브 [이단장 시즌2 EP.7] : 채영
10/12 월 22시30분 MBC every1 [계약결혼 3회] : 채영 MC
10/25 일 [2026 fromis_9 ASIA TOUR TOMORROW GLOW.] TAIPEI
11/14 토 [2026 fromis_9 ASIA TOUR TOMORROW GLOW.] TOKYO 1일차
11/15 일 [2026 fromis_9 ASIA TOUR TOMORROW GLOW.] TOKYO 2일차
11/22 일 [2026 fromis_9 ASIA TOUR TOMORROW GLOW.] HONG KONG"""
POST = {"postedAt": "2026-10-06T00:07:31+09:00", "number": 3068576,
        "url": "https://gall.dcinside.com/mgallery/board/view/?id=fromis&no=3068576",
        "foundIn": "개념글", "schedule": SCHEDULE}
NOW = datetime(2026, 10, 7, 9, tzinfo=gallery.KST)


def post_html(schedule=SCHEDULE, end=True, media=""):
    lines = "".join(f"<div><span>{line}</span></div>" for line in schedule.splitlines())
    return ('<span class="title_subject">앞으로 일정 스케줄</span>'
            '<div class="gall_writer" data-nick="필굿"></div>'
            '<span class="gall_date">2026.10.06 00:07:31</span>'
            '<div class="write_div"><div>ㅡㅡㅡㅡㅡㅡㅡㅡ</div>' + lines +
            ('<div>ㅡㅡㅡㅡㅡㅡㅡㅡ</div>' if end else '') + media + '</div>')


def listing(rows):
    return '<table class="gall_list">' + ''.join(
        f'<tr class="ub-content"><td class="gall_num">{no}</td>'
        f'<td class="gall_tit"><a><em></em>{title}</a></td>'
        f'<td class="gall_writer" data-nick="{nick}"></td>'
        f'<td class="gall_date" title="{date}"></td></tr>'
        for no, title, nick, date in rows) + '</table>'


class GalleryTests(unittest.TestCase):
    def test_real_schedule_filters_broadcasts_and_has_seven_offline_entries(self):
        events = gallery.offline_events(POST, '2026-10-07', classify)
        self.assertEqual(len(events), 7)
        self.assertEqual(sum(e['c'] == 'campus' for e in events.values()), 2)
        self.assertEqual(sum(e['c'] == 'concert' for e in events.values()), 4)
        self.assertEqual(next(e for e in events.values() if e['c'] == 'musical')['time'], '14:00')
        self.assertTrue(all(e.get('sc') is False for e in events.values() if e['c'] == 'concert'))

    def test_exact_author_title_and_newest_timestamp_number(self):
        rows = [(10, gallery.TITLE, gallery.AUTHOR, '2026-10-06 00:07:31'),
                (11, gallery.TITLE, gallery.AUTHOR, '2026-10-06 00:07:31'),
                (12, gallery.TITLE + ' 수정', gallery.AUTHOR, '2026-10-07 01:00:00'),
                (13, gallery.TITLE, '다른 작성자', '2026-10-07 01:00:00')]
        candidates = gallery.list_candidates(listing(rows))
        self.assertEqual([c['number'] for c in candidates], [10, 11])
        self.assertEqual(max(candidates, key=lambda c: (c['posted'], c['number']))['number'], 11)

    def test_fallback_order_and_stop_after_recommended_match(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if '/view/' in url:
                return post_html()
            return listing([(3068576, gallery.TITLE, gallery.AUTHOR, '2026-10-06 00:07:31')])
        post = gallery.find_latest(fetch)
        self.assertEqual(post['foundIn'], '개념글')
        self.assertEqual(len(calls), 2)
        self.assertIn('exception_mode=recommend', calls[0])
        calls.clear()
        def fetch_empty(url):
            calls.append(url)
            if 'bing.com' in url:
                return '<rss><channel/></rss>'
            return listing([])
        with self.assertRaises(gallery.GalleryError):
            gallery.find_latest(fetch_empty)
        self.assertEqual(len(calls), 3)
        self.assertIn('exception_mode=recommend', calls[0])
        self.assertNotIn('exception_mode', calls[1])
        self.assertIn('bing.com', calls[2])

    def test_found_post_incomplete_body_is_reported_without_older_fallback(self):
        def fetch(url):
            if '/view/' in url:
                return post_html(end=False)
            return listing([(3068576, gallery.TITLE, gallery.AUTHOR, '2026-10-06 00:07:31')])
        with self.assertRaisesRegex(gallery.GalleryError, '최신 글은 확인했으나 전체 본문 확보 실패'):
            gallery.find_latest(fetch)

    def test_content_images_rejected_preview_thumbnails_ignored(self):
        candidate = {'number': 3068576}
        with self.assertRaises(gallery.GalleryError):
            gallery.read_post(candidate, lambda _: post_html(media='<img src="schedule.png">'))
        result = gallery.read_post(candidate, lambda _: post_html(media='<div class="og-div"><img src="preview.png"></div>'))
        self.assertIn('호서대학교', result['schedule'])

    def test_dates_weekdays_uncertainty_and_year_rollover(self):
        with self.assertRaises(gallery.GalleryError):
            gallery.offline_events({**POST, 'schedule': '10/8 금 [아주대학교 축제]'}, '2026-10-07', classify)
        events = gallery.offline_events({**POST, 'schedule': '? 10/8 목 [아주대학교 축제]'}, '2026-10-07', classify)
        self.assertEqual(events, {})
        events = gallery.offline_events({**POST, 'postedAt': '2026-12-31T23:30:00+09:00', 'schedule': '1/1 금 [뮤지컬 헬스키친] : 지원'}, '2027-01-01', classify)
        self.assertEqual(next(iter(events.values()))['d'], '2027-01-01')

    def test_existing_calendar_id_preserved_and_musical_not_duplicated(self):
        existing = {'calendar-id': {'d': '2026-10-09', 't': '뮤지컬 헬스키친 공연 지원', 'c': 'musical'}}
        gallery.merge_events(existing, gallery.offline_events(POST, '2026-10-07', classify), {})
        self.assertEqual(len(existing), 7)
        self.assertEqual(existing['calendar-id']['time'], '14:00')

    def test_gallery_id_preserved_when_calendar_catches_up(self):
        old = gallery.offline_events(POST, '2026-10-07', classify)
        key, event = next((k, e) for k, e in old.items() if 'TAIPEI' in e['t'])
        existing = {'new-calendar-id': {**event, 't': 'ASIA TOUR [TOMORROW GLOW.] IN TAIPEI', 'source': 'calendar'}}
        gallery.merge_events(existing, {key: event}, old)
        self.assertEqual(list(existing), [key])
        self.assertEqual(existing[key]['source'], 'gallery')
        self.assertIs(existing[key]['sc'], False)

    def test_failure_preserves_snapshot_and_success_removes_omitted_future_only(self):
        old = gallery.offline_events(POST, '2026-10-07', classify)
        old['past'] = {'d': '2026-10-01', 't': '지난 축제', 'c': 'campus', 'source': 'gallery'}
        current = {}
        with patch.object(gallery, 'find_latest', side_effect=gallery.GalleryError('접근 실패')):
            meta = gallery.sync_gallery(current, old, POST, NOW, classify=classify)
        self.assertEqual(current, old)
        self.assertEqual(meta['status'], 'failed')
        current = {}
        with patch.object(gallery, 'find_latest', return_value={**POST, 'schedule': '10/9 금 14시 [뮤지컬 헬스키친] : 지원'}):
            meta = gallery.sync_gallery(current, old, POST, NOW, classify=classify)
        self.assertEqual(len(current), 2)
        self.assertIn('past', current)
        self.assertEqual(meta['status'], 'ok')

    def test_older_search_snapshot_cannot_replace_last_verified_post(self):
        old = gallery.offline_events(POST, '2026-10-07', classify)
        current = {}
        with patch.object(gallery, 'find_latest', return_value={**POST, 'postedAt': '2026-10-01T00:07:31+09:00'}):
            meta = gallery.sync_gallery(current, old, POST, NOW, classify=classify)
        self.assertEqual(current, old)
        self.assertEqual(meta['status'], 'failed')

    def test_past_gallery_event_merges_when_video_calendar_catches_up(self):
        event = {'d': '2026-10-01', 't': '아주대학교 축제', 'c': 'campus',
                 'source': 'gallery', 'gu': POST['url']}
        current = {'new-calendar-id': {'d': '2026-10-01', 't': '아주대학교 축제', 'c': 'campus'}}
        with patch.object(gallery, 'find_latest', return_value=POST):
            gallery.sync_gallery(current, {'original-id': event}, POST, NOW, classify=classify)
        self.assertIn('original-id', current)
        self.assertNotIn('new-calendar-id', current)
        self.assertEqual(sum(e['d'] == '2026-10-01' for e in current.values()), 1)

    def test_night_post_counts_for_following_morning(self):
        html = post_html().replace('2026.10.06 00:07:31', '2026.10.05 23:30:00')
        post = gallery.read_post({'number': 1}, lambda _: html)
        self.assertEqual(post['morningDate'], '2026-10-06')


if __name__ == '__main__':
    unittest.main()
