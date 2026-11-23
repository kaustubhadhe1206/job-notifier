"""Email notifications over SMTP (free with a Gmail app password, or any SMTP provider)."""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def recipient(settings: dict) -> str:
    return (settings.get("notify_email") or os.environ.get("NOTIFY_EMAIL") or os.environ.get("SMTP_USER") or "").strip()


def send_email(to: str, subject: str, text: str, html: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if password and "gmail" in host:
        password = password.replace(" ", "")  # Google shows app passwords as "abcd efgh ijkl mnop"
    if not (user and password):
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD are not set")
    if not to:
        raise RuntimeError("No recipient: set a notification email in Settings")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM") or f"Job Notifier <{user}>"
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)


VISA_LABELS = {
    "stated": ("✅ Sponsors visas:", "#15803d"),
    "history": ("✅ Known H-1B sponsor:", "#15803d"),
    "listed": ("✅ Sponsor (your list):", "#15803d"),
    "unknown": ("➖ Sponsorship not mentioned:", "#6b7280"),
}


# Where the job was found: (label, text color, background)
CHANNEL_LABELS = {
    "hidden_new": ("Hidden · first opening", "#6d28d9", "#ede9fe"),
    "hidden": ("Hidden", "#6d28d9", "#ede9fe"),
    "watchlist": ("Watchlist", "#b45309", "#fef3c7"),
    "linkedin": ("LinkedIn", "#0a66c2", "#e8f1fb"),
    "jobboard": ("Job board", "#374151", "#f3f4f6"),
}


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 60:
        return "just now" if mins < 2 else f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} days ago"


def send_jobs_email(to: str, jobs: list[dict], app_url: str = "") -> None:
    n = len(jobs)
    first = jobs[0]
    hidden = sum(1 for j in jobs if (j.get("channel") or "").startswith("hidden"))
    tag = f"[{CHANNEL_LABELS[first['channel']][0]}] " if first.get("channel") in CHANNEL_LABELS else ""
    subject = f"{tag}New job: {first['title']} @ {first['company']}"
    if n > 1:
        subject = (f"{n} new jobs{f' ({hidden} hidden)' if hidden else ''}: "
                   f"{first['title']} @ {first['company']} + {n - 1} more")

    text_lines, rows = [], []
    for j in jobs:
        posted = _ago(j.get("posted_at"))
        meta = " · ".join(x for x in [j["company"], j.get("location") or "", f"posted {posted}" if posted else ""] if x)
        label, color = VISA_LABELS.get(j.get("visa") or "", ("", "#4b5563"))
        visa_line = f"{label} {j.get('note') or ''}".strip() if label else ""
        ch_label, ch_fg, ch_bg = CHANNEL_LABELS.get(j.get("channel") or "", ("", "", ""))
        text_lines.append(f"- {'[' + ch_label + '] ' if ch_label else ''}{j['title']}\n  {meta}\n  "
                          f"{visa_line + chr(10) + '  ' if visa_line else ''}{j.get('url') or ''}")
        visa_html = (f'<div style="margin-top:4px;font-size:13px;color:{color}"><strong>{escape(label)}</strong> '
                     f'<span style="color:#6b7280">{escape(j.get("note") or "")}</span></div>') if label else ""
        badge = (f'<span style="display:inline-block;font-size:11px;font-weight:700;color:{ch_fg};background:{ch_bg};'
                 f'padding:2px 8px;border-radius:99px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em">'
                 f'{escape(ch_label)}</span><br>') if ch_label else ""
        rows.append(
            f"""<tr><td style="padding:14px 16px;border-bottom:1px solid #e5e7eb">
              {badge}<a href="{escape(j.get('url') or '#', quote=True)}"
                 style="font-size:16px;font-weight:600;color:#1d4ed8;text-decoration:none">{escape(j['title'])}</a>
              <div style="margin-top:4px;color:#4b5563;font-size:14px">{escape(meta)}</div>
              {visa_html}
            </td></tr>"""
        )

    footer = f'<a href="{escape(app_url, quote=True)}" style="color:#6b7280">Open dashboard</a>' if app_url else ""
    html = f"""<!doctype html><html><body style="margin:0;background:#f9fafb;font-family:-apple-system,Segoe UI,Roboto,sans-serif">
      <div style="max-width:620px;margin:0 auto;padding:24px">
        <div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em">Job Notifier</div>
        <h1 style="font-size:20px;margin:6px 0 12px;color:#111827">{n} new job{'s' if n != 1 else ''} just posted</h1>
        <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:10px">
          {''.join(rows)}
        </table>
        <p style="font-size:12px;color:#9ca3af;margin-top:16px">{footer}</p>
      </div></body></html>"""
    text = f"{n} new job{'s' if n != 1 else ''} just posted\n\n" + "\n\n".join(text_lines)
    send_email(to, subject, text, html)
