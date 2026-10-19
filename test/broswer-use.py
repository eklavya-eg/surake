import asyncio
from dotenv import load_dotenv
from browser_use import Agent, ChatOpenRouter, Browser

load_dotenv()

browser = Browser.from_system_chrome()

async def main():
    llm = ChatOpenRouter(
        model="anthropic/claude-sonnet-4-6"
    )

    agent = Agent(
        task="Open https://example.com and extract its main heading.",
        llm=llm,
    )

    history = await agent.run(max_steps=10)
    print(history.final_result())

asyncio.run(main())