import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config.settings import settings

logger = logging.getLogger("uvicorn.error")

def send_otp_email(recipient_email: str, otp_code: str, purpose: str = "signup"):
    """
    Sends a 6-digit OTP code to the user's email.
    If default settings are detected, it gracefully prints the OTP to the console/log.
    """
    subject = "Verify your signup" if purpose == "signup" else "Reset your password"
    body = f"""
    Hello,

    Thank you for choosing Equinox. Your 6-digit OTP code is:
    
    ===> {otp_code} <===

    This code is valid for 5 minutes. If you did not make this request, please ignore this email.

    Best regards,
    The Equinox Team
    """

    # Check if SMTP credentials have been configured
    if settings.SMTP_USERNAME == "your-email@gmail.com" or not settings.SMTP_PASSWORD:
        logger.warning(
            f"\n"
            f"========================================================================\n"
            f"[DEVELOPMENT OVERRIDE] SMTP Credentials not configured in .env\n"
            f"Recipient: {recipient_email}\n"
            f"Purpose: {purpose.upper()}\n"
            f"OTP Code: {otp_code}\n"
            f"========================================================================"
        )
        return True

    try:
        # Create MIME message structure
        msg = MIMEMultipart()
        msg["From"] = settings.SMTP_SENDER
        msg["To"] = recipient_email
        msg["Subject"] = f"Equinox Security Ticker - {subject}"
        msg.attach(MIMEText(body, "plain"))

        # Initialize SMTP TLS connection
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        
        # Transmit email
        server.sendmail(settings.SMTP_SENDER, recipient_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent OTP email to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to transmit OTP email to {recipient_email}: {e}")
        # Always print to console as a secondary recovery fallback in local environments
        logger.info(f"[EMERGENCY LOG FALLBACK] Recipient: {recipient_email}, OTP: {otp_code}")
        return False


def send_watchlist_sentiment_email(recipient_email: str, recommendations: list) -> bool:
    """
    Sends a formatted HTML/plain text email of watchlist recommendations to the user.
    """
    subject = "Hourly Watchlist AI Sentiment & Quant Report"
    
    # Format the recommendations list into a clean text block
    recs_text = ""
    for idx, rec in enumerate(recommendations):
        recs_text += f"{idx+1}. {rec['symbol']} ({rec['name']})\n"
        recs_text += f"   Price: INR {rec['price']:.2f} ({rec['changePercent']:.2f}%)\n"
        recs_text += f"   Action: {rec['action']} (Score: {rec['sentimentScore']}/100)\n"
        recs_text += f"   Target: INR {rec['targetPrice']:.2f} | Stop Loss: INR {rec['stopLoss']:.2f}\n"
        if rec.get('sources'):
            recs_text += "   Top Sources:\n"
            for src in rec['sources'][:2]:
                recs_text += f"     - {src['source']}: {src['headline']}\n"
        recs_text += "\n"
        
    body = f"""
    Hello,

    Here is your hourly AI Sentiment & Quant analysis report for your Watchlist:

    {recs_text}
    Best regards,
    The Equinox Team
    """

    if settings.SMTP_USERNAME == "your-email@gmail.com" or not settings.SMTP_PASSWORD:
        logger.warning(
            f"\n"
            f"========================================================================\n"
            f"[DEVELOPMENT OVERRIDE] SMTP Credentials not configured in .env\n"
            f"Recipient: {recipient_email}\n"
            f"Purpose: WATCHLIST SENTIMENT EMAIL REPORT\n"
            f"Content:\n{body}\n"
            f"========================================================================"
        )
        return True

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.SMTP_SENDER
        msg["To"] = recipient_email
        msg["Subject"] = f"Equinox AI Ticker - {subject}"
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_SENDER, recipient_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent watchlist sentiment report to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to transmit watchlist sentiment report to {recipient_email}: {e}")
        logger.info(f"[EMERGENCY LOG FALLBACK] Recipient: {recipient_email}\nContent:\n{body}")
        return False
