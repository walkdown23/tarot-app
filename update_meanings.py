"""
update_meanings.py
------------------
매일 스케줄러(cron / systemd timer / GitHub Actions)에서 실행되어
tarotapi.dev API(78장, AE Waite 1910 원문 기반 퍼블릭 도메인 데이터)를 받아와
로컬 캐시 파일(tarot_data_cache.json)로 저장한다.

설계 원칙:
1. 실패해도 기존 캐시를 절대 훼손하지 않는다 (원자적 쓰기 + 백업).
2. 네트워크 장애 시 지수 백오프로 재시도 후, 그래도 실패하면 조용히 종료(exit 1) + 로그만 남김.
3. 저작권 보호: API가 제공하는 desc(긴 서술형 원문)는 저장하지 않고,
   이미 짧은 meaning_up / meaning_rev 문구만 그대로 구조적 데이터로 저장한다.
4. 실행 결과를 항상 update.log 에 타임스탬프와 함께 기록한다.
"""

import json
import logging
import os
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

API_URL = "https://tarotapi.dev/api/v1/cards"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "tarot_data_cache.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "update.log")
EXPECTED_CARD_COUNT = 78
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 5

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def fetch_all_cards():
    """API 호출 (지수 백오프 재시도). 실패하면 예외를 던진다."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                API_URL, headers={"User-Agent": "tarot-reading-app-updater/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                return data.get("cards", [])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            logging.warning(f"시도 {attempt}/{MAX_RETRIES} 실패: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE_SECONDS * attempt)  # 5s, 10s, ...
    raise RuntimeError(f"API 호출 {MAX_RETRIES}회 모두 실패: {last_error}")


def normalize(cards):
    """API 원본을 우리 리딩 프로그램이 쓰는 구조로 정규화.
    저작권상 안전하게 meaning_up/meaning_rev(이미 짧은 구절)만 취하고,
    긴 서술형 desc 필드는 저장하지 않는다."""
    normalized = {}
    for c in cards:
        name = c.get("name")
        if not name:
            continue
        normalized[name] = {
            "name_short": c.get("name_short"),
            "type": c.get("type"),          # "major" / "minor"
            "suit": c.get("suit"),          # minor만 존재
            "meaning_up": (c.get("meaning_up") or "").strip(),
            "meaning_rev": (c.get("meaning_rev") or "").strip(),
        }
    return normalized


def atomic_write(path, payload: dict):
    """임시 파일에 쓰고 os.replace로 교체 -> 쓰다가 죽어도 기존 파일은 안전.
    교체 전, 기존 파일이 있으면 .bak으로 백업."""
    if os.path.exists(path):
        backup_path = path + ".bak"
        os.replace(path, backup_path)  # 기존 캐시를 백업으로 이동

    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tarot_cache_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 원자적 교체
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def run():
    logging.info("업데이트 시작")
    try:
        raw_cards = fetch_all_cards()
        if len(raw_cards) != EXPECTED_CARD_COUNT:
            raise ValueError(
                f"카드 수 불일치: {len(raw_cards)}장 수신 (기대값 {EXPECTED_CARD_COUNT}장) - 캐시를 갱신하지 않음"
            )
        normalized = normalize(raw_cards)
        payload = {
            "source": "tarotapi.dev (A.E. Waite, Pictorial Key to the Tarot, 1910 / public domain)",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "card_count": len(normalized),
            "cards": normalized,
        }
        atomic_write(CACHE_PATH, payload)
        logging.info(f"업데이트 성공: {len(normalized)}장 저장 -> {CACHE_PATH}")
        print(f"OK: {len(normalized)}장 갱신 완료 ({CACHE_PATH})")
        return 0
    except Exception as e:
        logging.error(f"업데이트 실패, 기존 캐시 유지: {e}")
        print(f"FAIL: {e} (기존 캐시는 그대로 유지됩니다)")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
