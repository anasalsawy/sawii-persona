import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.embedder import Embedder  # noqa: E402
from sawii_persona.identity import create_persona, get_persona  # noqa: E402
from sawii_persona.memory.records import Memory  # noqa: E402
from sawii_persona.store import Store  # noqa: E402


class StoreCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        self.embedder = Embedder(None)
        self.memory = Memory(self.store, self.embedder)
        self.pid, self.key = await create_persona(self.store, name="Koko", definition="You are Koko.",
                                                  timezone="UTC")
        self.persona = await get_persona(self.store, self.pid)

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def turn(self, user, reply="ok"):
        tid = await self.memory.new_turn(self.pid, user)
        await self.memory.finish_turn(tid, reply)
        return tid
