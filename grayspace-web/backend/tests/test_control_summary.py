"""The deployments list's summaries (control/api.py summary): stale ones are returned at once and
refreshed in the background, which tells the UI when they changed."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from control import api


def worker(status):
    return {"base": {"status": status, "version": {"name": "grayspace/2026.11"}}}


class StaleWhileRefreshing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.get = AsyncMock(return_value=worker("healthy"))
        self.publish = MagicMock()
        for p in (patch.object(api, "worker_get", self.get), patch.object(api, "publish", self.publish),
                  patch.object(api.supervisor, "running", return_value=True), patch.dict(api._summary_cache, clear=True),
                  patch.dict(api._refreshing, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def age(self, seconds):
        stamp, base, rest = api._summary_cache["d1"]
        api._summary_cache["d1"] = (stamp - seconds, base, rest)

    async def settle(self):
        await asyncio.gather(*api._refreshing.values())

    async def test_the_first_call_waits_and_then_it_is_cached(self):
        base, _ = await api.summary("d1")
        self.assertEqual(base.status, "healthy")
        await api.summary("d1")
        self.assertEqual(self.get.await_count, 1)

    async def test_a_stale_one_comes_back_at_once_and_the_change_is_published(self):
        await api.summary("d1")
        self.age(api.SUMMARY_TTL)
        self.get.return_value = worker("degraded")
        base, _ = await api.summary("d1")
        self.assertEqual(base.status, "healthy")
        await api.summary("d1")  # one refresh at a time
        await self.settle()
        self.assertEqual(self.get.await_count, 2)
        self.publish.assert_called_once_with("deployment.updated", "d1")
        self.assertEqual((await api.summary("d1"))[0].status, "degraded")

    async def test_an_unchanged_refresh_publishes_nothing(self):
        await api.summary("d1")
        self.age(api.SUMMARY_TTL)
        await api.summary("d1")
        await self.settle()
        self.assertEqual(self.get.await_count, 2)
        self.publish.assert_not_called()

    async def test_fresh_waits_for_the_worker(self):
        await api.summary("d1")
        self.get.return_value = worker("degraded")
        self.assertEqual((await api.summary("d1", fresh=True))[0].status, "degraded")


if __name__ == "__main__":
    unittest.main()
