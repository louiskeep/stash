import asyncio


async def test_asyncio_mode_runs_async_tests():
    await asyncio.sleep(0)
    assert True
