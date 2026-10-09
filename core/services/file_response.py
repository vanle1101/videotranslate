"""Keep real file/range serving, but release transfers on client disconnect."""
import asyncio

from starlette.responses import FileResponse as StarletteFileResponse


class FileResponse(StarletteFileResponse):
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await super().__call__(scope, receive, send)

        async def disconnected():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        # Starlette FileResponse does not observe receive while sending ranges.
        # Uvicorn can stop writing after a disconnect while that response keeps
        # reading the whole source. Own its task separately so one cancellation
        # unwinds the real file context before the HTTP owner is released.
        transfer = asyncio.create_task(super().__call__(scope, receive, send))
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((transfer, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if transfer in done:
                return await transfer
            await disconnect  # Do not turn an unexpected receive failure into success.
            if not transfer.done():
                transfer.cancel()
                try:
                    await transfer
                except asyncio.CancelledError:
                    return
            return await transfer
        finally:
            for task in (transfer, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(transfer, disconnect, return_exceptions=True)
