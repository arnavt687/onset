"""The caregiver link shortener (ONSET_LINK_SHORTENER): short when it works, the long link when it does not.

No test here touches a real shortener: a local server plays the service.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from onset import caregiver, shorten
from onset.caregiver import EscalationConfig, make_invite

KEY, SECRET = "APItestkey", "x" * 40
URL = "wss://example.livekit.cloud"
LONG = "https://x.test/onset/caregiver/#c/example.livekit.cloud/12/aaaa.bbbb.cc"


def fake_service(*, reply=None, location=None, status=301, delay=0.0, create_status=200):
    """A stand-in shortener: GET /create?url=... answers a short link, GET /<code> redirects."""
    seen = {}

    async def create(request):
        seen["url"] = request.query.get("url")
        seen["params"] = dict(request.query)
        if delay:
            await asyncio.sleep(delay)
        body = reply if reply is not None else f"http://{request.host}/Ab12cd"
        return web.Response(text=body, status=create_status)

    async def follow(request):
        return web.Response(status=status, headers={"Location": seen["url"] if location is None else location})

    app = web.Application()
    app.router.add_get("/create", create)
    app.router.add_get("/{code}", follow)
    return app, seen


async def run(app, **kw):
    server = TestServer(app)
    await server.start_server()
    base = f"http://{server.host}:{server.port}"
    svc = shorten.Service("fake.test", base + "/create", (("format", "simple"),), rf"http://{server.host}:{server.port}/[A-Za-z0-9]+")
    old = dict(shorten.SERVICES)
    shorten.SERVICES["fake"] = svc
    try:
        async with aiohttp.ClientSession() as session:
            return await shorten.shorten(LONG, "fake", session=session, **kw), base
    finally:
        shorten.SERVICES.clear()
        shorten.SERVICES.update(old)
        await server.close()


def test_a_short_link_that_leads_back_to_ours_is_used():
    app, seen = fake_service()
    short, base = asyncio.run(run(app))
    assert short == base + "/Ab12cd" and len(short) < len(LONG)
    assert seen["url"] == LONG, "the whole link, #fragment included, must reach the service"
    assert seen["params"]["format"] == "simple"


@pytest.mark.parametrize("kw", [
    dict(location=LONG.split("#")[0]),                       # the service dropped the #fragment (the keys)
    dict(location="https://elsewhere.test/redirect?url=x"),  # the service sends people through another page first
    dict(status=200),                                        # a preview page instead of a redirect
    dict(reply="Error: please try again later"),             # not a link at all
    dict(reply="https://evil.test/Ab12cd"),                  # a link on some other site
    dict(create_status=502),
])
def test_anything_odd_means_the_long_link(kw):
    app, _ = fake_service(**kw)
    short, _ = asyncio.run(run(app))
    assert short is None


def test_a_slow_service_is_not_waited_for():
    app, _ = fake_service(delay=5.0)
    loop_start = asyncio.new_event_loop()
    try:
        t0 = loop_start.time()
        short, _ = loop_start.run_until_complete(run(app, timeout_s=0.3))
        assert short is None and loop_start.time() - t0 < 2.0
    finally:
        loop_start.close()


def test_never_raises_and_never_shortens_what_is_not_https():
    assert asyncio.run(shorten.shorten(LONG, "nope")) is None
    assert asyncio.run(shorten.shorten("http://x.test/#c/h/1/a", "isgd")) is None

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("no network")

    assert asyncio.run(shorten.shorten(LONG, "isgd", session=Broken())) is None


def test_the_links_are_never_logged(caplog):
    app, _ = fake_service(location="https://elsewhere.test/")
    with caplog.at_level("DEBUG"):
        asyncio.run(run(app))
        app2, _ = fake_service()
        asyncio.run(run(app2))
    # only Onset's own lines: the stand-in server's access log is not ours
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("onset"))
    assert "aaaa.bbbb.cc" not in text and "Ab12cd" not in text and "x.test" not in text
    assert "fake.test" in text and "71 -> 29 characters" in text


def test_the_option(caplog):
    f = shorten.service_from
    assert [f(v) for v in (None, "", "off", "0", "no", "OFF ")] == ["off"] * 6
    assert [f(v) for v in ("isgd", "is.gd", "IS.GD", "1", "on", "dagd", "da.gd")] == ["isgd"] * 5 + ["dagd"] * 2
    assert f("bitly") == "off" and "ONSET_LINK_SHORTENER" in caplog.text
    assert f("tinyurl") == "off", "left out: it stored the link with %23 in place of #"
    assert EscalationConfig.from_env({}).shortener == "off", "off unless asked for"
    assert EscalationConfig.from_env({"ONSET_LINK_SHORTENER": "is.gd"}).shortener == "isgd"


def invite():
    return make_invite("room-1", "call-abc", livekit_url=URL, api_key=KEY, api_secret=SECRET,
                       cfg=EscalationConfig(page="https://x.test/onset/caregiver/"), now=1000)


def test_the_invite_keeps_everything_but_the_link():
    inv = invite()
    calls = []

    async def fake(link, service):
        calls.append((link, service))
        return "https://is.gd/Ab12cd"

    out = asyncio.run(caregiver.with_short_link(inv, EscalationConfig(shortener="isgd"), shorten=fake))
    assert out.link == "https://is.gd/Ab12cd" and calls == [(inv.link, "isgd")]
    assert replace(out, link=inv.link) == inv, "keys, rooms and expiry are untouched"
    assert out.talk_token and out.talk_token not in out.link
    assert "is.gd" not in repr(out), "the link is never in the invite's repr"


def test_off_or_failing_means_the_same_invite():
    inv = invite()

    async def never(link, service):
        raise AssertionError("off must not call the shortener")

    assert asyncio.run(caregiver.with_short_link(inv, EscalationConfig(), shorten=never)) is inv

    async def none(link, service):
        return None

    async def boom(link, service):
        raise RuntimeError("down")

    for fn in (none, boom):
        assert asyncio.run(caregiver.with_short_link(inv, EscalationConfig(shortener="isgd"), shorten=fn)) is inv


def test_the_text_carries_the_short_link():
    inv = replace(invite(), link="https://is.gd/Ab12cd")
    text = caregiver.invite_text("Onset check-in for Alex.", inv.link, caregiver_name="Sam Lee", patient_name="Alex Smith", talk=True)
    assert text.endswith("\n\nhttps://is.gd/Ab12cd") and len(text) < 300
