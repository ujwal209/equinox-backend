import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any, Optional
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

def send_otp_email(recipient_email: str, otp_code: str) -> bool:
    """
    Sends an OTP verification email to the user.
    """
    subject = "Equinox - Email Verification Code"
    
    plain_text = f"Your Equinox verification code is: {otp_code}. This code will expire in 10 minutes."
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            @media only screen and (max-width: 600px) {{
                .container {{ width: 100% !important; padding: 20px 14px !important; }}
            }}
        </style>
    </head>
    <body style="background-color: #000000; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 20px 10px; margin: 0;">
        <div class="container" style="max-width: 520px; margin: 0 auto; background-color: #09090b; border: 1px solid #27272a; border-radius: 24px; padding: 32px; text-align: left;">
            <div style="margin-bottom: 24px;">
                <div style="display: inline-block; background-color: #ffffff; border-radius: 10px; padding: 6px 12px;">
                    <span style="font-weight: 900; font-size: 13px; color: #000000; letter-spacing: 1px;">EQUINOX</span>
                </div>
            </div>
            <h2 style="font-size: 22px; font-weight: 900; color: #ffffff; margin: 0 0 10px 0; letter-spacing: -0.5px;">Verify Your Email</h2>
            <p style="color: #a1a1aa; font-size: 14px; line-height: 1.5; margin-0 0 24px 0;">Use the verification code below to complete your authentication with Equinox Core.</p>
            
            <div style="background-color: #18181b; border: 1px solid #27272a; border-radius: 16px; padding: 20px; text-align: center; margin-bottom: 24px;">
                <span style="font-family: monospace; font-size: 32px; font-weight: 900; letter-spacing: 8px; color: #ffffff;">{otp_code}</span>
            </div>
            
            <p style="color: #71717a; font-size: 12px; margin: 0; line-height: 1.4;">This code will expire in 10 minutes. If you did not request this email, please ignore it.</p>
        </div>
    </body>
    </html>
    """

    if settings.SMTP_USERNAME == "your-email@gmail.com" or not settings.SMTP_PASSWORD:
        logger.warning(
            f"\n"
            f"========================================================================\n"
            f"[DEVELOPMENT OVERRIDE] SMTP Credentials not configured in .env\n"
            f"Recipient: {recipient_email}\n"
            f"Purpose: OTP Verification Code\n"
            f"Code: {otp_code}\n"
            f"========================================================================"
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_SENDER
        msg["To"] = recipient_email
        msg["Subject"] = subject
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_SENDER, recipient_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent OTP email to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send OTP email to {recipient_email}: {e}")
        return False


def send_intraday_watchlist_email(
    recipient_email: str, 
    analysis_items: List[Dict[str, Any]], 
    frequency_label: str = "Automated"
) -> bool:
    """
    Sends an automated or manual intraday AI Watchlist email digest.
    """
    if not analysis_items:
        logger.info(f"No analysis items provided for {recipient_email}. Skipping digest.")
        return False

    subject = f"Equinox Market Intelligence - Watchlist Digest ({frequency_label})"
    
    # Build HTML Cards for each stock
    stock_cards_html = ""
    plain_text_summary = ""
    
    for item in analysis_items:
        sym = item.get("symbol", "N/A")
        name = item.get("name", sym)
        price = item.get("price", 0.0)
        change_pct = item.get("changePercent", 0.0)
        curr_sym = item.get("currency_symbol", "₹")
        clean_sym = sym.split('.')[0].upper()
        exchange = "BSE" if sym.upper().endswith(".BO") else ("NSE" if "." in sym else "US")
        
        placeholder_logo = f"https://ui-avatars.com/api/?name={clean_sym[:2]}&background=18181b&color=ffffff&bold=true&size=128"
        default_logo = f"https://eodhd.com/img/logos/{exchange}/{clean_sym}.png"
        logo_url = item.get("logo_url") or default_logo
        
        sentiment = item.get("sentiment", "NEUTRAL").upper()
        summary = item.get("summary", "No detailed summary available.")
        suggestion = item.get("suggestion", "Watch for intraday breakouts.")
        sources = item.get("sources", [])
        
        sent_color = "#10b981" if sentiment == "BULLISH" else "#ef4444" if sentiment == "BEARISH" else "#a1a1aa"
        sent_bg = "rgba(16,185,129,0.12)" if sentiment == "BULLISH" else "rgba(239,68,68,0.12)" if sentiment == "BEARISH" else "rgba(161,161,170,0.12)"
        
        sources_html = ""
        if sources:
            sources_html = "<div style='margin-top: 14px; padding-top: 12px; border-top: 1px solid #27272a; font-size: 12px; color: #a1a1aa;'><strong>Intraday Catalysts:</strong><ul style='padding-left: 18px; margin-top: 6px; font-weight: 500;'>"
            for src in sources[:3]:
                title = src.get('title', 'Article')
                url = src.get('url', '#')
                sources_html += f"<li style='margin-bottom: 4px;'><a href='{url}' style='color: #ffffff; text-decoration: underline;'>{title}</a></li>"
            sources_html += "</ul></div>"
            
        stock_cards_html += f"""
        <div class="card" style="background-color: #121215; border: 1px solid #27272a; border-radius: 20px; padding: 24px; margin-bottom: 20px;">
            <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 16px; border-bottom: 1px solid #27272a; padding-bottom: 14px;">
                <tr>
                    <td align="left" style="vertical-align: middle;">
                        <table border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td style="padding-right: 12px; vertical-align: middle;">
                                    <img src="{logo_url}" onerror="this.onerror=null;this.src='{placeholder_logo}';" width="38" height="38" style="border-radius: 10px; border: 1px solid #27272a; vertical-align: middle; background-color: #ffffff; object-fit: contain; padding: 2px;" alt="{sym}" />
                                </td>
                                <td style="vertical-align: middle;">
                                    <h3 style="margin: 0; font-size: 18px; font-weight: 900; color: #ffffff; letter-spacing: -0.5px;">{sym} <span style="font-size: 12px; color: #71717a; font-weight: 600;">({name})</span></h3>
                                    <p style="margin: 3px 0 0 0; font-size: 15px; font-weight: 800; color: {'#10b981' if change_pct >= 0 else '#ef4444'};">
                                        {curr_sym} {price:.2f} ({'+' if change_pct >= 0 else ''}{change_pct:.2f}%)
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                    <td align="right" style="vertical-align: top;">
                        <span style="background-color: {sent_bg}; color: {sent_color}; border: 1px solid {sent_color}; font-weight: 900; font-size: 11px; padding: 6px 12px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; display: inline-block;">
                            {sentiment}
                        </span>
                    </td>
                </tr>
            </table>
            
            <div style="color: #e4e4e7; font-size: 14px; line-height: 1.6; margin-bottom: 14px; font-weight: 400;">
                <strong style="color: #ffffff; font-weight: 800;">Intraday Analysis:</strong><br/>
                {summary}
            </div>
            
            <div style="background-color: #18181b; border: 1px solid #27272a; border-left: 4px solid #ffffff; padding: 14px 16px; border-radius: 12px; color: #ffffff; font-size: 13px; font-weight: 600;">
                <strong>Intraday Strategy:</strong> {suggestion}
            </div>
            {sources_html}
        </div>
        """
        
        plain_text_summary += f"\n- {sym} ({sentiment})\n  Price: {curr_sym} {price:.2f} ({change_pct:.2f}%)\n  Analysis: {summary}\n  Intraday Signal: {suggestion}\n"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Equinox Intraday Watchlist Digest</title>
        <style>
            @media only screen and (max-width: 600px) {{
                .container {{ width: 100% !important; padding: 20px 14px !important; border-radius: 16px !important; }}
                .card {{ padding: 16px !important; border-radius: 16px !important; }}
                .title-text {{ font-size: 20px !important; }}
            }}
        </style>
    </head>
    <body style="background-color: #000000; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 20px 10px; margin: 0;">
        <div class="container" style="max-width: 650px; margin: 0 auto; background-color: #09090b; border: 1px solid #27272a; border-radius: 28px; padding: 36px; text-align: left;">
            
            <div style="border-bottom: 1px solid #27272a; padding-bottom: 24px; margin-bottom: 28px;">
                <table width="100%" border="0" cellspacing="0" cellpadding="0">
                    <tr>
                        <td align="left" style="vertical-align: middle;">
                            <div style="display: inline-block; background-color: #ffffff; border-radius: 12px; padding: 6px 10px; margin-bottom: 10px;">
                                <span style="font-weight: 900; font-size: 14px; color: #000000; letter-spacing: 1px;">EQUINOX</span>
                            </div>
                            <h1 class="title-text" style="margin: 0; font-size: 24px; font-weight: 900; color: #ffffff; letter-spacing: -0.5px;">Intraday Watchlist Digest</h1>
                            <p style="margin: 6px 0 0 0; color: #a1a1aa; font-size: 13px; font-weight: 600;">
                                AI Market Intelligence ({frequency_label} Report)
                            </p>
                        </td>
                    </tr>
                </table>
            </div>
            
            {stock_cards_html if stock_cards_html else '<p style="color: #a1a1aa;">No stocks in your active watchlist were found for analysis.</p>'}
            
            <div style="border-top: 1px solid #27272a; padding-top: 24px; margin-top: 32px; text-align: center; color: #71717a; font-size: 12px; font-weight: 600;">
                <p style="margin: 0;">Automated Market Intelligence by Equinox.</p>
                <p style="margin: 6px 0 0 0;">Intraday paper trading operates Mon-Fri 9:15 AM - 3:30 PM IST.</p>
            </div>
        </div>
    </body>
    </html>
    """

    if settings.SMTP_USERNAME == "your-email@gmail.com" or not settings.SMTP_PASSWORD:
        logger.warning(
            f"\n"
            f"========================================================================\n"
            f"[DEVELOPMENT OVERRIDE] SMTP Credentials not configured in .env\n"
            f"Recipient: {recipient_email}\n"
            f"Purpose: INTRADAY WATCHLIST AI DIGEST ({frequency_label})\n"
            f"Plain Text Summary:\n{plain_text_summary}\n"
            f"========================================================================"
        )
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_SENDER
        msg["To"] = recipient_email
        msg["Subject"] = subject
        msg.attach(MIMEText(plain_text_summary, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_SENDER, recipient_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent intraday watchlist AI email digest to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send intraday watchlist AI email digest to {recipient_email}: {e}")
        return False
