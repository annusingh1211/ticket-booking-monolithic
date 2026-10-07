from email.message import EmailMessage
from html import escape
import logging
import smtplib

from app.core.config import settings


logger = logging.getLogger(__name__)


def _send_email(
    recipient_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = recipient_email

    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        logger.info(
            "Sending email: subject=%r recipient=%s smtp=%s:%s",
            subject,
            recipient_email,
            settings.smtp_host,
            settings.smtp_port,
        )

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout,
        ) as server:
            server.ehlo()

            if settings.smtp_starttls:
                server.starttls()
                server.ehlo()

            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)

        logger.info(
            "Email sent successfully: subject=%r recipient=%s",
            subject,
            recipient_email,
        )

    except Exception:
        logger.exception(
            "Email delivery failed: subject=%r recipient=%s smtp=%s:%s",
            subject,
            recipient_email,
            settings.smtp_host,
            settings.smtp_port,
        )


def _layout(recipient_name: str, title: str, intro: str, content_html: str) -> str:
    name = escape(recipient_name or "there")
    app = escape(settings.app_name)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
</head>
<body style="margin:0;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#263241;">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f4f6f8;padding:32px 12px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:640px;background:#ffffff;border:1px solid #e4e8ed;border-radius:14px;overflow:hidden;">
<tr><td style="background:#171b2b;padding:22px 28px;">
  <div style="font-size:20px;font-weight:700;color:#ffffff;">{app}</div>
  <div style="font-size:11px;color:#aeb5c5;margin-top:4px;">Event booking &amp; customer services</div>
</td></tr>
<tr><td style="padding:30px 28px;">
  <div style="font-size:13px;color:#6c7583;margin-bottom:8px;">{escape(intro)}</div>
  <h1 style="font-size:24px;line-height:1.3;margin:0 0 22px;color:#202938;">{escape(title)}</h1>
  {content_html}
  <p style="font-size:13px;line-height:1.6;color:#596575;margin:26px 0 0;">Hello {name},<br><br>
  This email was sent because there was activity on your {app} account. Please keep this email for your records.</p>
</td></tr>
<tr><td style="border-top:1px solid #edf0f3;padding:20px 28px;">
  <div style="font-size:11px;color:#8a94a3;line-height:1.6;">
    <strong style="color:#566170;">{app}</strong><br>
    This is an automated service email. Please do not reply to this message.
  </div>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _details_table(rows: list[tuple[str, str]]) -> str:
    html = '<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="border:1px solid #e6e9ee;border-radius:10px;overflow:hidden;">'
    for label, value in rows:
        html += f'<tr><td style="padding:12px 14px;border-bottom:1px solid #edf0f3;font-size:12px;color:#7a8492;width:38%;">{escape(label)}</td><td style="padding:12px 14px;border-bottom:1px solid #edf0f3;font-size:13px;color:#263241;font-weight:600;">{escape(str(value))}</td></tr>'
    return html + "</table>"


def send_welcome_email(recipient_email: str, recipient_name: str) -> None:
    text = f"""Hello {recipient_name},

Welcome to {settings.app_name}.

Your customer account has been created successfully. You can now discover events, select seats, manage reservations, order food for confirmed bookings, and receive booking and payment notifications.

Your account is ready to use.

Regards,
{settings.app_name} Customer Experience Team
"""
    html = _layout(
        recipient_name,
        "Welcome to TicketFlow",
        "ACCOUNT CREATED",
        '<p style="font-size:15px;line-height:1.7;color:#4f5b6b;">Your customer account is ready. You can now manage your complete event journey from one place.</p>'
        '<div style="margin-top:20px;">'
        '<div style="padding:12px 14px;background:#f6f7fb;border-radius:9px;margin-bottom:8px;font-size:13px;">Browse published events and check live seat availability.</div>'
        '<div style="padding:12px 14px;background:#f6f7fb;border-radius:9px;margin-bottom:8px;font-size:13px;">Reserve seats and receive booking confirmations.</div>'
        '<div style="padding:12px 14px;background:#f6f7fb;border-radius:9px;margin-bottom:8px;font-size:13px;">Order food against your confirmed booking.</div>'
        '<div style="padding:12px 14px;background:#f6f7fb;border-radius:9px;font-size:13px;">Track notifications, payments and cancellations.</div>'
        '</div>',
    )
    _send_email(recipient_email, f"Welcome to {settings.app_name} | Your account is ready", text, html)


def send_event_created_email(recipient_email: str, recipient_name: str, event_name: str, venue: str, starts_at: str) -> None:
    text = f"""Hello {recipient_name},

Your event has been created successfully.

Event: {event_name}
Venue: {venue}
Starts: {starts_at}

The event is now available in your TicketFlow workspace.

Regards,
{settings.app_name} Customer Experience Team
"""
    html = _layout(
        recipient_name,
        "Event created successfully",
        "EVENT MANAGEMENT",
        _details_table([("Event", event_name), ("Venue", venue), ("Starts", starts_at)])
        + '<p style="font-size:13px;line-height:1.6;color:#596575;">The event record has been created successfully in TicketFlow.</p>',
    )
    _send_email(recipient_email, f"Event created | {event_name}", text, html)


def send_event_notification_email(
    recipient_email: str,
    recipient_name: str,
    action: str,
    event_name: str,
    venue: str,
    starts_at: str,
    previous_details: str = "",
) -> None:
    text = f"""Hello {recipient_name},

There has been an update to an event in your TicketFlow account.

Action: {action}
Event: {event_name}
Venue: {venue}
Starts: {starts_at}
{previous_details}

Please review your account if you need more information.

Regards,
{settings.app_name} Customer Experience Team
"""
    html = _layout(
        recipient_name,
        f"Event update: {action}",
        "EVENT UPDATE",
        _details_table([("Action", action), ("Event", event_name), ("Venue", venue), ("Starts", starts_at)])
        + (f'<div style="margin-top:14px;padding:12px 14px;background:#f7f8fa;border-radius:9px;font-size:12px;color:#596575;">{escape(previous_details)}</div>' if previous_details else ""),
    )
    _send_email(recipient_email, f"Event update | {event_name}", text, html)


def send_notification_email(
    recipient_email: str,
    recipient_name: str,
    notification: str,
    subject: str = "TicketFlow Notification",
) -> None:
    safe_notification = escape(notification)
    text = f"""Hello {recipient_name},

{notification}

Please sign in to your TicketFlow account to review the latest details.

Regards,
{settings.app_name} Customer Experience Team
"""
    html = _layout(
        recipient_name,
        subject,
        "ACCOUNT ACTIVITY",
        f'<div style="padding:18px;background:#f7f8fa;border:1px solid #e7eaf0;border-radius:10px;font-size:14px;line-height:1.7;color:#354052;">{safe_notification}</div>'
        '<p style="font-size:12px;color:#7b8593;">For your security, TicketFlow will never ask you to share your password or authentication credentials by email.</p>',
    )
    _send_email(recipient_email, subject, text, html)



def send_booking_confirmation_email(
    recipient_email: str, recipient_name: str, reference: str, event_name: str,
    venue: str, starts_at: str, seat_numbers: list[str], total_amount: float,
) -> None:
    seats = ", ".join(seat_numbers) or "—"
    text = f"""Hello {recipient_name},

Your TicketFlow booking is confirmed.

Booking reference: {reference}
Event: {event_name}
Venue: {venue}
Starts: {starts_at}
Seats: {seats}
Total ticket amount: ₹{total_amount:.2f}

Please keep this confirmation for your records.

Regards,
{settings.app_name} Customer Experience Team
"""
    html = _layout(
        recipient_name, "Booking confirmed", "RESERVATION CONFIRMATION",
        _details_table([
            ("Booking reference", reference), ("Event", event_name), ("Venue", venue),
            ("Starts", starts_at), ("Seats", seats), ("Ticket total", f"₹{total_amount:.2f}"),
        ]) + '<p style="font-size:13px;line-height:1.6;color:#596575;margin-top:18px;">Your reservation is confirmed. You can review the complete order, payment and food details from your TicketFlow account.</p>',
    )
    _send_email(recipient_email, f"Booking confirmed | {reference} | {event_name}", text, html)


def send_final_order_confirmation_email(recipient_email, recipient_name, order):
    seats = ", ".join(order["seats"]) or "—"
    food_lines = "\n".join(f'{x["name"]} × {x["quantity"]}    ₹{x["line_total"]:.2f}' for x in order["food_items"]) or "No food ordered"
    text = f"""Hello {recipient_name},\n\nYour payment has been successfully completed and your reservation is now fully confirmed.\n\nBOOKING DETAILS\nBooking Reference: {order["reference"]}\nEvent: {order["event_name"]}\nVenue: {order["venue"]}\nDate & Time: {order["starts_at"]}\nBooking Status: {order["status"]}\n\nSEATS\n{seats}\n\nTICKET SUMMARY\nTicket total: ₹{order["ticket_total"]:.2f}\n\nFOOD ORDERS\n{food_lines}\nFood total: ₹{order["food_total"]:.2f}\n\nPAYMENT\nPayment ID: #{order["payment_id"]}\nPayment Status: {order["payment_status"]}\nPayment Amount: ₹{order["payment_amount"]:.2f}\nPayment Reference: {order["payment_reference"]}\n\nORDER TOTAL\nTickets: ₹{order["ticket_total"]:.2f}\nFood: ₹{order["food_total"]:.2f}\nGrand Total: ₹{order["grand_total"]:.2f}\n\nYour booking is completely confirmed. Please keep this email as your booking receipt and reference.\n\nRegards,\n{settings.app_name} Customer Experience Team\n"""
    food_html = "".join(f'<tr><td style="padding:10px;border-bottom:1px solid #edf0f3;font-size:12px;">{escape(x["name"])} × {x["quantity"]}</td><td align="right" style="padding:10px;border-bottom:1px solid #edf0f3;font-size:12px;font-weight:600;">₹{x["line_total"]:.2f}</td></tr>' for x in order["food_items"])
    if not food_html: food_html = '<tr><td colspan="2" style="padding:10px;color:#7a8492;font-size:12px;">No food ordered</td></tr>'
    content = _details_table([("Booking reference",order["reference"]),("Event",order["event_name"]),("Venue",order["venue"]),("Date & time",order["starts_at"]),("Booking status",order["status"]),("Seats",seats),("Ticket total",f'₹{order["ticket_total"]:.2f}'),("Food total",f'₹{order["food_total"]:.2f}'),("Payment",order["payment_status"]),("Payment reference",order["payment_reference"]),("Grand total",f'₹{order["grand_total"]:.2f}')])
    content += f'<h3 style="font-size:16px;margin:24px 0 10px;">Food orders</h3><table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6e9ee;border-radius:10px;overflow:hidden;">{food_html}</table>'
    html = _layout(recipient_name,"Payment successful — booking confirmed","FINAL BOOKING CONFIRMATION",content)
    _send_email(recipient_email,f"Payment successful | {order["reference"]} | {order["event_name"]}",text,html)
