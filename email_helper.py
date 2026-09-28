import os
import logging
import requests

logger = logging.getLogger(__name__)

# GitHub Secrets / Environment variable se API key fetch ho rahi hai
BREVO_API_KEY = os.environ.get('BREVO_API_KEY')

def send_brevo_verification_email(to_email, user_name, otp_code, token_link):
    """
    Brevo v3 API ke through verification email send karne ka helper function.
    """
    if not BREVO_API_KEY:
        logger.error("BREVO_API_KEY environment variable missing!")
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }

    html_content = f"""
    <div style="font-family: Arial, sans-serif; background-color: #000000; color: #ffffff; padding: 25px; border-radius: 8px; max-width: 600px; margin: auto;">
        <h2 style="color: #FFD700; border-bottom: 2px solid #FFD700; padding-bottom: 10px;">Account Verification</h2>
        <p>Hello <strong style="color: #FFD700;">{user_name}</strong>,</p>
        <p>Thank you for registering on Hadi88. Please verify your account using either method below:</p>
        
        <div style="background-color: #1a1a1a; padding: 15px; border-left: 4px solid #FFD700; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px; color: #cccccc;">Method 1: Enter this 6-digit OTP code</p>
            <h1 style="color: #FFD700; letter-spacing: 6px; margin: 10px 0; font-size: 32px;">{otp_code}</h1>
        </div>

        <div style="margin: 25px 0;">
            <p style="margin-bottom: 10px; font-size: 14px; color: #cccccc;">Method 2: Click the direct verification button</p>
            <a href="{token_link}" style="background-color: #FFD700; color: #000000; padding: 12px 25px; text-decoration: none; font-weight: bold; border-radius: 5px; display: inline-block;">Verify Account Direct</a>
        </div>

        <p style="font-size: 12px; color: #888888; margin-top: 30px;">This OTP code and link will expire in 10 minutes.</p>
    </div>
    """

    payload = {
        "sender": {
            "name": "Hadi88",
            "email": "no-reply@hadi88.online"
        },
        "to": [
            {
                "email": to_email,
                "name": user_name
            }
        ],
        "subject": f"{otp_code} is your Hadi88 Verification Code",
        "htmlContent": html_content
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            return True
        else:
            logger.error(f"Brevo API Error: Status {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Brevo API Exception: {str(e)}")
        return False

