"""Keep real file/range serving, but release transfers on client disconnect."""
import asyncio

from starlette.responses import FileResponse as StarletteFileResponse


class _TransferDisconnected(Exception):
    pass


class FileResponse(StarletteFileResponse):
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await super().__call__(scope, receive, send)

        async def disconnected():
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        interrupted = False
        async def guarded_send(message):
            if interrupted:
                raise _TransferDisconnected
            await send(message)

        async def transmit():
            try:
                await super(FileResponse, self).__call__(scope, receive, guarded_send)
            except _TransferDisconnected:
                return

        # Do not cancel the file-open/read thread's await: a native open may
        # return a live handle after its asyncio future has been discarded.
        # Stop at the next send boundary, letting Starlette close its context.
        transfer = asyncio.create_task(transmit())
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((transfer, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if transfer in done:
                return await asyncio.shield(transfer)
            await disconnect  # Do not turn an unexpected receive failure into success.
            interrupted = True
            return await asyncio.shield(transfer)
        finally:
            interrupted = True
            if not disconnect.done():
                disconnect.cancel()
            cleanup = asyncio.gather(transfer, disconnect, return_exceptions=True)
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
