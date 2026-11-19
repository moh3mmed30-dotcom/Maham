import pytest
from app.ledger import record_transaction

class FakeDB:
    def __init__(self): self.added=[]
    async def execute(self, stmt):
        class R:
            def scalar_one_or_none(self): return None
        return R()
    def add(self, obj): self.added.append(obj)
    async def flush(self): pass

@pytest.mark.asyncio
async def test_unbalanced_ledger_rejected():
    db=FakeDB()
    with pytest.raises(ValueError):
        await record_transaction(db,"k","r","bad",[("a",100,0),("b",0,90)])
