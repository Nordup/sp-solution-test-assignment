import asyncio, tempfile, json
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command, RetryPolicy
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

class State(TypedDict, total=False):
    approved: bool
    result: str

async def main():
    calls = {'approval_entries': 0, 'effects': 0, 'read_attempts': 0}
    def approve(state):
        calls['approval_entries'] += 1
        return {'approved': interrupt({'action': 'synthetic_effect'})}
    def effect(state):
        calls['effects'] += 1
        return {'result': 'executed'}
    def reject(state):
        return {'result': 'denied'}
    def build(saver):
        b=StateGraph(State)
        b.add_node('approval', approve)
        b.add_node('effect', effect)
        b.add_node('reject', reject)
        b.add_edge(START,'approval')
        b.add_conditional_edges('approval',lambda s:'effect' if s['approved'] else 'reject')
        b.add_edge('effect',END); b.add_edge('reject',END)
        return b.compile(checkpointer=saver)
    with tempfile.TemporaryDirectory() as d:
        db=str(Path(d)/'state.sqlite')
        cfg={'configurable':{'thread_id':'approve'}}
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            result=await build(saver).ainvoke({},cfg,durability='sync')
            assert result['__interrupt__'] and calls['effects']==0
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            result=await build(saver).ainvoke(Command(resume=True),cfg,durability='sync')
            assert result['result']=='executed' and calls['effects']==1
            denied={'configurable':{'thread_id':'deny'}}
            graph=build(saver)
            await graph.ainvoke({},denied,durability='sync')
            result=await graph.ainvoke(Command(resume=False),denied,durability='sync')
            assert result['result']=='denied' and calls['effects']==1
        def transient_read(state):
            calls['read_attempts']+=1
            if calls['read_attempts']<3: raise ConnectionError('synthetic transient')
            return {'result':'recovered'}
        b=StateGraph(State)
        b.add_node('read',transient_read,retry_policy=RetryPolicy(max_attempts=3,initial_interval=.01,jitter=False,retry_on=ConnectionError))
        b.add_edge(START,'read');b.add_edge('read',END)
        assert (await b.compile().ainvoke({}))['result']=='recovered'
    assert calls=={'approval_entries':4,'effects':1,'read_attempts':3}
    print(json.dumps({'sqlite_reopen_resume':True,'denial_prevents_effect':True,'approval_node_reentered':True,'bounded_retry_verified':True,'calls':calls}))
asyncio.run(main())
