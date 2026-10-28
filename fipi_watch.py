"""
Отдельный бот: только уведомления админу об изменениях открытого банка ФИПИ (ОГЭ).
Не связан с ботом трекера для учеников.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.isdigit()
}

FIPI_URL = os.getenv(
    "FIPI_URL",
    "https://oge.fipi.ru/bank/index.php?proj=8BBD5C99F37898B6402964AB11955663",
)

BASE = Path(__file__).resolve().parent
HASH_FILE = BASE / "fipi_hash.txt"
SNAPSHOT_FILE = BASE / "fipi_snapshot.txt"


def normalize_html(html: str) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_signals(text: str) -> dict:
    page_nums = set(re.findall(r"(?i)(?:стр\.?|страниц[аыуе]?)[\s№:]*(\d{1,4})", text))
    page_nums |= set(re.findall(r"(?i)page[\s:]*(\d{1,4})", text))
    page_nums |= set(re.findall(r"(?i)(\d{1,4})\s*из\s*\d{1,4}", text))
    codes = re.findall(r"\b[A-Z0-9]{5,12}\b", text)
    return {
        "chars": len(text),
        "word_zadanie": len(re.findall(r"[Зз]адани\w*", text)),
        "code_like": len(set(codes)),
        "codes_sample": sorted(set(codes))[:30],
        "numbers": len(re.findall(r"\b\d{2,}\b", text)),
        "pages": sorted(page_nums, key=lambda x: int(x) if x.isdigit() else 0),
    }


def fetch_page(url: str) -> tuple[str, str]:
    headers = {
        "User-Agent": "FIPI-Notify-Bot/1.0",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=45)
    r.raise_for_status()
    norm = normalize_html(r.text)
    digest = hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()
    return norm, digest


def describe_changes(old_sig: dict, new_sig: dict) -> list[str]:
    lines: list[str] = []
    d_chars = new_sig["chars"] - old_sig["chars"]
    if d_chars > 200:
        lines.append(f"• Объём текста вырос (~{d_chars:+d} символов)")
    elif d_chars < -200:
        lines.append(f"• Объём текста уменьшился (~{d_chars:+d} символов)")
    else:
        lines.append("• Объём текста почти не изменился (возможны мелкие правки)")

    d_z = new_sig["word_zadanie"] - old_sig["word_zadanie"]
    if d_z > 0:
        lines.append(f"• Больше упоминаний «задани…»: {old_sig['word_zadanie']} → {new_sig['word_zadanie']}")
    elif d_z < 0:
        lines.append(f"• Меньше упоминаний «задани…»: {old_sig['word_zadanie']} → {new_sig['word_zadanie']}")

    d_c = new_sig["code_like"] - old_sig["code_like"]
    if d_c > 0:
        lines.append(f"• Больше code-like строк: {old_sig['code_like']} → {new_sig['code_like']}")
    elif d_c < 0:
        lines.append(f"• Меньше code-like строк: {old_sig['code_like']} → {new_sig['code_like']}")

    old_pages = set(old_sig.get("pages") or [])
    new_pages = set(new_sig.get("pages") or [])
    if new_pages - old_pages:
        lines.append("• Новые номера стр. в тексте: " + ", ".join(sorted(new_pages - old_pages, key=lambda x: int(x) if x.isdigit() else 0)))
    if old_pages - new_pages:
        lines.append("• Пропали номера стр. в тексте: " + ", ".join(sorted(old_pages - new_pages, key=lambda x: int(x) if x.isdigit() else 0)))

    old_codes = set(old_sig.get("codes_sample") or [])
    new_codes = set(new_sig.get("codes_sample") or [])
    added_codes = sorted(new_codes - old_codes)[:12]
    removed_codes = sorted(old_codes - new_codes)[:12]
    if added_codes:
        lines.append("• Новые code-like: " + ", ".join(added_codes))
    if removed_codes:
        lines.append("• Пропавшие code-like: " + ", ".join(removed_codes))

    if len(lines) == 1 and "почти не изменился" in lines[0]:
        lines.append("• Есть отличие hash — загляни на сайт ФИПИ вручную.")
    return lines


def diff_fragments(old: str, new: str, max_lines: int = 5) -> list[str]:
    def chunks(s: str, n: int = 100) -> list[str]:
        return [s[i : i + n] for i in range(0, min(len(s), 5000), n)]

    old_set = set(chunks(old))
    new_set = set(chunks(new))
    added = [a.strip() for a in list(new_set - old_set) if a.strip()][:max_lines]
    removed = [r.strip() for r in list(old_set - new_set) if r.strip()][:max_lines]
    lines: list[str] = []
    if added:
        lines.append("<b>Примеры нового текста:</b>")
        for a in added:
            lines.append("+ " + a[:140])
    if removed:
        lines.append("<b>Примеры исчезнувшего текста:</b>")
        for r in removed:
            lines.append("− " + r[:140])
    if not lines:
        lines.append("Крупных текстовых фрагментов в diff нет.")
    return lines


def send_admin(text: str) -> None:
    if not BOT_TOKEN:
        print("BOT_TOKEN не задан.\n", text)
        return
    if not ADMIN_IDS:
        print("ADMIN_IDS пуст.\n", text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in ADMIN_IDS:
        try:
            requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            print("Отправлено", chat_id)
        except Exception as e:
            print("Ошибка", chat_id, e)


def main() -> int:
    try:
        text, digest = fetch_page(FIPI_URL)
    except Exception as e:
        send_admin(f"Не удалось проверить ФИПИ:\n<code>{e}</code>")
        print("Fetch error:", e)
        return 1

    old_digest = HASH_FILE.read_text(encoding="utf-8").strip() if HASH_FILE.exists() else ""
    old_text = SNAPSHOT_FILE.read_text(encoding="utf-8") if SNAPSHOT_FILE.exists() else ""

    if not old_digest:
        HASH_FILE.write_text(digest, encoding="utf-8")
        SNAPSHOT_FILE.write_text(text[:50000], encoding="utf-8")
        print("Первый запуск: снимок сохранён.")
        return 0

    if digest == old_digest:
        print("Изменений нет.")
        return 0

    old_sig = extract_signals(old_text)
    new_sig = extract_signals(text)

    msg = (
        "<b>ФИПИ · изменения в открытом банке (ОГЭ, английский)</b>\n\n"
        f'<a href="{FIPI_URL}">Открыть банк</a>\n\n'
        "<b>Что изменилось (оценка):</b>\n"
        + "\n".join(describe_changes(old_sig, new_sig))
        + "\n\n"
        + "\n".join(diff_fragments(old_text, text))
        + "\n\n"
        "<i>Автооценка по странице сайта. После уведомления проверь банк вручную.</i>"
    )

    send_admin(msg)
    HASH_FILE.write_text(digest, encoding="utf-8")
    SNAPSHOT_FILE.write_text(text[:50000], encoding="utf-8")
    print("Изменение зафиксировано.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
