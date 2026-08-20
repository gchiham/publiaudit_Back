"""
PubliAudit — Email via Amazon SES (SMTP).
"""
import os, smtplib, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

log = logging.getLogger(__name__)

SMTP_HOST      = os.environ.get('SMTP_HOST', 'email-smtp.us-east-1.amazonaws.com')
SMTP_PORT      = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER      = os.environ.get('SMTP_USER', '')
SMTP_PASS      = os.environ.get('SMTP_PASS', '')
EMAIL_FROM     = os.environ.get('EMAIL_FROM', 'reports@publiaudit.com')
EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME', 'PubliAudit')


def send_email(
    to: str | list[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
    attachments: list[str | Path] | None = None,
    cc: str | list[str] | None = None,
) -> bool:
    """
    Envía un email via Amazon SES SMTP.
    Retorna True si exitoso, False si falla (loguea el error).

    to          : email destino o lista de emails
    subject     : asunto
    body_html   : cuerpo HTML
    body_text   : cuerpo texto plano (opcional, fallback)
    attachments : lista de rutas a archivos a adjuntar
    cc          : copia a (opcional)
    """
    if not SMTP_USER or not SMTP_PASS:
        log.error('[mail] SMTP_USER/SMTP_PASS no configurados')
        return False

    to_list = [to] if isinstance(to, str) else to
    cc_list = ([cc] if isinstance(cc, str) else cc) if cc else []

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f'{EMAIL_FROM_NAME} <{EMAIL_FROM}>'
    msg['To']      = ', '.join(to_list)
    if cc_list:
        msg['Cc']  = ', '.join(cc_list)

    if body_text:
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    if attachments:
        msg = _add_attachments(msg, attachments)

    recipients = to_list + cc_list
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        log.info(f'[mail] enviado: {subject} → {recipients}')
        return True
    except Exception as e:
        log.error(f'[mail] error enviando a {recipients}: {e}')
        return False


def _add_attachments(msg: MIMEMultipart, attachments: list) -> MIMEMultipart:
    outer = MIMEMultipart('mixed')
    for key, val in msg.items():
        outer[key] = val
    for part in msg.get_payload():
        outer.attach(part)
    for path in attachments:
        path = Path(path)
        if not path.exists():
            log.warning(f'[mail] adjunto no encontrado: {path}')
            continue
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=path.name)
        outer.attach(part)
    return outer


def send_report_email(
    to: str | list[str],
    campaign_name: str,
    period: str,
    total_detections: int,
    xlsx_path: str | Path | None = None,
) -> bool:
    """
    Email de reporte de campaña con tabla resumen y adjunto Excel opcional.
    """
    subject = f'Reporte de Campaña — {campaign_name} | {period}'
    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a3a5c; padding: 24px; border-radius: 8px 8px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 22px;">PubliAudit</h1>
            <p style="color: #aac4e0; margin: 4px 0 0;">Reporte de Monitoreo Publicitario</p>
        </div>
        <div style="background: #f9f9f9; padding: 28px; border: 1px solid #e0e0e0;">
            <h2 style="color: #1a3a5c; margin-top: 0;">{campaign_name}</h2>
            <p style="color: #555;">Período: <strong>{period}</strong></p>
            <div style="background: white; border: 1px solid #ddd; border-radius: 6px; padding: 20px; margin: 20px 0; text-align: center;">
                <div style="font-size: 48px; font-weight: bold; color: #1a3a5c;">{total_detections}</div>
                <div style="color: #888; font-size: 14px;">pautas detectadas</div>
            </div>
            <p style="color: #555;">{'El detalle completo se encuentra en el archivo Excel adjunto.' if xlsx_path else ''}</p>
        </div>
        <div style="background: #f0f0f0; padding: 14px; border-radius: 0 0 8px 8px; text-align: center;">
            <p style="color: #aaa; font-size: 12px; margin: 0;">
                PubliAudit · publiaudit.com · reports@publiaudit.com
            </p>
        </div>
    </div>
    """
    attachments = [xlsx_path] if xlsx_path else None
    return send_email(to, subject, body_html, attachments=attachments)
