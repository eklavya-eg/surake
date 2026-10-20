import uuid
from typing import TypedDict
from browser_use import ChatOpenAI
from langgraph.graph import StateGraph, Start, End, Node
from langchain_openrouter import ChatOpenRouter
from langchain_typesafe import TypeSafeClassifier
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from typing import Annotated
from langchain_core.messages import AnyMessage, SystemMessage
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    project_map: str
    tasks: dict[str, dict]
    

class Agent:
    def __init__(self, task, llm):
        self.llm = None
        self.decision_model = None
        self.agent = None
        self.init_llm()
        self.init_decision_model()
        self.init_agent()
        self.threads = []

    async def ainvoke(self):
        pass  # Placeholder for the asynchronous invocation logic of the agent

    def init_decision_model(self, model_name:str="gpt-5-mini", temperature:float=0):
        self.decision_model = TypeSafeClassifier(
            model="jev-1.13",
        )

    async def call_decision_model(self, state:dict|str, questions:dict|None=None):
        if self.decision_model is None:
            self.init_decision_model()
        response = await self.decision_model.ainvoke({
            "state": {
                "user_request": "Find suitable",
                "project_summary": "Building a competitor analysis report",
                "active_tasks": [
                    {"id": "T1", "description": "Research competitors", "status": "in_progress"}
                ],
                "available_agents": ["browser", "general"],
            },
            "questions": {
                "task_type": {
                    "type": "choice",
                    "options": ["browser", "general", "new_task"],
                    "instructions": "Choose the appropriate action."
                }
            }
        })
        return response
    
    async def find_thread(self, query:str):
        for thread in self.threads:
            response = await self.call_decision_model(f"Thread ID: {thread}\nQuery: {query}\nIs this query related to the thread? Answer with 'yes' or 'no'.")
            if "yes" in response.lower():
                return thread
        return None

    def init_llm(self, model_name:str="gpt-5-mini", temperature:float=0):
        self.init_openrouter_llm(model_name=model_name, temperature=temperature)

    def init_openai_llm(self, model_name:str="gpt-5-mini", temperature:float=0):
        self.llm = ChatOpenAI(
            model=model_name,
            temperature=temperature
        )

    def init_openrouter_llm(self, model_name:str, temperature:float=0):
        self.llm = ChatOpenRouter(
            model=model_name,
            temperature=temperature,
        )

    def init_agent(self):
        self.agent = self.create_agent()

    async def call_llm(self, query:str):
        if self.llm is None:
            self.init_llm()
        response = await self.llm.agenerate([query])
        return response.generations[0][0].text

    def add_thread(self):
        thread = uuid.uuid4()
        self.threads.append(thread)
        return thread



    def create_agent(self):
        graph = StateGraph(AgentState, checkpointer=InMemorySaver())
        start_node = Start("start")
        end_node = End("end")
        graph.add_node(start_node)
        graph.add_node()
        graph.add_node(end_node)

        # Add your custom nodes and transitions here
        # For example:
        # node1 = Node("node1", self.llm)
        # graph.add_node(node1)
        # graph.add_edge(start_node, node1)
        # graph.add_edge(node1, end_node)
        with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
            agent = graph.compile(checkpointer=checkpointer)

        return graph



    
    async def run(self, query, max_steps=10):
        self.agent.ainvoke()