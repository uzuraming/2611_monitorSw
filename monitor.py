# -*- coding: utf-8 -*-
"""
札幌 大通公園 イルミナスイッチ / マットセンサー 監視

・LAST_MENU_FILE (演奏実態) を CHECK_INTERVAL 秒ごとに読み、
  menu に "sw" または "signal" が含まれる新しい記録を「反応」とみなす。
・監視時間 (16:00〜22:00) 中に TIMEOUT 秒以上反応がなければ『警告』メール。
  異常継続中は REMIND_INTERVAL 秒ごとに再通知、反応が再開したら【復旧】メール。
・mail_list.json の mail_time (16:30, 18:00, 20:00, 22:00) に
  to_mail へ【定時報告】(反応回数・無反応区間・直近ログ) を送信。
  集計は status.log から行うので、監視プログラムの再起動に影響されない。
"""
import html
import json
import time
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

from sendMail import sendMail, DESCRIPTION

# ---------------------------------------------------------------- 設定
LAST_MENU_FILE = Path("/run/2506_music/last-menu")
STATUS_LOG = Path("/opt/2506_music/log/status.log")
MAIL_LIST_PATHS = [
    Path("/opt/2611_monitorSw/mail_list.json"),
    Path(__file__).resolve().with_name("mail_list.json"),
]

SITE_NAME = "札幌SW"
CHECK_INTERVAL = 5          # last-menu を読む間隔 (秒)。sw4 の menu は約26秒で切り替わるので十分短く
TIMEOUT = 240               # この秒数反応がなければ異常 (4分)
REMIND_INTERVAL = 600       # 異常継続中の再通知間隔 (秒)。0 なら再通知しない
ACTIVE_START_HOUR = 16      # 監視開始 16:00
ACTIVE_END_HOUR = 22        # 監視終了 22:00
REPORT_DELAY = 60           # 定時報告は指定時刻の この秒数後 に送信 (時報などのログ書き込みを待つため)
REPORT_GRACE = 300          # 定時報告の送信猶予 (秒)。この間に送れなければその回はスキップ
LOG_TAIL_LINES = 30

# mail_list.json に設定が無い場合の既定値
DEFAULT_ALERT_MAIL = ["nada@zigenls.com", "motoharu.ando@s-imf.co.jp"]
DEFAULT_REPORT_TIMES = ["16:30", "18:00", "20:00", "22:00"]

LogEntry = namedtuple("LogEntry", "dt level msg raw")


# ---------------------------------------------------------------- 共通
def log(*args):
    print(datetime.now().strftime("%Y/%m/%d %H:%M:%S"), *args, flush=True)


def is_trigger(menu):
    """演奏実態として正常扱いする menu か"""
    return "sw" in menu or "signal" in menu


def fmt_dur(sec):
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    out = (f"{h}時間" if h else "") + (f"{m}分" if m else "") + (f"{s}秒" if s or not (h or m) else "")
    return out


def fmt_t(dt):
    return dt.strftime("%H:%M:%S")


def window_of(now):
    start = now.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0)
    end = now.replace(hour=ACTIVE_END_HOUR, minute=0, second=0, microsecond=0)
    return start, end


def in_window(now):
    start, end = window_of(now)
    return start <= now < end


def load_config():
    for p in MAIL_LIST_PATHS:
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    log("mail_list.json が読めません。既定値を使用します")
    return {}


def alert_recipients(cfg):
    return cfg.get("alert_mail") or cfg.get("to_mail") or DEFAULT_ALERT_MAIL


def report_recipients(cfg):
    return cfg.get("to_mail") or DEFAULT_ALERT_MAIL


def report_times(cfg):
    return cfg.get("mail_time") or DEFAULT_REPORT_TIMES


def tail_text(path, lines=LOG_TAIL_LINES, max_bytes=65536):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except OSError as e:
        return f"(ログ取得エラー: {e})"


def file_mtime(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


# ---------------------------------------------------------------- status.log 集計
def read_status_entries(start, end, path=None):
    """status.log から start <= dt <= end の行を返す。形式: 2026/11/23,17:55:32,3,sw4"""
    path = path or STATUS_LOG
    prefix = start.strftime("%Y/%m/%d,")
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(prefix):
                continue
            parts = line.rstrip("\n").split(",", 3)
            if len(parts) < 4:
                continue
            try:
                dt = datetime.strptime(parts[0] + " " + parts[1], "%Y/%m/%d %H:%M:%S")
            except ValueError:
                continue
            if start <= dt <= end:
                entries.append(LogEntry(dt, parts[2], parts[3], line.rstrip("\n")))
    return entries


def analyze(now, path=None):
    """本日 16:00 から now (最大 22:00) までの反応状況を集計"""
    start, end = window_of(now)
    cutoff = min(now, end)
    result = {"start": start, "cutoff": cutoff, "error": None,
              "sw": 0, "signal": 0, "events": [], "gaps": [], "long_gaps": [],
              "max_gap": None, "hourly": {}}
    for h in range(ACTIVE_START_HOUR, ACTIVE_END_HOUR):
        result["hourly"][h] = {"sw": 0, "signal": 0, "long": 0}
    if cutoff <= start:
        return result
    try:
        entries = read_status_entries(start, cutoff, path)
    except OSError as e:
        result["error"] = f"status.log 読み取りエラー: {e}"
        return result

    events = [e for e in entries if is_trigger(e.msg)]
    result["events"] = events
    for e in events:
        key = "sw" if "sw" in e.msg else "signal"
        result[key] += 1
        if e.dt.hour in result["hourly"]:
            result["hourly"][e.dt.hour][key] += 1

    # 監視開始〜最初の反応、反応〜反応、最後の反応〜現在 の間隔
    points = [(start, "監視開始")] + [(e.dt, e.msg) for e in events] + [(cutoff, "現在" if now < end else "監視終了")]
    for (a, am), (b, bm) in zip(points, points[1:]):
        sec = (b - a).total_seconds()
        gap = {"from": a, "from_msg": am, "to": b, "to_msg": bm, "sec": sec}
        result["gaps"].append(gap)
        if sec > TIMEOUT:
            result["long_gaps"].append(gap)
            if a.hour in result["hourly"]:
                result["hourly"][a.hour]["long"] += 1
    if result["gaps"]:
        result["max_gap"] = max(result["gaps"], key=lambda g: g["sec"])
    return result


# ---------------------------------------------------------------- HTML
STYLE = ("<style>"
         "body{font-family:sans-serif;font-size:14px}"
         "table{border-collapse:collapse;margin:6px 0}"
         "td,th{border:1px solid #999;padding:3px 8px;text-align:left}"
         "th{background:#eee}"
         ".ng{color:#c00;font-weight:bold}.ok{color:#080;font-weight:bold}"
         "pre{background:#f4f4f4;padding:6px;font-size:12px}"
         "</style>")


def h(s):
    return html.escape(str(s))


def kv_table(rows):
    out = "<table>"
    for k, v in rows:
        out += f"<tr><th>{h(k)}</th><td>{v}</td></tr>"
    return out + "</table>"


def html_page(title, inner):
    return (f"<html><head><meta charset='utf-8'>{STYLE}</head><body>"
            f"<h3>{h(title)}</h3>{inner}"
            f"<p style='color:#666;font-size:12px'>{h(DESCRIPTION)}</p></body></html>")


def log_block(lines=LOG_TAIL_LINES):
    return f"<h4>直近ログ (status.log 末尾 {lines} 行)</h4><pre>{h(tail_text(STATUS_LOG, lines))}</pre>"


def gaps_table(gaps):
    if not gaps:
        return f"<p class='ok'>{TIMEOUT // 60}分を超える無反応はありません</p>"
    out = "<table><tr><th>#</th><th>無反応区間</th><th>長さ</th><th>直前</th><th>直後</th></tr>"
    for i, g in enumerate(gaps, 1):
        out += (f"<tr><td>{i}</td><td>{fmt_t(g['from'])} 〜 {fmt_t(g['to'])}</td>"
                f"<td class='ng'>{fmt_dur(g['sec'])}</td>"
                f"<td>{h(g['from_msg'])}</td><td>{h(g['to_msg'])}</td></tr>")
    return out + "</table>"


def hourly_table(a):
    out = "<table><tr><th>時間帯</th><th>SW反応</th><th>時報</th><th>4分超の無反応</th></tr>"
    last_hour = a["cutoff"].hour if a["cutoff"].minute or a["cutoff"].second else a["cutoff"].hour - 1
    for hr, c in a["hourly"].items():
        if hr > last_hour:
            break
        long_cell = f"<span class='ng'>{c['long']}件</span>" if c["long"] else "0件"
        out += f"<tr><td>{hr:02d}:00〜{hr + 1:02d}:00</td><td>{c['sw']}回</td><td>{c['signal']}回</td><td>{long_cell}</td></tr>"
    return out + "</table>"


# ---------------------------------------------------------------- 監視本体
class Monitor:
    def __init__(self):
        self.started = datetime.now()
        self.seen_dt_epoch = None       # 最後に見た last-menu の dt_epoch
        self.last_event = None          # (datetime, menu) 最後の反応
        self.read_error = None          # last-menu 読み取りエラー内容
        self.read_error_since = None
        self.last_menu_raw = ""
        self.alert = None               # 異常中: {"since": datetime, "since_msg": str, "detected": datetime, "notified": datetime, "count": int}
        self.sent_reports = set()

    # --- last-menu 読み取り
    def poll_menu(self, now):
        try:
            raw = LAST_MENU_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
            menu = str(data.get("menu", ""))
            dt_epoch = float(data.get("dt_epoch", 0))
        except (OSError, ValueError, TypeError) as e:
            if self.read_error is None:
                self.read_error_since = now
                log(f"last-menu 読み取りエラー: {e}")
            self.read_error = str(e)
            return None
        if self.read_error is not None:
            log("last-menu 読み取り回復")
        self.read_error = None
        self.read_error_since = None
        self.last_menu_raw = raw.strip()

        if dt_epoch == self.seen_dt_epoch:
            return None
        self.seen_dt_epoch = dt_epoch
        if not is_trigger(menu):
            return None
        ev_dt = datetime.fromtimestamp(dt_epoch)
        if self.last_event is None or ev_dt > self.last_event[0]:
            self.last_event = (ev_dt, menu)
            return self.last_event
        return None

    def reference(self, now):
        """無反応時間の起点: 最後の反応 / 本日の監視開始 / プログラム起動 のうち最も新しいもの"""
        start, _ = window_of(now)
        cands = [(start, "監視開始"), (self.started, "監視プログラム起動")]
        if self.last_event:
            cands.append(self.last_event)
        return max(cands, key=lambda c: c[0])

    # --- 1回分の処理
    def step(self, now=None):
        now = now or datetime.now()
        event = self.poll_menu(now)
        if event:
            log(f"反応: {event[1]} ({fmt_t(event[0])})")

        if in_window(now):
            if self.alert and event:
                self.send_recovery(now, event)
                self.alert = None
            ref_dt, ref_msg = self.reference(now)
            idle = (now - ref_dt).total_seconds()
            if self.alert is None and idle > TIMEOUT:
                self.alert = {"since": ref_dt, "since_msg": ref_msg, "detected": now, "notified": now, "count": 1}
                self.send_alert(now, first=True)
            elif self.alert and REMIND_INTERVAL and (now - self.alert["notified"]).total_seconds() >= REMIND_INTERVAL:
                self.alert["notified"] = now
                self.alert["count"] += 1
                self.send_alert(now, first=False)
        elif self.alert:
            log(f"監視時間終了。異常継続のまま終了 ({fmt_t(self.alert['since'])} から無反応)")
            self.alert = None

        self.check_report(now)

    # --- メール
    def send_alert(self, now, first):
        a = self.alert
        idle = (now - a["since"]).total_seconds()
        stats = analyze(now)
        mtime = file_mtime(STATUS_LOG)
        if a["since_msg"] in ("監視開始", "監視プログラム起動"):
            last_str = f"{fmt_t(a['since'])} の{h(a['since_msg'])}以降、反応なし"
        else:
            last_str = f"{fmt_t(a['since'])} ({h(a['since_msg'])})"
        rows = [
            ("状態", f"<span class='ng'>SW・時報の反応が {fmt_dur(idle)} ありません</span>"),
            ("最終反応", last_str),
            ("判定基準", f"{fmt_dur(TIMEOUT)} 以上反応なしで異常"),
            ("検知時刻", now.strftime("%Y/%m/%d %H:%M:%S") + ("" if first else f" (第{a['count']}報 / 初回 {fmt_t(a['detected'])})")),
            ("本日の反応", f"SW {stats['sw']}回 / 時報 {stats['signal']}回 (16:00〜)"),
            ("本日の4分超の無反応", f"{len(stats['long_gaps'])}件 (今回を含む)"),
            ("status.log 最終更新", fmt_t(mtime) if mtime else "取得不可"),
            ("last-menu", h(self.last_menu_raw) if self.read_error is None
                else f"<span class='ng'>読み取りエラー ({h(self.read_error)}) {fmt_t(self.read_error_since)}〜</span>"),
        ]
        note = "<p>反応が再開すると【復旧】メールを送信します。"
        if REMIND_INTERVAL:
            note += f"継続中は {fmt_dur(REMIND_INTERVAL)} ごとに再通知します。"
        note += "</p>"
        title = f"{SITE_NAME} 反応なし {fmt_dur(idle)} (最終反応 {fmt_t(a['since'])})"
        if not first:
            title = f"[継続 第{a['count']}報] " + title
        body = html_page(title, kv_table(rows) + note + log_block())
        log(f"異常通知: {title}")
        sendMail(alert_recipients(load_config()), title, body, 1)

    def send_recovery(self, now, event):
        a = self.alert
        gap = (event[0] - a["since"]).total_seconds()
        rows = [
            ("状態", "<span class='ok'>反応再開</span>"),
            ("無反応区間", f"{fmt_t(a['since'])} 〜 {fmt_t(event[0])}"),
            ("無反応時間", f"<span class='ng'>{fmt_dur(gap)}</span>"),
            ("再開時の menu", h(event[1])),
            ("異常検知", f"{fmt_t(a['detected'])} (通知 {a['count']}回)"),
        ]
        title = f"【復旧】{SITE_NAME} 反応再開 (無反応 {fmt_dur(gap)} {fmt_t(a['since'])}〜{fmt_t(event[0])})"
        body = html_page(title, kv_table(rows) + log_block(15))
        log(f"復旧通知: {title}")
        sendMail(alert_recipients(load_config()), title, body, 0)

    def check_report(self, now):
        cfg = load_config()
        for t in report_times(cfg):
            try:
                hh, mm = map(int, t.split(":"))
            except ValueError:
                continue
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            key = target.strftime("%Y/%m/%d %H:%M")
            if key in self.sent_reports:
                continue
            send_at = target + timedelta(seconds=REPORT_DELAY)
            if send_at <= now < send_at + timedelta(seconds=REPORT_GRACE):
                self.sent_reports.add(key)
                self.send_report(target, cfg)

    def send_report(self, target, cfg):
        """target: 報告の基準時刻 (mail_time)。集計は target まで、送信は REPORT_DELAY 秒後"""
        a = analyze(target)
        long_n = len(a["long_gaps"])
        mg = a["max_gap"]
        rows = [
            ("集計期間", f"{a['start'].strftime('%Y/%m/%d %H:%M')} 〜 {a['cutoff'].strftime('%H:%M')}"),
            ("SW反応", f"{a['sw']}回"),
            ("時報", f"{a['signal']}回"),
            ("最長の無反応", f"{fmt_dur(mg['sec'])} ({fmt_t(mg['from'])}〜{fmt_t(mg['to'])})" if mg else "-"),
            (f"{TIMEOUT // 60}分超の無反応",
             f"<span class='ng'>{long_n}件</span>" if long_n else "<span class='ok'>0件</span>"),
        ]
        if a["error"]:
            rows.append(("エラー", f"<span class='ng'>{h(a['error'])}</span>"))
        if self.alert:
            rows.append(("現在の状態", f"<span class='ng'>異常継続中 ({fmt_t(self.alert['since'])} から無反応)</span>"))
        inner = (kv_table(rows)
                 + "<h4>時間帯別</h4>" + hourly_table(a)
                 + f"<h4>{TIMEOUT // 60}分を超える無反応の一覧</h4>" + gaps_table(a["long_gaps"])
                 + log_block())
        title = f"【定時報告】{SITE_NAME} {target.strftime('%m/%d %H:%M')} SW{a['sw']}回 / 4分超の無反応{long_n}件"
        log(f"定時報告: {title}")
        sendMail(report_recipients(cfg), title, html_page(title, inner), 0)


if __name__ == "__main__":
    log(f"監視開始 (判定 {TIMEOUT}秒 / 間隔 {CHECK_INTERVAL}秒 / 監視時間 {ACTIVE_START_HOUR}:00-{ACTIVE_END_HOUR}:00)")
    mon = Monitor()
    while True:
        try:
            mon.step()
        except Exception as e:  # 監視自体は止めない
            log(f"監視処理エラー: {e!r}")
        time.sleep(CHECK_INTERVAL)
