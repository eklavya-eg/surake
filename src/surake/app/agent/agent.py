from browser_use import ChatOpenAI
from langgraph.graph import StateGraph, Start, End, Node
from langchain_openrouter import ChatOpenRouter

class Agent:
    def __init__(self, task, llm):
        self.llm = None
        self.agent = None
        self.init_llm()
        self.init_agent()

    def init_llm(self):
        self.llm = ChatOpenRouter(
            model="openai/gpt-4.1-mini",
            temperature=0,
        )

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

    def create_agent(self):
        pass



    
    async def run(self, query, max_steps=10):
        self.agent.ainvoke()