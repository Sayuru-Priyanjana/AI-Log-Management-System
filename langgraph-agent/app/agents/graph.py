from typing import Annotated, Sequence, TypedDict, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
import json
import sqlite3

class GraphState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    investigation_id: str

def build_graph(llm_provider, tools):
    # This is a stub that will be expanded.
    workflow = StateGraph(GraphState)
    
    # We will define the real nodes here.
    async def call_model(state: GraphState):
        return {"messages": [AIMessage(content="I am the LangGraph Agent.")]}
        
    workflow.add_node("agent", call_model)
    workflow.add_edge(START, "agent")
    workflow.add_edge("agent", END)
    
    conn = sqlite3.connect("langgraph_checkpoints.sqlite", check_same_thread=False)
    memory = SqliteSaver(conn)
    return workflow.compile(checkpointer=memory)
