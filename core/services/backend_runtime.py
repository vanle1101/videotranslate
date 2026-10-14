"""Own the local desktop HTTP loop without changing provider loop policies."""
import asyncio
import sys


def create_backend_loop():
    # Python's Windows Proactor socket shutdown can raise after notifying the
    # protocol, leaving the server attachment unreleased on a peer reset. The
    # local HTTP/WebSocket server uses selector sockets; preparation subprocesses
    # are run by the existing cancellable media worker instead of this loop.
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


async def _serve_and_drain(server):
    try:
        await server.serve()
    finally:
        # Graceful shutdown can expire with unread response bytes still queued.
        # transport.close() waits to flush those bytes; a client that stopped
        # reading can retain its socket after the event loop closes. At this
        # point Uvicorn has ended graceful service, so abort only its remaining
        # transports and let response owners close their file contexts before
        # Runner cancels every child task (including native-open awaits).
        state = getattr(server, "server_state", None)
        if state is not None:
            for connection in tuple(state.connections):
                connection.transport.abort()
            tasks = tuple(state.tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # Deliver connection_lost callbacks before closing the local loop.
            await asyncio.sleep(0)


def run_backend_server(server):
    """Serve on a thread-owned loop and drain its async/native workers on exit.

    This explicit runner also supports Python 3.10. It never changes the global
    event-loop policy used by Edge-TTS's separate ``asyncio.run`` calls.
    """
    runner_type = getattr(asyncio, "Runner", None)
    if runner_type is not None:
        # Keep the standard runner's task/generator/executor teardown bounds
        # on current Python; only supply this backend's local loop factory.
        with runner_type(loop_factory=create_backend_loop) as runner:
            runner.run(_serve_and_drain(server))
        return
    loop = create_backend_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve_and_drain(server))
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                for task in pending:
                    if not task.cancelled() and task.exception() is not None:
                        loop.call_exception_handler({
                            "message": "Unhandled exception during desktop backend shutdown",
                            "exception": task.exception(), "task": task,
                        })
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
