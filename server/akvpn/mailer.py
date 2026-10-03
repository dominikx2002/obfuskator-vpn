"""Wysylka maili przez msmtp (ta sama konfiguracja Gmaila, co Twoje skrypty)."""
import subprocess
from email.message import EmailMessage


class MailError(RuntimeError):
    pass


class Mailer:
    def __init__(self, cfg: dict):
        self.cmd = list(cfg["mail_cmd"])
        self.sender = cfg.get("mail_from", "")
        self.prefix = cfg.get("mail_subject_prefix", "")

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["To"] = to
        if self.sender:
            msg["From"] = self.sender
        msg["Subject"] = f"{self.prefix}: {subject}" if self.prefix else subject
        msg.set_content(body)  # UTF-8, polskie znaki w temacie i tresci
        try:
            r = subprocess.run(self.cmd, input=msg.as_bytes(), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise MailError(str(e))
        if r.returncode != 0:
            raise MailError((r.stderr or r.stdout).decode("utf-8", "replace").strip()[-300:])
