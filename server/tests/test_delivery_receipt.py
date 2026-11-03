"""A signed 200 alone must not stop delivery retries without DKU OJ's receipt."""
from datetime import timedelta

import httpx
import pytest

from app.timeutil import utcnow
from .test_dkuoj_bridge import db, fake, oj  # noqa: F401
from .test_outbox_locking_resend import deliver, handed_in, row


@pytest.mark.parametrize("response", [
    {}, {"receipt": None}, {"receipt": ""}, {"receipt": "   "}, {"receipt": 42},
    [], "not-json",
])
def test_unconfirmed_success_retries_identical_hand_in(oj, fake, response):
    _, aid = handed_in(oj, fake)
    original = fake.handle
    sent = []

    def handle(req):
        result = original(req)
        if req.url.path.endswith("/submissions"):
            sent.append(req.content)
            if len(sent) == 1:
                # DKU OJ may have already filed it. Its idempotent endpoint must
                # receive the SAME attempt and bytes when confirmation is retried.
                return (httpx.Response(200, text=response) if isinstance(response, str)
                        else httpx.Response(200, json=response))
        return result

    fake.handle = handle
    now = utcnow() + timedelta(seconds=1)
    assert deliver(oj, now) == {"sent": 0, "failed": 1, "dead": 0}
    first = row(oj, aid)
    assert first["status"] == "failed" and first["last_status"] == 200
    assert "receipt" in first["last_error"].lower()
    assert aid in fake.submissions
    assert deliver(oj, now + timedelta(seconds=10)) == {"sent": 0, "failed": 0, "dead": 0}
    assert len(sent) == 1
    assert deliver(oj, now + timedelta(minutes=1)) == {"sent": 1, "failed": 0, "dead": 0}
    assert len(sent) == 2 and sent[0] == sent[1]
    final = row(oj, aid)
    assert final["status"] == "sent" and final["last_error"] is None
    assert final["payload"] == first["payload"]
    assert deliver(oj, now + timedelta(minutes=2)) == {"sent": 0, "failed": 0, "dead": 0}
