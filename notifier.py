"""
notifier.py — Email alert module for Early Signal Detection.
=============================================================
Sends a formatted HTML email when anomalous keywords are detected.

Uses Python's built-in smtplib with STARTTLS (port 587).
Works with Gmail, Outlook, or any standard SMTP provider.

Gmail setup:
  1. Enable 2-Step Verification on your Google account
  2. Go to myaccount.google.com → Security → App Passwords
  3. Generate a 16-character App Password
  4. Set SIGNAL_EMAIL_PASSWORD=<app_password> in your .env file

Usage:
    from notifier import send_anomaly_alert
    send_anomaly_alert(report_df)        # sends only if anomalies exist
"""

from __future__ import annotations              # X | Y type hints on Python 3.9

import smtplib                                  # standard SMTP client
import logging                                  # structured log messages
from email.mime.multipart import MIMEMultipart  # multi-part email container
from email.mime.text import MIMEText            # plain-text and HTML email parts
from datetime import datetime                   # timestamp in subject line

import pandas as pd                             # DataFrame type hint

import config                                   # SMTP settings, credentials, threshold

# ── Module-level logger ───────────────────────────────────────────────────────
log = logging.getLogger(__name__)               # scoped to "notifier" in log output


# ══════════════════════════════════════════════════════════════════════════════
# 1. HTML TEMPLATE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _anomaly_rows_html(anomalies: pd.DataFrame) -> str:
    """Build HTML <tr> rows for each anomalous keyword."""
    rows = ""                                   # accumulate row HTML strings
    for _, row in anomalies.iterrows():         # iterate each anomalous signal
        growth = (                              # format growth rate or show N/A
            f"{row['growth_rate']:+.1f}%"
            if pd.notna(row.get("growth_rate"))
            else "N/A"
        )
        score = (                               # format composite score
            f"{row['score']:+.1f}"
            if pd.notna(row.get("score"))
            else "N/A"
        )
        rows += f"""
        <tr>
          <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;
                     font-weight:600;color:#c53030;">
            {str(row['keyword']).title()}
          </td>
          <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;
                     text-align:center;">{int(row['frequency'])}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;
                     text-align:center;color:#c53030;font-weight:700;">{growth}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;
                     text-align:center;">{row['z_score']:.2f}</td>
          <td style="padding:10px 14px;border-bottom:1px solid #f0f0f0;
                     text-align:center;">{score}</td>
        </tr>"""                                # one <tr> per anomalous keyword
    return rows                                 # full table body HTML


def _all_signals_rows_html(df: pd.DataFrame) -> str:
    """Build compact HTML rows for all tracked keywords (summary section)."""
    rows = ""                                   # accumulate row strings
    for _, row in df.iterrows():                # iterate entire signal report
        anom_style = (                          # red text for anomaly rows
            "color:#c53030;font-weight:600;" if row["is_anomaly"] else "color:#4a5568;"
        )
        growth_str = (
            f"{row['growth_rate']:+.1f}%" if pd.notna(row.get("growth_rate")) else "—"
        )
        rows += f"""
        <tr>
          <td style="padding:7px 14px;border-bottom:1px solid #f7f7f7;
                     {anom_style}">{str(row['keyword']).title()}</td>
          <td style="padding:7px 14px;border-bottom:1px solid #f7f7f7;
                     text-align:center;font-size:12px;color:#718096;">{growth_str}</td>
          <td style="padding:7px 14px;border-bottom:1px solid #f7f7f7;
                     text-align:center;font-size:12px;">
            {'🔴' if row['is_anomaly'] else '–'}
          </td>
        </tr>"""
    return rows                                 # full summary table body


def _build_html(
    anomalies:   pd.DataFrame,
    all_signals: pd.DataFrame,
) -> str:
    """
    Assemble the complete HTML email body.
    Returns a self-contained HTML string suitable for MIMEText(subtype='html').
    """
    n_anom       = len(anomalies)               # number of anomalous signals
    n_total      = len(all_signals)             # total signals tracked
    now_str      = datetime.now().strftime("%Y-%m-%d  %H:%M")  # display timestamp
    threshold    = config.ANOMALY_THRESHOLD     # z-score threshold from config
    anom_rows    = _anomaly_rows_html(anomalies)      # red anomaly table rows
    all_rows     = _all_signals_rows_html(all_signals) # grey summary rows

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:20px;background:#f0f4f8;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

  <div style="max-width:620px;margin:0 auto;background:#fff;
              border-radius:10px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.08);">

    <!-- ── Header ──────────────────────────────────────────────────── -->
    <div style="background:#1a202c;padding:28px 32px;">
      <div style="color:#a0aec0;font-size:11px;letter-spacing:1.5px;
                  text-transform:uppercase;margin-bottom:6px;">
        Early Signal Detection System
      </div>
      <div style="color:#fff;font-size:22px;font-weight:700;line-height:1.2;">
        🔴 {n_anom} Anomal{'y' if n_anom == 1 else 'ies'} Detected
      </div>
      <div style="color:#718096;font-size:12px;margin-top:6px;">{now_str}</div>
    </div>

    <!-- ── Alert banner ─────────────────────────────────────────────── -->
    <div style="background:#fff5f5;border-left:4px solid #e53e3e;padding:12px 32px;">
      <span style="color:#742a2a;font-size:13px;">
        <strong>{n_anom}</strong> of <strong>{n_total}</strong> tracked keywords
        exceeded the anomaly threshold
        (z&#8209;score&nbsp;&gt;&nbsp;{threshold})
      </span>
    </div>

    <!-- ── Anomaly table ─────────────────────────────────────────────── -->
    <div style="padding:24px 32px 8px;">
      <div style="font-size:11px;font-weight:700;color:#718096;
                  letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;">
        Anomalous Signals
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f7fafc;">
            <th style="padding:9px 14px;text-align:left;color:#4a5568;
                       font-weight:600;border-bottom:2px solid #e2e8f0;">Keyword</th>
            <th style="padding:9px 14px;text-align:center;color:#4a5568;
                       font-weight:600;border-bottom:2px solid #e2e8f0;">Freq</th>
            <th style="padding:9px 14px;text-align:center;color:#4a5568;
                       font-weight:600;border-bottom:2px solid #e2e8f0;">Growth</th>
            <th style="padding:9px 14px;text-align:center;color:#4a5568;
                       font-weight:600;border-bottom:2px solid #e2e8f0;">Z&#8209;Score</th>
            <th style="padding:9px 14px;text-align:center;color:#4a5568;
                       font-weight:600;border-bottom:2px solid #e2e8f0;">Score</th>
          </tr>
        </thead>
        <tbody>{anom_rows}</tbody>
      </table>
    </div>

    <!-- ── All signals summary ──────────────────────────────────────── -->
    <div style="padding:16px 32px 24px;">
      <div style="font-size:11px;font-weight:700;color:#718096;
                  letter-spacing:1px;text-transform:uppercase;
                  margin-bottom:12px;margin-top:8px;">
        All Tracked Keywords
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f7fafc;">
            <th style="padding:7px 14px;text-align:left;color:#718096;
                       font-size:11px;border-bottom:1px solid #e2e8f0;">Keyword</th>
            <th style="padding:7px 14px;text-align:center;color:#718096;
                       font-size:11px;border-bottom:1px solid #e2e8f0;">Growth</th>
            <th style="padding:7px 14px;text-align:center;color:#718096;
                       font-size:11px;border-bottom:1px solid #e2e8f0;">Flag</th>
          </tr>
        </thead>
        <tbody>{all_rows}</tbody>
      </table>
    </div>

    <!-- ── Footer ───────────────────────────────────────────────────── -->
    <div style="background:#f7f9fc;padding:14px 32px;
                border-top:1px solid #e2e8f0;">
      <span style="color:#a0aec0;font-size:11px;">
        Early Signal Detection System · Automated alert · Do not reply
      </span>
    </div>

  </div>
</body>
</html>"""                                      # complete self-contained HTML document


# ══════════════════════════════════════════════════════════════════════════════
# 2. SMTP SENDER
# ══════════════════════════════════════════════════════════════════════════════

def _send_via_smtp(subject: str, html_body: str) -> None:
    """
    Connect to the configured SMTP server and deliver one email.
    Uses STARTTLS (port 587) — compatible with Gmail, Outlook, and most providers.

    Raises RuntimeError with a descriptive message on failure so callers can
    decide whether to abort or continue without alerting.
    """
    msg = MIMEMultipart("alternative")          # container for both plain-text and HTML parts
    msg["Subject"] = subject                    # email subject line
    msg["From"]    = config.EMAIL_SENDER        # sender address (must match SMTP credentials)
    msg["To"]      = config.EMAIL_RECIPIENT     # recipient address

    plain = MIMEText(                           # plain-text fallback for email clients that block HTML
        "Anomalies detected. View this email in an HTML-capable client.",
        "plain", "utf-8",
    )
    html = MIMEText(html_body, "html", "utf-8") # HTML version with tables and colours

    msg.attach(plain)                           # attach plain-text part first (lower priority)
    msg.attach(html)                            # attach HTML part second (preferred by clients)

    try:
        with smtplib.SMTP(                      # open connection to SMTP server
            config.EMAIL_SMTP_HOST,             # e.g. "smtp.gmail.com"
            config.EMAIL_SMTP_PORT,             # 587 for STARTTLS
            timeout=15,                         # fail fast if server is unreachable
        ) as server:
            server.ehlo()                       # identify this client to the server
            server.starttls()                   # upgrade connection to TLS encryption
            server.ehlo()                       # re-identify after TLS upgrade
            server.login(                       # authenticate with App Password
                config.EMAIL_SENDER,
                config.EMAIL_PASSWORD,
            )
            server.sendmail(                    # deliver the message
                config.EMAIL_SENDER,
                config.EMAIL_RECIPIENT,
                msg.as_string(),
            )
        log.info(
            "Alert email sent to %s  (subject: %s)",
            config.EMAIL_RECIPIENT, subject,
        )

    except smtplib.SMTPAuthenticationError:     # wrong credentials — App Password issue
        raise RuntimeError(
            "SMTP authentication failed. "
            "For Gmail, generate an App Password at myaccount.google.com → Security."
        )
    except smtplib.SMTPException as exc:        # other SMTP-level errors
        raise RuntimeError(f"SMTP error: {exc}")
    except OSError as exc:                      # network unreachable, DNS failure, timeout
        raise RuntimeError(f"Network error sending email: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def send_anomaly_alert(report: pd.DataFrame) -> bool:
    """
    Send an HTML email alert if the report contains anomalous signals.

    Skips silently when:
      - EMAIL_ENABLED is False in config (default)
      - No anomalies in the report
      - Required credentials are missing

    Returns True if an email was sent, False otherwise.
    """
    if not config.EMAIL_ENABLED:                # email alerts disabled in config/.env
        log.debug("Email alerts disabled (SIGNAL_EMAIL_ENABLED not set to 'true').")
        return False                            # nothing to do

    anomalies = report[report["is_anomaly"]]    # filter to only anomalous rows

    if anomalies.empty:                         # no anomalies — no alert needed
        log.info("No anomalies detected — skipping email alert.")
        return False                            # clean exit

    if not config.EMAIL_SENDER or not config.EMAIL_PASSWORD or not config.EMAIL_RECIPIENT:
        log.warning(                            # credentials not configured — warn but don't crash
            "Email alert skipped: SIGNAL_EMAIL_SENDER / SIGNAL_EMAIL_PASSWORD / "
            "SIGNAL_EMAIL_RECIPIENT not set in environment."
        )
        return False

    n      = len(anomalies)                     # number of anomalies for subject line
    now    = datetime.now().strftime("%Y-%m-%d")
    subject = f"📡 [{now}] Signal Alert — {n} Anomal{'y' if n == 1 else 'ies'} Detected"

    html_body = _build_html(anomalies, report)  # render the full HTML email body

    try:
        _send_via_smtp(subject, html_body)      # deliver via SMTP
        return True                             # email sent successfully
    except RuntimeError as exc:
        log.error("Failed to send alert email: %s", exc)  # log but don't crash the pipeline
        return False                            # caller continues without alerting
