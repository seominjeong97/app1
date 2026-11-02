"""커플 다이어리 — 실행: streamlit run app.py

GitHub에 app.py, requirements.txt를 올리고 Streamlit Community Cloud에서 배포합니다.
배포된 앱 URL을 QR코드로 공유하면 두 사람이 같은 앱에 접속합니다.

배포 설정: Streamlit 앱 Settings > Secrets에 아래 TOML을 등록하세요.
실제 비밀번호는 GitHub에 올리지 마세요. 두 PIN은 서로 다르게 설정하세요.

[couple]
name1 = "나"
name2 = "너"
pin1 = "충분히-긴-첫번째-비밀번호"
pin2 = "충분히-긴-두번째-비밀번호"
app_url = "https://YOUR-APP.streamlit.app"

[supabase]
url = "https://YOUR-PROJECT.supabase.co"
service_role_key = "YOUR-SERVER-ONLY-SERVICE-ROLE-KEY"

Supabase 프로젝트의 SQL Editor에서 아래 SQL을 한 번 실행하세요.
이 앱은 하나의 커플 전용입니다. 다른 커플은 별도 앱/저장소를 사용하세요.

create table public.couple_diary (
  id text primary key,
  author text not null check (author in ('p1', 'p2')),
  mood text not null,
  body text not null,
  created_at timestamptz not null default now(),
  read_at timestamptz
);
alter table public.couple_diary enable row level security;
revoke all on public.couple_diary from anon, authenticated;

서비스 역할 키는 서버 Secrets에서만 사용합니다. 공개 저장소/브라우저에 넣지 마세요.
Supabase 설정이 모두 없으면 SQLite 체험 모드입니다. Cloud 재시작/재배포 시
체험 데이터는 사라질 수 있으므로 실제 사용 시 반드시 Supabase를 설정하세요.
두 사람 비밀번호 설정이 없으면 저장소에 접근하지 않고 안내만 표시합니다.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import hmac
import io
import sqlite3
import time
import uuid

import requests
import streamlit as st
import qrcode

st.set_page_config(page_title="우리의 하루", page_icon="💌", layout="centered", initial_sidebar_state="collapsed")
KST = ZoneInfo("Asia/Seoul")

def config(section):
    try:
        return dict(st.secrets.get(section, {}))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return {}

couple, sb = config("couple"), config("supabase")
names = {"p1": str(couple.get("name1", "나")), "p2": str(couple.get("name2", "너"))}
remote = bool(sb.get("url") and sb.get("service_role_key"))

st.markdown('''<style>
#MainMenu, footer {visibility:hidden;} header {background:transparent!important;}
.block-container {max-width:470px;padding:1.0rem .9rem .4rem!important;}
[data-testid="stAppViewContainer"] {background:#fff9f7;}
[data-testid="stVerticalBlock"] {gap:.45rem;}
h1 {font-size:1.6rem!important;letter-spacing:-1px;padding:0!important;}
p {margin-bottom:.2rem;} [data-testid="stCaptionContainer"] {font-size:.76rem;}
.stButton button, .stFormSubmitButton button {border-radius:14px;min-height:38px;}
[data-testid="stTextArea"] textarea {font-size:16px!important;}
[data-baseweb="select"] {font-size:16px;}
[data-testid="stForm"] {padding:.65rem;border-radius:18px;background:white;}
.diary {background:#fff;border:1px solid #f0dfda;border-radius:16px;padding:10px 12px;
height:108px;overflow-y:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.5;}
@media(max-width:480px){.block-container{padding-top:.65rem!important;}}
</style>''', unsafe_allow_html=True)
st.title("💌 우리의 하루")
st.caption("기분 한 조각, 마음 한 줄. 둘이 함께 쓰는 작은 일기")

pins = {"p1": str(couple.get("pin1", "")), "p2": str(couple.get("pin2", ""))}
if not all(pins.values()) or pins["p1"] == pins["p2"]:
    st.info("먼저 앱 Settings → Secrets에 서로 다른 pin1·pin2를 설정해주세요. 설정 방법과 저장소 SQL은 app.py 맨 위에 있습니다.")
    st.stop()
if bool(sb.get("url")) != bool(sb.get("service_role_key")):
    st.error("Supabase url과 service_role_key를 모두 설정해주세요.")
    st.stop()

if "user" not in st.session_state:
    with st.form("login"):
        who = st.selectbox("누구인가요?", ["p1", "p2"], format_func=lambda x: names[x])
        pin = st.text_input("내 비밀번호", type="password")
        enter = st.form_submit_button("우리 공간 들어가기 💗", use_container_width=True)
    if enter:
        now = time.time()
        if now < st.session_state.get("retry_after", 0):
            st.error("잠시 후 다시 시도해주세요.")
        elif hmac.compare_digest(pin.encode(), pins[who].encode()):
            st.session_state.user = who
            st.session_state.failures = 0
            st.rerun()
        else:
            st.session_state.failures = st.session_state.get("failures", 0) + 1
            st.session_state.retry_after = now + min(60, 2 ** min(st.session_state.failures, 6))
            st.error("비밀번호를 확인해주세요.")
    st.caption("각자의 비밀번호로 접속하세요. 비밀번호는 서로 공유하지 않는 것을 권장해요.")
    st.stop()

user = st.session_state.user
other = "p2" if user == "p1" else "p1"

def db():
    con = sqlite3.connect(str(Path(__file__).with_name("diary_demo.sqlite3")), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS couple_diary (id TEXT PRIMARY KEY, author TEXT NOT NULL, mood TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT)")
    return con

def api(method, params=None, payload=None):
    url = str(sb["url"]).rstrip("/") + "/rest/v1/couple_diary"
    key = str(sb["service_role_key"])
    response = requests.request(method, url, params=params, json=payload, timeout=15,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "return=minimal"})
    response.raise_for_status()
    return response.json() if response.content else None

def entries():
    if remote:
        return api("GET", {"select": "*", "order": "created_at.desc,id.desc", "limit": "100"})
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM couple_diary ORDER BY created_at DESC,id DESC LIMIT 100")]

def save(mood, body, entry_id):
    row = dict(id=entry_id, author=user, mood=mood, body=body,
               created_at=datetime.now(timezone.utc).isoformat(), read_at=None)
    if remote:
        # UUID 재사용 시 중복 제출을 무시합니다.
        key = str(sb["service_role_key"])
        response = requests.post(str(sb["url"]).rstrip("/") + "/rest/v1/couple_diary",
            json=row, timeout=15, headers={"apikey": key, "Authorization": f"Bearer {key}",
            "Prefer": "resolution=ignore-duplicates,return=minimal"})
        response.raise_for_status()
    else:
        with db() as con:
            con.execute("INSERT OR IGNORE INTO couple_diary VALUES (?,?,?,?,?,?)", tuple(row.values()))

def mark_read(row):
    if row["author"] != other or row.get("read_at"):
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    if remote:
        api("PATCH", {"id": f"eq.{row['id']}", "author": f"eq.{other}", "read_at": "is.null"}, {"read_at": timestamp})
    else:
        with db() as con:
            con.execute("UPDATE couple_diary SET read_at=? WHERE id=? AND author=? AND read_at IS NULL", (timestamp, row["id"], other))

def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST).strftime("%m/%d %H:%M")

# 상단은 화면 전환 없이 새로고침·공유·로그아웃만 제공합니다.
a, b, c = st.columns([5, 1, 1])
a.caption(f"{names[user]}님의 공간 · {'연결됨' if remote else '⚠️ 임시 체험 저장'}")
if b.button("↻", help="상대의 최신 일기와 읽음 상태 새로고침", use_container_width=True):
    st.rerun()
with c.popover("⚙️", use_container_width=True):
    st.caption("앱 공유 · 설정")
    app_url = str(couple.get("app_url", "")).strip()
    if app_url.startswith("https://"):
        image = qrcode.make(app_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        st.image(buffer.getvalue(), width=190)
        st.link_button("공유 주소 열기", app_url)
        st.caption("QR에는 앱 주소만 들어갑니다. 접속 후 본인 비밀번호로 로그인하세요.")
    else:
        st.caption("Secrets의 couple.app_url에 배포 주소를 넣으면 QR이 표시됩니다.")
    if not remote:
        st.warning("체험 데이터는 재시작·재배포 시 사라질 수 있습니다. 실제 일기는 Supabase에 저장하세요.")
    if st.button("로그아웃", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.session_state.setdefault("draft_id", str(uuid.uuid4()))
with st.form("write", clear_on_submit=True):
    mood = st.select_slider("오늘의 기분", options=["🥲 속상해", "😴 지쳤어", "😌 평온해", "🥰 행복해", "💗 보고싶어"], value="😌 평온해")
    body = st.text_area("오늘의 한 줄", placeholder="오늘 너에게 들려주고 싶은 이야기…", height=82, max_chars=500, label_visibility="collapsed")
    submitted = st.form_submit_button("마음 남기기 💌", use_container_width=True, type="primary")
if submitted:
    if not body.strip():
        st.warning("이야기를 한 줄 적어주세요.")
    else:
        try:
            save(mood, body.strip(), st.session_state.draft_id)
            st.session_state.draft_id = str(uuid.uuid4())
            st.toast("마음을 남겼어요 💗")
        except requests.RequestException:
            # 원문과 UUID를 유지해 실패 후 재시도할 수 있게 합니다.
            st.session_state.failed_body = body
            st.error("저장 결과를 확인하지 못했어요. 아래 원문을 복사해 새로고침 후 목록을 확인해주세요.")
        except sqlite3.Error:
            st.session_state.failed_body = body
            st.error("저장에 실패했어요. 아래 원문을 복사해 다시 시도해주세요.")
if st.session_state.get("failed_body"):
    with st.expander("저장 실패 원문 복구"):
        st.code(st.session_state.failed_body, language=None)
        if st.button("복구 원문 닫기"):
            del st.session_state.failed_body
            st.rerun()

@st.fragment(run_every="20s")
def inbox():
    try:
        rows = entries()
        if not rows:
            st.caption("아직 비어 있어요. 첫 번째 마음을 남겨보세요 🌷")
            return
        # 한 화면에서 최신 100개의 일기를 선택해 읽습니다.
        by_id = {row["id"]: row for row in rows}
        def label(key):
            r = by_id[key]
            state = "✓" if r.get("read_at") else ("NEW" if r["author"] == other else "미확인")
            return f"{local_time(r['created_at'])} · {names[r['author']]} · {r['mood']} · {state}"
        selected = st.selectbox("우리의 기록 · 최신 100개", list(by_id), format_func=label, key="selected_entry")
        row = by_id[selected]
        # 외부 입력은 반드시 이스케이프합니다.
        import html
        st.markdown('<div class="diary">' + html.escape(row["body"]) + '</div>', unsafe_allow_html=True)
        if row.get("read_at"):
            st.caption(f"💗 {names['p2' if row['author'] == 'p1' else 'p1']} 확인 · {local_time(row['read_at'])}")
        elif row["author"] == user:
            st.caption(f"아직 {names[other]}님이 확인하지 않았어요")
        else:
            if st.checkbox("마음을 읽었어요 💗", key=f"read_{row['id']}"):
                mark_read(row)
                st.rerun(scope="fragment")
        st.caption("20초마다 자동 갱신 · 긴 일기는 카드 안에서 스크롤")
    except (requests.RequestException, sqlite3.Error, ValueError, KeyError):
        st.error("기록을 불러오지 못했어요. 연결과 저장소 설정을 확인한 뒤 ↻를 눌러주세요.")

inbox()
