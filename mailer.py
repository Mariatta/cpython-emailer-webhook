import asyncio
import email.message
import email.utils
import http
import http.client
import os
import re
import traceback
import sys

import aiohttp
import aiohttp.web
import aiosmtplib

from tasks import send_email_task

ALLOWED_BRANCHES_CRE = re.compile(r'^(\d\.\d+|main|master)$')
SENDER = os.environ.get("SENDER_EMAIL", "sender@example.com")
RECIPIENT = os.environ.get("RECIPIENT_EMAIL", "recipient@example.com")
SMTP_HOSTNAME = os.environ.get('SMTP_HOSTNAME', "localhost")
SMTP_PORT = int(os.environ.get('SMTP_PORT', 1025))
PORT = int(os.environ.get('PORT', 8585))

class ResponseExit(Exception):

    def __init__(self, status=None, text=None) -> None:
        super().__init__(text)
        self.response = aiohttp.web.Response(status=status.value, text=text)


def get_diff_stat(commit):
    files = {
        "A": commit["added"],
        "D": commit["removed"],
        "M": commit["modified"],
    }
    result = []
    for key, file_list in files.items():
        if file_list:
            result.append("\n".join(f"{key} {f}" for f in file_list))
    return "\n".join(result)


def build_message(commit, **kwargs):
    branch = kwargs.get("branch")
    diff_stat = kwargs.get("diff_stat")
    unified_diff = kwargs.get("unified_diff")
    template = f"""\
{commit["url"]}
commit: {commit["id"]}
branch: {branch}
author: {commit["author"]["name"]} <{commit["author"]["email"]}>
committer: {commit["committer"]["name"]} <{commit["committer"]["email"]}>
date: {commit["timestamp"]}
summary:

{commit["message"]}

files:
{diff_stat}

{unified_diff}
"""
    msg = email.message.EmailMessage()
    sender_name = commit['committer']['name']
    if sender_name == 'GitHub':
        # Show author's name as sender if committer info is 'GitHub'.
        sender_name = commit['author']['name']
    msg["From"] = email.utils.formataddr((sender_name, SENDER))
    msg["To"] = RECIPIENT
    msg["Subject"] = commit["message"].split("\n")[0]
    msg.set_content(template)
    return msg


async def fetch_diff(client, url):
    async with client.get(url) as response:
        if response.status >= 300:
            msg = f'unexpected response for {response.url!r}: {response.status}'
            raise http.client.HTTPException(msg)
        return (await response.text())


class PushEvent:

    def __init__(self, client, smtp, request):
        self.client = client
        self.smtp = smtp
        self.request = request

    async def process(self):
        if self.request.content_type != 'application/json':
            msg = f'can only accept application/json, not {self.request.content_type}'
            raise ResponseExit(status=http.HTTPStatus.UNSUPPORTED_MEDIA_TYPE, text=msg)
        payload = await self.request.json()
        if 'commits' not in payload or len(payload['commits']) == 0:
            raise ResponseExit(status=http.HTTPStatus.NO_CONTENT, text='There is no commit to be processed.')
        branch_name = payload['ref'].split('/').pop()
        if ALLOWED_BRANCHES_CRE.match(branch_name) is None:
            raise ResponseExit(status=http.HTTPStatus.NO_CONTENT, text='Invalid branch name.')
        # Since we use the 'squash and merge' button, there will
        # always be single commit.
        commit = payload['commits'][0]
        unified_diff = await fetch_diff(self.client, commit['url'] + '.diff')
        diff_stat = get_diff_stat(commit)
        message = build_message(commit, branch=branch_name, diff_stat=diff_stat,
                                unified_diff=unified_diff)
        send_email_task.delay(self.smtp, message)


def create_handler(create_client, smtp_client):
    async def handler(request):
        async with create_client() as client, smtp_client() as smtp:
            try:
                await PushEvent(client, smtp, request).process()
                return aiohttp.web.Response(status=http.HTTPStatus.OK)
            except ResponseExit as exc:
                return exc.response
            except Exception as exc:
                traceback.print_exception(
                    type(exc), exc, exc.__traceback__, file=sys.stderr
                )
                return aiohttp.web.Response(status=http.HTTPStatus.INTERNAL_SERVER_ERROR)
    return handler


def application(loop):
    app = aiohttp.web.Application(loop=loop)
    app.router.add_post('/', create_handler(
        lambda: aiohttp.ClientSession(loop=loop),
        # TODO: remove use_tls=False if we won't use starttls
        lambda: aiosmtplib.SMTP(hostname=SMTP_HOSTNAME, port=SMTP_PORT, loop=loop, use_tls=False),
    ))
    return app

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    app = application(loop)
    aiohttp.web.run_app(app, port=PORT)
