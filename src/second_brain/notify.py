import logging
import urllib.request

log = logging.getLogger("second_brain")


def notify(url: str, title: str, message: str) -> bool:
    """POST a plain-text message (ntfy.sh style). Never raises: a failed alert must not hide the failure."""
    if not url:
        return False
    request = urllib.request.Request(
        url, data=message.encode("utf-8"), headers={"Title": title}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except OSError as exc:
        log.warning("Notify failed: %s", exc)
    return False
