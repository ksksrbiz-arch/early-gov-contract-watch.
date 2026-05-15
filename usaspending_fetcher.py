import requests
import json
import logging
from datetime import datetime, timedelta

from config import (
    MIN_CONTRACT_AMOUNT,
    DAYS_LOOKBACK,
    AWARD_FIELDS,
    CONTRACT_AWARD_TYPES,
    STATE_FILE,
    MAX_SEEN_AWARD_IDS,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.usaspending.gov/api/v2"
API_PAGE_LIMIT = 100
MAX_RESULTS = 500


def get_last_modified_cutoff():
    return (datetime.now() - timedelta(days=DAYS_LOOKBACK)).strftime("%Y-%m-%d")


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"last_check": None, "seen_award_ids": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_recent_large_contracts():
    state = load_state()
    seen = set(state.get("seen_award_ids", []))
    start_date = get_last_modified_cutoff()
    filters = {
        "award_type_codes": CONTRACT_AWARD_TYPES,
        "award_amounts": [{"lower_bound": MIN_CONTRACT_AMOUNT}],
        "time_period": [
            {
                "start_date": start_date,
                "end_date": datetime.now().strftime("%Y-%m-%d"),
            }
        ],
    }
    max_pages = max(1, MAX_RESULTS // API_PAGE_LIMIT)
    new_awards = []
    try:
        for page in range(1, max_pages + 1):
            payload = {
                "filters": filters,
                "fields": AWARD_FIELDS,
                "sort": "Action Date",
                "order": "desc",
                "limit": API_PAGE_LIMIT,
                "page": page,
            }
            resp = requests.post(
                f"{API_BASE}/search/spending_by_award/",
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            for a in results:
                aid = a.get("Award ID") or str(a.get("id", ""))
                if aid in seen:
                    continue
                if str(a.get("Modification Number") or "").strip() not in ("0", ""):
                    continue
                new_awards.append(a)
                seen.add(aid)
            if len(results) < API_PAGE_LIMIT:
                break
        state["seen_award_ids"] = list(seen)[-MAX_SEEN_AWARD_IDS:]
        save_state(state)
        return new_awards
    except requests.exceptions.HTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        response_text = getattr(getattr(e, "response", None), "text", "") or ""
        logger.error(
            "API error: %s (status=%s, response=%s)",
            e,
            status_code,
            response_text[:500],
        )
        return []
    except Exception as e:
        logger.error(f"API error: {e}")
        return []


def print_award_summary(award):
    description = (award.get("Description") or "")[:120]
    print(
        f"\n=== NEW CONTRACT ===\n"
        f"{award.get('Recipient Name')} | ${float(award.get('Award Amount') or 0):,.0f}\n"
        f"{description}...\n"
    )
