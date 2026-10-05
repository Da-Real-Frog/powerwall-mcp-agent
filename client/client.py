import os
import json
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

async def run_agent():
    # Fetch the server URL from the .env file
    server_url = os.getenv("MCP_SERVER_URL")
    
    if not server_url:
        raise ValueError("❌ MCP_SERVER_URL is not defined in the .env file.")

    print(f"🔌 Connecting to remote MCP Server at {server_url}...")
    
    # Connect over the network using SSE
    async with sse_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ Remote MCP Session initialized.\n")
            
            # 1. Discover tools dynamically from the MCP server
            mcp_tools = await session.list_tools()
            gemini_function_declarations = []
            
            for tool in mcp_tools.tools:
                # Clean the schema to meet Gemini's strict OpenAPI requirements
                schema = tool.input_schema.copy() if tool.input_schema else {}
                schema.pop("additionalProperties", None)
                schema.pop("additional_properties", None)
                
                gemini_function_declarations.append(
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters=schema
                    )
                )
            
            agent_tools = types.Tool(function_declarations=gemini_function_declarations)
            
            # 2. Initialize Gemini client
            client = genai.Client() 
            chat = client.chats.create(
                model="gemini-3.8-flash",
                config=types.GenerateContentConfig(
                    tools=[agent_tools],
                    temperature=0.2, 
                )
            )
            
            prompt = (
                "You are my home energy manager. Evaluate my power situation. "
                "Check my current battery level, then check the solar forecast across my 3 arrays. "
                "Synthesize this data and tell me if I need to charge my Powerwalls from the grid tonight."
            )
            print(f"👤 User: {prompt}\n")
            
            # 3. Agent Execution Loop
            response = chat.send_message(prompt)
            
            while response.function_calls:
                for call in response.function_calls:
                    print(f"🤖 Agent is executing tool: [{call.name}]")
                    
                    # Execute the requested tool via the MCP session
                    result = await session.call_tool(call.name, call.args)
                    
                    # Extract the JSON string from the MCP TextContent block
                    tool_output_string = result.content[0].text
                    
                    # Feed the raw telemetry back to the LLM
                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=call.name,
                            response={"result": json.loads(tool_output_string)}
                        )
                    )
            
            print(f"\n🧠 Agent Final Report:\n{response.text}")

if __name__ == "__main__":
    asyncio.run(run_agent())
