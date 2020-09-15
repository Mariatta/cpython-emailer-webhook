import asyncio
import os

import cachetools
import celery

app = celery.Celery("send_cpython_email")

app.conf.update(
    BROKER_URL=os.environ["REDIS_URL"], CELERY_RESULT_BACKEND=os.environ["REDIS_URL"]
)

cache = cachetools.LRUCache(maxsize=500)

SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")


@app.task()
def send_email_task(smtp, message):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_email(smtp, message))


async def send_email(smtp, message):
    async with smtp as server:
        await server.connect()
        # Call ehlo() as a workaround for cole/aiosmtplib/#13.
        await server.ehlo()
        if SMTP_USERNAME is not None and SMTP_PASSWORD is not None:
            await server.login(SMTP_USERNAME, SMTP_PASSWORD)
        return await server.send_message(message)
